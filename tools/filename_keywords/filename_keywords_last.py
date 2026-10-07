"""
Filename Suffix Counter
-----------------------
Pick a folder and find files whose names END the same way, even when the
beginnings (prefixes) are different. Example:

    vgnamun_blueroof_64.png
    vgnhseing1_blueroof_64.png
    vgnretail5_blueroof_64.png
    blueroof_64.png

all share the ending  *blueroof_64.png  -> 4 files.

Click an ending to see which files have it.

Requires only Python 3 (tkinter is included). Run:  python filename_keywords.py
"""

import os
import re
import tkinter as tk
from tkinter import filedialog, ttk
from collections import defaultdict

SEPARATORS = re.compile(r"[^A-Za-z0-9]+")   # _  -  space  .  (  )  etc.


def suffix_candidates(filename):
    """
    All endings of a file name that start at a word boundary (extension kept).
    'vgnamun_blueroof_64.png' ->
        ['vgnamun_blueroof_64.png', 'blueroof_64.png', '64.png']
    """
    stem, _ext = os.path.splitext(filename)
    starts = [0] + [m.end() for m in SEPARATORS.finditer(stem) if m.end() < len(stem)]
    return [filename[s:] for s in starts]


def scan_folder(folder, recursive):
    """Returns (groups, display): lowercase ending -> set of file paths / original-case text."""
    groups = defaultdict(set)
    display = {}
    if recursive:
        walker = os.walk(folder)
    else:
        walker = [(folder, [], os.listdir(folder))]
    for root, _dirs, files in walker:
        for name in files:
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            for cand in suffix_candidates(name):
                key = cand.lower()
                groups[key].add(path)
                display.setdefault(key, cand)
    return groups, display


def common_endings(groups, display, minimum):
    """
    Keep only endings shared by >= minimum files, and drop an ending when a
    longer one covers exactly the same files (so you see '*blueroof_64.png'
    and not also '*roof_64.png' / '*64.png' for the same group).
    Returns a list of (text, count, key) sorted by count, then length.
    """
    rows = []
    for key, paths in groups.items():
        if len(paths) < minimum:
            continue
        if not re.search(r"[a-z]", os.path.splitext(key)[0]):
            continue  # skip endings that are only digits/extension, e.g. '64.png'
        sample = os.path.basename(next(iter(paths))).lower()
        redundant = any(
            len(longer) > len(key) and longer.endswith(key)
            and len(groups[longer]) == len(paths)
            for longer in suffix_candidates(sample)
        )
        if not redundant:
            rows.append(("*" + display[key], len(paths), key))
    rows.sort(key=lambda r: (-r[1], -len(r[2]), r[2]))
    return rows


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Filename Suffix Counter")
        self.geometry("900x560")
        self.groups = {}
        self.display = {}
        self.rows = []
        self.folder = ""

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="Choose Folder...", command=self.choose_folder).pack(side="left")

        self.recursive = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Include subfolders", variable=self.recursive,
                        command=self.rescan).pack(side="left", padx=10)

        ttk.Label(top, text="Min count:").pack(side="left")
        self.min_count = tk.IntVar(value=2)
        spin = ttk.Spinbox(top, from_=1, to=9999, width=5, textvariable=self.min_count,
                           command=self.refresh)
        spin.pack(side="left", padx=4)
        spin.bind("<KeyRelease>", lambda _e: self.refresh())

        self.path_label = ttk.Label(self, text="No folder selected", padding=(8, 0))
        self.path_label.pack(fill="x")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(panes)
        self.tree = ttk.Treeview(left, columns=("word", "count"), show="headings")
        self.tree.heading("word", text="Common ending")
        self.tree.heading("count", text="Files")
        self.tree.column("word", width=280)
        self.tree.column("count", width=60, anchor="e")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.show_files)
        panes.add(left, weight=1)

        right = ttk.Frame(panes)
        self.files = tk.Listbox(right)
        sb2 = ttk.Scrollbar(right, orient="vertical", command=self.files.yview)
        self.files.configure(yscrollcommand=sb2.set)
        self.files.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        panes.add(right, weight=2)

        self.status = ttk.Label(self, text="", padding=8)
        self.status.pack(fill="x")

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder = folder
            self.rescan()

    def rescan(self):
        if not self.folder:
            return
        self.path_label.config(text=self.folder)
        self.groups, self.display = scan_folder(self.folder, self.recursive.get())
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.files.delete(0, "end")
        try:
            minimum = max(1, int(self.min_count.get()))
        except (tk.TclError, ValueError):
            minimum = 2
        self.rows = common_endings(self.groups, self.display, minimum)
        for i, (text, count, _key) in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(i), values=(text, count))
        self.status.config(text=f"{len(self.rows)} common endings shown")

    def show_files(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        key = self.rows[int(sel[0])][2]
        self.files.delete(0, "end")
        for path in sorted(self.groups.get(key, [])):
            self.files.insert("end", os.path.relpath(path, self.folder))


if __name__ == "__main__":
    App().mainloop()
