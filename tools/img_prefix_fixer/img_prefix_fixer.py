#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IMG Prefix Fixer
----------------
Reads the texture names inside the TXD files of GTA III / Vice City / San Andreas
IMG archives. For every texture "tex" that has NO exact image in the images folder
(tex.png) but does have prefixed images (<anything>_tex.png, e.g.
player_props_tex.png or dyn_objects_tex.png), the tool copies one of those images
into the output folder under the clean name  tex.png  (prefix removed).

The prefix is always cut from the START of the file name by matching the texture
name from the END of the file name, so  player_props_CJ_bottle3.png  becomes
CJ_bottle3.png  (the texture name comes from the TXD, never guessed).

Source images are never modified - only copies are written to the output folder.

Requirements: Python 3.8+ (tkinter ships with the normal Windows installer).
Run:  python img_prefix_fixer.py
"""

import os
import sys
import time
import queue
import shutil
import struct
import hashlib
import threading
import subprocess

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:  # headless environments (tests)
    tk = None

SECTOR = 2048
RW_STRUCT = 0x01
RW_STRING = 0x02
RW_TEXNATIVE = 0x15
RW_TEXDICT = 0x16
PLATFORM_PS2 = 0x00325350  # 'PS2\0'
PLATFORMS_STRUCT_NAME = (5, 8, 9)  # Xbox, D3D8, D3D9 -> name stored inside struct
BAD_FILENAME_CHARS = '<>:"/\\|?*'


# ----------------------------------------------------------------------------
# IMG parsing
# ----------------------------------------------------------------------------
def _clean_name(raw):
    return raw.split(b"\x00", 1)[0].decode("latin-1").strip()


def _find_sibling(path, new_ext):
    base = os.path.splitext(os.path.basename(path))[0].lower()
    folder = os.path.dirname(path) or "."
    try:
        for n in os.listdir(folder):
            if n.lower() == base + new_ext:
                return os.path.join(folder, n)
    except OSError:
        pass
    return None


def read_img_entries(img_path):
    """Return list of (name, offset_bytes, size_bytes).

    Supports VER2 (GTA SA, single .img) and VER1 (GTA III / VC, .img + .dir).
    """
    with open(img_path, "rb") as f:
        magic = f.read(4)
        if magic == b"VER2":
            count = struct.unpack("<I", f.read(4))[0]
            table = f.read(count * 32)
            entries = []
            for i in range(len(table) // 32):
                off, s1, s2, nm = struct.unpack_from("<IHH24s", table, i * 32)
                entries.append((_clean_name(nm), off * SECTOR, (s1 or s2) * SECTOR))
            return entries

    dir_path = _find_sibling(img_path, ".dir")
    if not dir_path:
        raise ValueError(
            "Not a VER2 archive and no matching .dir file was found next to it "
            "(GTA III / Vice City archives need the .dir file)."
        )
    with open(dir_path, "rb") as d:
        table = d.read()
    entries = []
    for i in range(len(table) // 32):
        off, size, nm = struct.unpack_from("<II24s", table, i * 32)
        entries.append((_clean_name(nm), off * SECTOR, size * SECTOR))
    return entries


# ----------------------------------------------------------------------------
# TXD parsing (RenderWare) - reads headers only, skips pixel data
# ----------------------------------------------------------------------------
def _hdr(f):
    d = f.read(12)
    if len(d) < 12:
        return None
    return struct.unpack("<III", d)  # type, size, version


def _name_from_struct(f, data_start):
    f.seek(data_start + 8)
    return _clean_name(f.read(32))


def _name_from_string_chunk(f, struct_start, struct_size):
    f.seek(struct_start + struct_size)
    h = _hdr(f)
    if h and h[0] == RW_STRING:
        return _clean_name(f.read(min(h[1], 64)))
    return ""


def _texture_name(f, start):
    f.seek(start)
    h = _hdr(f)
    if h is None or h[0] != RW_STRUCT:
        return ""
    data_start = start + 12
    raw = f.read(4)
    if len(raw) < 4:
        return ""
    platform = struct.unpack("<I", raw)[0]

    if platform in PLATFORMS_STRUCT_NAME:
        name = _name_from_struct(f, data_start)
        if not name:
            name = _name_from_string_chunk(f, data_start, h[1])
    else:  # PS2 and anything unknown: name lives in a String chunk
        name = _name_from_string_chunk(f, data_start, h[1])
        if not name:
            name = _name_from_struct(f, data_start)
    return name


def read_txd_texture_names(f, offset, file_size):
    """Return list of texture names inside the TXD at `offset`, or None if the
    data there is not a valid texture dictionary."""
    f.seek(offset)
    h = _hdr(f)
    if h is None or h[0] != RW_TEXDICT:
        return None
    end = min(offset + 12 + h[1], file_size)
    pos = offset + 12
    names = []
    while pos + 12 <= end:
        f.seek(pos)
        ch = _hdr(f)
        if ch is None:
            break
        ctype, csize, _ = ch
        data_start = pos + 12
        if ctype == RW_TEXNATIVE:
            n = _texture_name(f, data_start)
            if n:
                names.append(n)
        pos = data_start + csize
    return names


# ----------------------------------------------------------------------------
# Core logic
# ----------------------------------------------------------------------------
def _same_dir(a, b):
    na = os.path.normcase(os.path.realpath(a))
    nb = os.path.normcase(os.path.realpath(b))
    return na == nb


def _valid_name(name):
    return bool(name) and name not in (".", "..") and not any(c in name for c in BAD_FILENAME_CHARS)


def _sort_key(path):
    return (path.lower(), path)


def build_index(folder, exts, recursive, skip_dir=None):
    """Index the images folder.

    Returns (exact, tails):
      exact: set of lower-case file names without extension
      tails: dict  lower-case tail -> list of (path, prefix_lower)
             For "player_props_CJ_bottle3.png" the tails are
               "props_cj_bottle3" (prefix "player")
               "cj_bottle3"       (prefix "player_props")
               "bottle3"          (prefix "player_props_cj")
             i.e. the name is always split from the START and the remaining
             END is what gets compared with the texture name from the TXD.
    """
    skip = os.path.normcase(os.path.abspath(skip_dir)) if skip_dir else None
    exact, tails = set(), {}

    def add(path):
        base, ext = os.path.splitext(os.path.basename(path))
        if ext.lower().lstrip(".") not in exts:
            return
        low = base.lower()
        exact.add(low)
        i = low.find("_")
        while i != -1:
            tail = low[i + 1:]
            if tail:
                tails.setdefault(tail, []).append((path, low[:i]))
            i = low.find("_", i + 1)

    if recursive:
        for root, dirs, files in os.walk(folder):
            if skip:
                dirs[:] = [d for d in dirs
                           if os.path.normcase(os.path.abspath(os.path.join(root, d))) != skip]
            for fn in files:
                add(os.path.join(root, fn))
    else:
        for fn in os.listdir(folder):
            p = os.path.join(folder, fn)
            if os.path.isfile(p):
                add(p)
    return exact, tails


def collect_textures(img_paths, log, progress):
    """Read every TXD of every IMG.

    Returns (textures, bad):
      textures: dict lower-case name -> {"name": original case, "txds": [txd file names]}
      bad: list of "img: txd" strings for TXD entries that could not be read
    """
    jobs, total = [], 0
    for p in img_paths:
        try:
            entries = read_img_entries(p)
        except Exception as e:
            log(f"[ERROR] {os.path.basename(p)}: {e}")
            continue
        txds = [e for e in entries if e[0].lower().endswith(".txd")]
        log(f"{os.path.basename(p)}: {len(entries)} entries, {len(txds)} TXD")
        jobs.append((p, txds))
        total += len(txds)

    textures, bad = {}, []
    done = 0
    progress(0, total)
    for img_path, txds in jobs:
        try:
            f = open(img_path, "rb")
        except OSError as e:
            log(f"[ERROR] cannot open {img_path}: {e}")
            continue
        with f:
            file_size = os.fstat(f.fileno()).st_size
            for name, off, _size in txds:
                done += 1
                if done % 20 == 0 or done == total:
                    progress(done, total)
                try:
                    texs = read_txd_texture_names(f, off, file_size)
                except Exception:
                    texs = None
                if texs is None:
                    bad.append(f"{os.path.basename(img_path)}: {name}")
                    continue
                for tex in texs:
                    low = tex.lower()
                    rec = textures.get(low)
                    if rec is None:
                        textures[low] = {"name": tex, "txds": [name]}
                    elif name not in rec["txds"]:
                        rec["txds"].append(name)
    progress(total, total)
    return textures, bad


def _same_content(paths):
    """True if all files are byte-identical, False if not, None if unreadable."""
    try:
        if len({os.path.getsize(p) for p in paths}) > 1:
            return False
        digests = set()
        for p in paths:
            h = hashlib.md5()
            with open(p, "rb") as fh:
                for blk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(blk)
            digests.add(h.hexdigest())
        return len(digests) == 1
    except OSError:
        return None


def write_report(path, folder, out_dir, exts, stats, copied, conflicts, notfound,
                 invalid, bad, errors, elapsed):
    ext0 = sorted(exts)[0]
    rel = lambda p: os.path.relpath(p, folder) if _inside(p, folder) else p
    L = []
    L.append("IMG PREFIX FIX REPORT")
    L.append("=" * 60)
    L.append("Date         : " + time.strftime("%Y-%m-%d %H:%M:%S"))
    L.append("Images folder: " + folder)
    L.append("Output folder: " + out_dir)
    L.append("Extensions   : " + ", ".join(sorted(exts)))
    L.append("")
    L.append("Summary")
    L.append("-" * 60)
    L.append(f"Unique textures in the TXD files     : {stats['textures']}")
    L.append(f"  already have an exact image        : {stats['exact']}")
    L.append(f"  copied to output (prefix removed)  : {stats['copied']}")
    L.append(f"  skipped (already in output folder) : {stats['existed']}")
    L.append(f"  NOT FOUND (no exact, no prefixed)  : {stats['notfound']}")
    L.append(f"  invalid file names skipped         : {stats['invalid']}")
    L.append(f"  copy errors                        : {stats['errors']}")
    L.append(f"Textures with several prefixed files : {stats['multi']} "
             f"({stats['multi_diff']} with DIFFERENT content)")
    L.append(f"TXD files unreadable                 : {stats['bad']}")
    L.append(f"Time                                 : {elapsed:.1f}s")
    L.append("")

    if conflicts:
        L.append("=" * 60)
        L.append("SEVERAL PREFIXED FILES FOR ONE TEXTURE (one was copied)")
        L.append("=" * 60)
        for name, src, others, same, by_txd in conflicts:
            L.append(f"{name}.{ext0}")
            L.append(f"    used   : {rel(src)}" + ("   (prefix = TXD name)" if by_txd else ""))
            for o in others:
                L.append(f"    other  : {rel(o)}")
            if same is True:
                L.append("    content: identical")
            elif same is False:
                L.append("    content: DIFFERENT - check which one you want")
            else:
                L.append("    content: could not compare")
            L.append("")

    if notfound:
        L.append("=" * 60)
        L.append("NOT FOUND (no exact file and no <prefix>_name file)")
        L.append("=" * 60)
        for name, txds in notfound:
            shown = ", ".join(txds[:5]) + (f", ... (+{len(txds) - 5})" if len(txds) > 5 else "")
            L.append(f"{name}.{ext0}    in: {shown}")
        L.append("")

    if copied:
        L.append("=" * 60)
        L.append("COPIED")
        L.append("=" * 60)
        for dest_name, src in copied:
            L.append(f"{dest_name}    <-  {rel(src)}")
        L.append("")

    if invalid:
        L.append("Invalid texture names (cannot be used as file names):")
        for n in invalid:
            L.append(f"    ! {n}")
        L.append("")
    if errors:
        L.append("Copy errors:")
        for e in errors:
            L.append(f"    ! {e}")
        L.append("")
    if bad:
        L.append("Unreadable / invalid TXD files:")
        for b in bad:
            L.append(f"    ! {b}")
        L.append("")

    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        out.write("\n".join(L) + "\n")


def _inside(path, folder):
    try:
        p = os.path.normcase(os.path.abspath(path))
        f = os.path.normcase(os.path.abspath(folder))
        return os.path.commonpath([p, f]) == f
    except ValueError:
        return False


def run_fix(folder, img_paths, out_dir, exts, recursive, overwrite, report_path,
            log=print, progress=lambda d, t: None):
    started = time.time()
    if _same_dir(folder, out_dir):
        raise ValueError("The output folder must be different from the images folder.")

    log("Reading IMG archives ...")
    textures, bad = collect_textures(img_paths, log, progress)
    log(f"  {len(textures)} unique texture name(s) in the TXD files")

    log(f"Indexing images in: {folder}")
    exact, tails = build_index(folder, exts, recursive, skip_dir=out_dir)
    log(f"  {len(exact)} image file(s) found ({', '.join(sorted(exts))})")

    stats = dict(textures=len(textures), exact=0, copied=0, existed=0, notfound=0,
                 invalid=0, errors=0, multi=0, multi_diff=0, bad=len(bad))
    copied, conflicts, notfound, invalid, errors = [], [], [], [], []

    items = sorted(textures.items(), key=lambda kv: kv[0])
    n = len(items)
    log("Copying prefixed images without their prefix ...")
    progress(0, n)
    os.makedirs(out_dir, exist_ok=True)

    for i, (low, rec) in enumerate(items, 1):
        if i % 50 == 0 or i == n:
            progress(i, n)
        name = rec["name"]
        if not _valid_name(name):
            invalid.append(name)
            stats["invalid"] += 1
            continue
        if low in exact:
            stats["exact"] += 1
            continue
        cands = tails.get(low)
        if not cands:
            notfound.append((name, rec["txds"]))
            stats["notfound"] += 1
            continue

        # Prefer a file whose whole prefix equals the name of a TXD that contains
        # this texture; otherwise take the first one in alphabetical order.
        txd_names = {os.path.splitext(t)[0].lower() for t in rec["txds"]}
        preferred = [c for c in cands if c[1] in txd_names]
        pool = preferred or cands
        src = sorted((c[0] for c in pool), key=_sort_key)[0]

        others = sorted((c[0] for c in cands if c[0] != src), key=_sort_key)
        if others:
            same = _same_content([src] + others)
            stats["multi"] += 1
            if same is False:
                stats["multi_diff"] += 1
            conflicts.append((name, src, others, same, bool(preferred)))

        dest = os.path.join(out_dir, name + os.path.splitext(src)[1].lower())
        if os.path.exists(dest) and not overwrite:
            stats["existed"] += 1
            continue
        try:
            shutil.copy2(src, dest)
            stats["copied"] += 1
            copied.append((os.path.basename(dest), src))
        except OSError as e:
            stats["errors"] += 1
            errors.append(f"{os.path.basename(dest)}: {e}")

    progress(n, n)
    write_report(report_path, folder, out_dir, exts, stats, copied, conflicts,
                 notfound, invalid, bad, errors, time.time() - started)
    return stats


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
def _open_path(p):
    if sys.platform.startswith("win"):
        os.startfile(p)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


class App:
    def __init__(self, root):
        self.root = root
        root.title("IMG Prefix Fixer")
        root.geometry("880x760")
        root.minsize(740, 620)
        self.q = queue.Queue()

        self.folder_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.ext_var = tk.StringVar(value="png")
        self.recursive_var = tk.BooleanVar(value=True)
        self.overwrite_var = tk.BooleanVar(value=False)
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.report_var = tk.StringVar(value=os.path.join(script_dir, "prefix_fix_report.txt"))

        pad = dict(padx=10, pady=5)

        # 1) images folder
        fr = ttk.LabelFrame(root, text="1) Images folder (the one with prefixed names, e.g. upscaled)")
        fr.pack(fill="x", **pad)
        fr.columnconfigure(0, weight=1)
        ttk.Entry(fr, textvariable=self.folder_var).grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(fr, text="Browse...", command=self.pick_folder).grid(row=0, column=1, padx=6)
        row2 = ttk.Frame(fr)
        row2.grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))
        ttk.Checkbutton(row2, text="Include subfolders", variable=self.recursive_var).pack(side="left")
        ttk.Label(row2, text="     Extensions (comma separated):").pack(side="left")
        ttk.Entry(row2, textvariable=self.ext_var, width=14).pack(side="left", padx=4)

        # 2) IMG files
        fr2 = ttk.LabelFrame(root, text="2) IMG files to read")
        fr2.pack(fill="both", expand=False, **pad)
        fr2.columnconfigure(0, weight=1)
        self.lb = tk.Listbox(fr2, height=6, selectmode="extended", activestyle="none")
        self.lb.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(fr2, orient="vertical", command=self.lb.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=6)
        self.lb.config(yscrollcommand=sb.set)
        btns = ttk.Frame(fr2)
        btns.grid(row=0, column=2, sticky="n", padx=6, pady=6)
        ttk.Button(btns, text="Add IMG...", command=self.add_imgs).pack(fill="x", pady=2)
        ttk.Button(btns, text="Remove selected", command=self.remove_imgs).pack(fill="x", pady=2)
        ttk.Button(btns, text="Clear all", command=lambda: self.lb.delete(0, "end")).pack(fill="x", pady=2)

        # 3) output folder
        fr3 = ttk.LabelFrame(root, text="3) Output folder (copies without prefix go here)")
        fr3.pack(fill="x", **pad)
        fr3.columnconfigure(0, weight=1)
        ttk.Entry(fr3, textvariable=self.out_var).grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(fr3, text="Browse...", command=self.pick_out).grid(row=0, column=1, padx=6)
        ttk.Checkbutton(fr3, text="Overwrite files that already exist in the output folder",
                        variable=self.overwrite_var).grid(row=1, column=0, columnspan=2,
                                                          sticky="w", padx=6, pady=(0, 6))

        # 4) report
        fr4 = ttk.LabelFrame(root, text="4) Report file")
        fr4.pack(fill="x", **pad)
        fr4.columnconfigure(0, weight=1)
        ttk.Entry(fr4, textvariable=self.report_var).grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(fr4, text="Save as...", command=self.pick_report).grid(row=0, column=1, padx=6)

        # run
        run = ttk.Frame(root)
        run.pack(fill="x", **pad)
        self.start_btn = ttk.Button(run, text="Start", command=self.start)
        self.start_btn.pack(side="left")
        ttk.Button(run, text="Open output folder", command=self.open_out).pack(side="left", padx=8)
        ttk.Button(run, text="Open report", command=self.open_report).pack(side="left")
        self.pb = ttk.Progressbar(run, mode="determinate")
        self.pb.pack(side="left", fill="x", expand=True, padx=8)

        # log
        self.log_box = scrolledtext.ScrolledText(root, height=10, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ---- helpers
    def log(self, msg):
        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def pick_folder(self):
        d = filedialog.askdirectory(title="Select the folder that contains the images")
        if d:
            self.folder_var.set(os.path.normpath(d))

    def pick_out(self):
        d = filedialog.askdirectory(title="Select the output folder")
        if d:
            self.out_var.set(os.path.normpath(d))

    def add_imgs(self):
        files = filedialog.askopenfilenames(
            title="Select IMG files",
            filetypes=[("IMG archives", "*.img"), ("All files", "*.*")],
        )
        existing = set(self.lb.get(0, "end"))
        for p in files:
            p = os.path.normpath(p)
            if p not in existing:
                self.lb.insert("end", p)

    def remove_imgs(self):
        for i in reversed(self.lb.curselection()):
            self.lb.delete(i)

    def pick_report(self):
        p = filedialog.asksaveasfilename(
            title="Save report as", defaultextension=".txt",
            initialfile="prefix_fix_report.txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if p:
            self.report_var.set(os.path.normpath(p))

    def open_out(self):
        p = self.out_var.get().strip()
        if not p or not os.path.isdir(p):
            messagebox.showinfo("Output folder", "The output folder does not exist yet.")
            return
        try:
            _open_path(p)
        except Exception as e:
            messagebox.showerror("Output folder", str(e))

    def open_report(self):
        p = self.report_var.get()
        if not os.path.isfile(p):
            messagebox.showinfo("Report", "The report file does not exist yet.")
            return
        try:
            _open_path(p)
        except Exception as e:
            messagebox.showerror("Report", str(e))

    def set_busy(self, busy):
        self.start_btn.config(state="disabled" if busy else "normal")

    # ---- run
    def start(self):
        folder = self.folder_var.get().strip()
        out_dir = self.out_var.get().strip()
        imgs = list(self.lb.get(0, "end"))
        report = self.report_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Missing input", "Please choose a valid images folder.")
            return
        if not imgs:
            messagebox.showwarning("Missing input", "Please add at least one IMG file.")
            return
        if not out_dir:
            messagebox.showwarning("Missing input", "Please choose the output folder.")
            return
        if _same_dir(folder, out_dir):
            messagebox.showwarning("Wrong folder",
                                   "The output folder must be different from the images folder.")
            return
        if not report:
            messagebox.showwarning("Missing input", "Please choose where to save the report.")
            return
        exts = {e.strip().lower().lstrip(".")
                for e in self.ext_var.get().replace(";", ",").split(",") if e.strip()}
        if not exts:
            exts = {"png"}

        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")
        self.pb.config(value=0, maximum=1)
        self.set_busy(True)

        t = threading.Thread(
            target=self._worker,
            args=(folder, imgs, out_dir, exts, self.recursive_var.get(),
                  self.overwrite_var.get(), report),
            daemon=True,
        )
        t.start()
        self.root.after(100, self._poll)

    def _worker(self, folder, imgs, out_dir, exts, recursive, overwrite, report):
        try:
            stats = run_fix(
                folder, imgs, out_dir, exts, recursive, overwrite, report,
                log=lambda m: self.q.put(("log", m)),
                progress=lambda d, t: self.q.put(("progress", d, t)),
            )
            self.q.put(("done", stats, out_dir, report))
        except Exception:
            import traceback
            self.q.put(("error", traceback.format_exc()))

    def _poll(self):
        finished = False
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.log(msg[1])
                elif kind == "progress":
                    self.pb.config(maximum=max(msg[2], 1), value=msg[1])
                elif kind == "done":
                    s, out_dir, report = msg[1], msg[2], msg[3]
                    self.log("")
                    self.log(f"Done. {s['textures']} unique textures: {s['exact']} already exact, "
                             f"{s['copied']} copied without prefix, {s['existed']} skipped (already in output), "
                             f"{s['notfound']} NOT FOUND.")
                    if s["multi"]:
                        self.log(f"{s['multi']} texture(s) had several prefixed files "
                                 f"({s['multi_diff']} with different content) - see the report.")
                    if s["errors"] or s["bad"] or s["invalid"]:
                        self.log(f"Problems: {s['errors']} copy error(s), {s['bad']} unreadable TXD, "
                                 f"{s['invalid']} invalid name(s) - see the report.")
                    self.log(f"Output folder: {out_dir}")
                    self.log(f"Report saved to: {report}")
                    messagebox.showinfo(
                        "Finished",
                        f"{s['copied']} file(s) copied to:\n{out_dir}\n\n"
                        f"{s['notfound']} texture(s) not found.\n\nReport:\n{report}")
                    finished = True
                elif kind == "error":
                    self.log(msg[1])
                    messagebox.showerror("Error", msg[1])
                    finished = True
        except queue.Empty:
            pass
        if finished:
            self.set_busy(False)
        else:
            self.root.after(100, self._poll)


def main():
    if tk is None:
        print("tkinter is not available in this Python installation.")
        sys.exit(1)
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
