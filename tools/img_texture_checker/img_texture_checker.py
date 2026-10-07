#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IMG Texture Checker
-------------------
Opens GTA III / Vice City / San Andreas IMG archives, reads the texture names
inside every TXD and checks whether a matching image exists in a folder.

Matching rules for every texture "tex" found inside a TXD:
  1) exact match:   tex.png
  2) prefixed:      <anything>_tex.png   (any prefix that ends with an underscore,
                    e.g. player_props_tex.png or dyn_objects_tex.png)
(case-insensitive, extensions configurable)

Anything that matches neither is written to the report file.

Requirements: Python 3.8+ (tkinter ships with the normal Windows installer).
Run:  python img_texture_checker.py
"""

import os
import sys
import time
import queue
import struct
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
# Comparison + report
# ----------------------------------------------------------------------------
def build_file_index(folder, exts, recursive):
    """Return (exact, tails) as sets of lower-case names without extension.

    exact: every image file name           "player_props_cj_bottle3"
    tails: every part after an underscore  "props_cj_bottle3", "cj_bottle3"
    so a texture "CJ_bottle3" matches any file named <prefix>_CJ_bottle3.
    """
    exact, tails = set(), set()

    def add(fn):
        base, ext = os.path.splitext(fn)
        if ext.lower().lstrip(".") not in exts:
            return
        low = base.lower()
        exact.add(low)
        i = low.find("_")
        while i != -1:
            tail = low[i + 1:]
            if tail:
                tails.add(tail)
            i = low.find("_", i + 1)

    if recursive:
        for _, _, files in os.walk(folder):
            for fn in files:
                add(fn)
    else:
        for fn in os.listdir(folder):
            if os.path.isfile(os.path.join(folder, fn)):
                add(fn)
    return exact, tails


def write_report(path, folder, exts, results, totals, elapsed):
    ext0 = sorted(exts)[0]
    L = []
    L.append("IMG TEXTURE CHECK REPORT")
    L.append("=" * 60)
    L.append("Date      : " + time.strftime("%Y-%m-%d %H:%M:%S"))
    L.append("Folder    : " + folder)
    L.append("Extensions: " + ", ".join(sorted(exts)))
    L.append("")
    L.append("Summary")
    L.append("-" * 60)
    L.append(f"TXD files checked        : {totals['txd']}")
    L.append(f"Textures checked         : {totals['tex']}")
    L.append(f"  found (exact name)     : {totals['exact']}")
    L.append(f"  found (with prefix_)   : {totals['prefixed']}")
    L.append(f"  MISSING                : {totals['missing']}")
    L.append(f"TXD files unreadable     : {totals['bad']}")
    L.append(f"Time                     : {elapsed:.1f}s")
    L.append("")

    for r in results:
        missing_txds = [t for t in r["txds"] if t[2]]
        if not missing_txds and not r["bad"]:
            continue
        L.append("=" * 60)
        L.append(f"IMG: {os.path.basename(r['img'])}")
        L.append("=" * 60)
        for txd_name, total, missing in missing_txds:
            L.append(f"[{txd_name}]  missing {len(missing)} / {total}")
            for m in missing:
                L.append(f"    - {m}.{ext0}")
            L.append("")
        if r["bad"]:
            L.append("Unreadable / invalid TXD files:")
            for b in r["bad"]:
                L.append(f"    ! {b}")
            L.append("")

    if totals["missing"] == 0 and totals["bad"] == 0:
        L.append("Nothing is missing. All textures were found.")

    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        out.write("\n".join(L) + "\n")


def run_check(folder, img_paths, exts, recursive, report_path,
              log=print, progress=lambda d, t: None):
    started = time.time()
    log(f"Indexing images in: {folder}")
    exact_index, tail_index = build_file_index(folder, exts, recursive)
    log(f"  {len(exact_index)} image file(s) found ({', '.join(sorted(exts))})")

    jobs = []
    total = 0
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

    totals = dict(txd=0, tex=0, exact=0, prefixed=0, missing=0, bad=0)
    results = []
    done = 0
    progress(0, total)

    for img_path, txds in jobs:
        res = {"img": img_path, "txds": [], "bad": []}
        results.append(res)
        try:
            f = open(img_path, "rb")
        except OSError as e:
            log(f"[ERROR] cannot open {img_path}: {e}")
            continue
        with f:
            file_size = os.fstat(f.fileno()).st_size
            log(f"Checking {os.path.basename(img_path)} ...")
            for name, off, _size in txds:
                done += 1
                if done % 20 == 0 or done == total:
                    progress(done, total)
                try:
                    texs = read_txd_texture_names(f, off, file_size)
                except Exception:
                    texs = None
                if texs is None:
                    res["bad"].append(name)
                    totals["bad"] += 1
                    continue

                totals["txd"] += 1
                seen, missing = set(), []
                for tex in texs:
                    low = tex.lower()
                    if low in seen:
                        continue
                    seen.add(low)
                    totals["tex"] += 1
                    if low in exact_index:
                        totals["exact"] += 1
                    elif low in tail_index:
                        totals["prefixed"] += 1
                    else:
                        missing.append(tex)
                        totals["missing"] += 1
                res["txds"].append((name, len(seen), missing))
                if missing:
                    log(f"  {name}: {len(missing)} missing")

    progress(total, total)
    write_report(report_path, folder, exts, results, totals, time.time() - started)
    return totals


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("IMG Texture Checker")
        root.geometry("860x680")
        root.minsize(720, 560)
        self.q = queue.Queue()

        self.folder_var = tk.StringVar()
        self.ext_var = tk.StringVar(value="png")
        self.recursive_var = tk.BooleanVar(value=True)
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.report_var = tk.StringVar(value=os.path.join(script_dir, "missing_report.txt"))

        pad = dict(padx=10, pady=5)

        # 1) folder
        fr = ttk.LabelFrame(root, text="1) Images folder")
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
        fr2 = ttk.LabelFrame(root, text="2) IMG files to check")
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

        # 3) report
        fr3 = ttk.LabelFrame(root, text="3) Report file")
        fr3.pack(fill="x", **pad)
        fr3.columnconfigure(0, weight=1)
        ttk.Entry(fr3, textvariable=self.report_var).grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(fr3, text="Save as...", command=self.pick_report).grid(row=0, column=1, padx=6)

        # run
        run = ttk.Frame(root)
        run.pack(fill="x", **pad)
        self.start_btn = ttk.Button(run, text="Start check", command=self.start)
        self.start_btn.pack(side="left")
        self.open_btn = ttk.Button(run, text="Open report", command=self.open_report)
        self.open_btn.pack(side="left", padx=8)
        self.pb = ttk.Progressbar(run, mode="determinate")
        self.pb.pack(side="left", fill="x", expand=True, padx=8)

        # log
        self.log_box = scrolledtext.ScrolledText(root, height=12, state="disabled", font=("Consolas", 9))
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
            self.folder_var.set(d)

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
            initialfile="missing_report.txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if p:
            self.report_var.set(p)

    def open_report(self):
        p = self.report_var.get()
        if not os.path.isfile(p):
            messagebox.showinfo("Report", "The report file does not exist yet.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            messagebox.showerror("Report", str(e))

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.start_btn.config(state=state)

    # ---- run
    def start(self):
        folder = self.folder_var.get().strip()
        imgs = list(self.lb.get(0, "end"))
        report = self.report_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Missing input", "Please choose a valid images folder.")
            return
        if not imgs:
            messagebox.showwarning("Missing input", "Please add at least one IMG file.")
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
            args=(folder, imgs, exts, self.recursive_var.get(), report),
            daemon=True,
        )
        t.start()
        self.root.after(100, self._poll)

    def _worker(self, folder, imgs, exts, recursive, report):
        try:
            totals = run_check(
                folder, imgs, exts, recursive, report,
                log=lambda m: self.q.put(("log", m)),
                progress=lambda d, t: self.q.put(("progress", d, t)),
            )
            self.q.put(("done", totals, report))
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
                    t, report = msg[1], msg[2]
                    self.log("")
                    self.log(f"Done. {t['txd']} TXD, {t['tex']} textures: "
                             f"{t['exact']} exact, {t['prefixed']} prefixed, "
                             f"{t['missing']} MISSING, {t['bad']} unreadable TXD.")
                    self.log(f"Report saved to: {report}")
                    messagebox.showinfo(
                        "Finished",
                        f"{t['missing']} missing texture(s).\n\nReport saved to:\n{report}")
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
