"""
Filename Keyword Counter
------------------------
Pick a folder and see which words appear most often in the file names.
Click a keyword to see which files contain it.

Requires only Python 3 (tkinter is included). Run:  python filename_keywords.py
"""

import os
import re
import tkinter as tk
from tkinter import filedialog, ttk
from collections import defaultdict


def split_name(filename):
    """Turn 'Rock_Wall01_Albedo-upscaled.png' into ['rock', 'wall', 'albedo', 'upscaled']."""
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)   # split camelCase
    words = re.split(r"[^A-Za-z]+", stem)               # split on anything that isn't a letter
    return [w.lower() for w in words if len(w) >= 2]


def scan_folder(folder, recursive):
    keywords = defaultdict(set)  # keyword -> set of file paths
    if recursive:
        walker = os.walk(folder)
    else:
        walker = [(folder, [], os.listdir(folder))]
    for root, _dirs, files in walker:
        for name in files:
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            for word in set(split_name(name)):
                keywords[word].add(path)
    return keywords


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Filename Keyword Counter")
        self.geometry("760x520")
        self.keywords = {}
        self.folder = ""

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="Choose Folder...", command=self.choose_folder).pack(side="left")

        self.recursive = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Include subfolders", variable=self.recursive,
                        command=self.rescan).pack(side="left", padx=10)

        ttk.Label(top, text="Min count:").pack(side="left")
        self.min_count = tk.IntVar(value=2)
        ttk.Spinbox(top, from_=1, to=9999, width=5, textvariable=self.min_count,
                    command=self.refresh).pack(side="left", padx=4)

        self.path_label = ttk.Label(self, text="No folder selected", padding=(8, 0))
        self.path_label.pack(fill="x")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(panes)
        self.tree = ttk.Treeview(left, columns=("word", "count"), show="headings")
        self.tree.heading("word", text="Keyword")
        self.tree.heading("count", text="Files")
        self.tree.column("word", width=180)
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
        self.keywords = scan_folder(self.folder, self.recursive.get())
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.files.delete(0, "end")
        try:
            minimum = int(self.min_count.get())
        except (tk.TclError, ValueError):
            minimum = 1
        rows = sorted(
            ((w, len(p)) for w, p in self.keywords.items() if len(p) >= minimum),
            key=lambda r: (-r[1], r[0]),
        )
        for word, count in rows:
            self.tree.insert("", "end", values=(word, count))
        self.status.config(text=f"{len(rows)} keywords shown")

    def show_files(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        word = self.tree.item(sel[0], "values")[0]
        self.files.delete(0, "end")
        for path in sorted(self.keywords.get(word, [])):
            self.files.insert("end", os.path.relpath(path, self.folder))


if __name__ == "__main__":
    App().mainloop()
