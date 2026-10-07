#!/usr/bin/env python3
"""
GTA SA Texture Dimension Tool
=============================
Modern GUI + CLI tool for scanning and fixing GTA San Andreas texture dimensions.

Features:
- Dark modern UI (CustomTkinter)
- Auto-detect non-power-of-two / non-standard textures
- High-quality Lanczos resize while preserving aspect ratio
- Backup system
- Dry-run mode
- Progress bar + live log
- Full CLI support

Requirements:
    pip install customtkinter pillow

Usage (GUI):
    python GTA_SA_Texture_Tool.py

Usage (CLI):
    python GTA_SA_Texture_Tool.py --cli --folder "H:\\textures" --scan
    python GTA_SA_Texture_Tool.py --cli --folder "H:\\textures" --fix
    python GTA_SA_Texture_Tool.py --cli --folder "H:\\textures" --fix --dry-run
"""

from __future__ import annotations

import argparse
import math
import os
import queue
import shutil
import sys
import threading
from pathlib import Path
from typing import Callable

try:
    from PIL import Image
except ImportError:
    print("Pillow is required →  pip install pillow")
    sys.exit(1)

# ─────────────────────────────────────────────
#  Core logic (shared by GUI and CLI)
# ─────────────────────────────────────────────

ALLOWED = {64, 128, 256, 512, 1024, 2048}


def is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def best_target(w: int, h: int) -> tuple[int, int]:
    """Choose the closest power-of-two size while preserving aspect ratio."""
    if is_pow2(w) or is_pow2(h):
        return w, h

    candidates = []
    for dim_name, dim in (("width", w), ("height", h)):
        p = 2 ** round(math.log2(max(dim, 1)))
        scale = p / dim
        nw = max(1, round(w * scale))
        nh = max(1, round(h * scale))
        if dim_name == "width":
            nw = p
        else:
            nh = p
        candidates.append((abs(scale - 1.0), nw, nh))

    _, nw, nh = min(candidates, key=lambda x: x[0])
    return nw, nh


def scan_folder(root: Path, progress_cb: Callable | None = None) -> dict:
    """Scan all PNGs and return statistics + list of non-standard files."""
    pngs = sorted(root.rglob("*.png"))
    total = len(pngs)

    result = {
        "total": total,
        "standard": 0,
        "non_standard": 0,
        "errors": 0,
        "non_std_files": [],   # list of (rel_path, w, h, target_w, target_h)
        "all_files": [],
    }

    for i, p in enumerate(pngs):
        rel = p.relative_to(root)
        try:
            with Image.open(p) as im:
                w, h = im.size
            ok = (w in ALLOWED) and (h in ALLOWED)
            status = "STANDARD" if ok else "NON-STD"
            if ok:
                result["standard"] += 1
            else:
                result["non_standard"] += 1
                tw, th = best_target(w, h)
                result["non_std_files"].append((str(rel), w, h, tw, th))
            result["all_files"].append((str(rel), w, h, status))
        except Exception as e:
            result["errors"] += 1
            result["all_files"].append((str(rel), 0, 0, f"ERROR: {e}"))

        if progress_cb:
            progress_cb(i + 1, total, str(rel))

    return result


def fix_textures(
    root: Path,
    non_std_files: list,
    dry_run: bool = False,
    make_backup: bool = True,
    progress_cb: Callable | None = None,
    log_cb: Callable | None = None,
) -> dict:
    """Resize non-standard textures to the calculated targets."""
    backup_root = root / "_dimension_fixer_backup"
    changed = 0
    skipped = 0
    errors = 0
    total = len(non_std_files)

    for i, (rel_str, ow, oh, tw, th) in enumerate(non_std_files):
        path = root / rel_str
        try:
            with Image.open(path) as im:
                current = im.size

            if current == (tw, th):
                skipped += 1
                if log_cb:
                    log_cb(f"[KEEP]  {rel_str}  {current[0]}x{current[1]}")
            else:
                if make_backup and not dry_run:
                    backup = backup_root / rel_str
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup)

                if log_cb:
                    action = "[DRY]" if dry_run else "[FIX]"
                    log_cb(f"{action}  {rel_str}  {current[0]}x{current[1]} → {tw}x{th}")

                if not dry_run:
                    with Image.open(path) as im:
                        im2 = im.resize((tw, th), Image.Resampling.LANCZOS)
                        save_kwargs = {}
                        if "transparency" in im.info:
                            save_kwargs["transparency"] = im.info["transparency"]
                        im2.save(path, **save_kwargs)
                changed += 1

        except Exception as e:
            errors += 1
            if log_cb:
                log_cb(f"[ERROR] {rel_str}: {e}")

        if progress_cb:
            progress_cb(i + 1, total, rel_str)

    return {
        "changed": changed,
        "skipped": skipped,
        "errors": errors,
        "backup_path": str(backup_root) if make_backup else None,
    }


# ─────────────────────────────────────────────
#  CLI mode
# ─────────────────────────────────────────────

def run_cli():
    parser = argparse.ArgumentParser(description="GTA SA Texture Dimension Tool (CLI)")
    parser.add_argument("--cli", action="store_true", help="Force CLI mode")
    parser.add_argument("--folder", required=True, help="Texture folder path")
    parser.add_argument("--scan", action="store_true", help="Only scan")
    parser.add_argument("--fix", action="store_true", help="Scan + fix")
    parser.add_argument("--dry-run", action="store_true", help="Don't write files")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup")
    args = parser.parse_args()

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        print(f"Folder not found: {root}")
        sys.exit(1)

    print(f"Scanning: {root}")
    result = scan_folder(root)

    print("\n========== SCAN RESULT ==========")
    print(f"Total PNGs     : {result['total']}")
    print(f"STANDARD      : {result['standard']}")
    print(f"NON-STANDARD  : {result['non_standard']}")
    print(f"ERRORS        : {result['errors']}")

    if result["non_std_files"]:
        print("\nNon-standard files:")
        for rel, w, h, tw, th in result["non_std_files"][:30]:
            print(f"  {w}x{h} → {tw}x{th}   {rel}")
        if len(result["non_std_files"]) > 30:
            print(f"  ... and {len(result['non_std_files']) - 30} more")

    if args.scan and not args.fix:
        return

    if not result["non_std_files"]:
        print("\nNothing to fix.")
        return

    print("\n========== FIXING ==========")
    fix_result = fix_textures(
        root,
        result["non_std_files"],
        dry_run=args.dry_run,
        make_backup=not args.no_backup,
        log_cb=print,
    )
    print(f"\nChanged : {fix_result['changed']}")
    print(f"Skipped : {fix_result['skipped']}")
    print(f"Errors  : {fix_result['errors']}")
    if fix_result["backup_path"]:
        print(f"Backup  : {fix_result['backup_path']}")


# ─────────────────────────────────────────────
#  GUI mode (CustomTkinter)
# ─────────────────────────────────────────────

def run_gui():
    try:
        import customtkinter as ctk
    except ImportError:
        print("CustomTkinter is required for GUI mode.")
        print("Install it with:  pip install customtkinter")
        print("\nYou can still use CLI mode:")
        print('  python GTA_SA_Texture_Tool.py --cli --folder "PATH" --scan')
        sys.exit(1)

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    class App(ctk.CTk):
        def __init__(self):
            super().__init__()
            self.title("GTA SA Texture Dimension Tool")
            self.geometry("980x720")
            self.minsize(800, 600)

            self.folder_path = ctk.StringVar()
            self.dry_run = ctk.BooleanVar(value=False)
            self.make_backup = ctk.BooleanVar(value=True)
            self.scan_result = None
            self.is_busy = False

            self._build_ui()

        def _build_ui(self):
            # Header
            header = ctk.CTkFrame(self, corner_radius=0, fg_color=("#1a1a2e", "#1a1a2e"))
            header.pack(fill="x")
            ctk.CTkLabel(
                header,
                text="GTA SA  •  Texture Dimension Tool",
                font=ctk.CTkFont(size=22, weight="bold"),
            ).pack(pady=14)

            # Main container
            main = ctk.CTkFrame(self, fg_color="transparent")
            main.pack(fill="both", expand=True, padx=16, pady=12)

            # ── Folder selection ──
            folder_frame = ctk.CTkFrame(main)
            folder_frame.pack(fill="x", pady=(0, 10))

            ctk.CTkLabel(folder_frame, text="Texture Folder", font=ctk.CTkFont(size=13, weight="bold")).pack(
                anchor="w", padx=12, pady=(10, 4)
            )

            row = ctk.CTkFrame(folder_frame, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(0, 12))

            self.folder_entry = ctk.CTkEntry(row, textvariable=self.folder_path, placeholder_text="Select or paste folder path...")
            self.folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

            ctk.CTkButton(row, text="Browse", width=90, command=self._browse).pack(side="left")

            # ── Options ──
            opts = ctk.CTkFrame(main)
            opts.pack(fill="x", pady=(0, 10))

            ctk.CTkLabel(opts, text="Options", font=ctk.CTkFont(size=13, weight="bold")).pack(
                anchor="w", padx=12, pady=(10, 6)
            )

            opt_row = ctk.CTkFrame(opts, fg_color="transparent")
            opt_row.pack(fill="x", padx=12, pady=(0, 12))

            ctk.CTkCheckBox(opt_row, text="Dry-run (preview only)", variable=self.dry_run).pack(side="left", padx=(0, 20))
            ctk.CTkCheckBox(opt_row, text="Create backup", variable=self.make_backup).pack(side="left")

            # ── Buttons ──
            btn_row = ctk.CTkFrame(main, fg_color="transparent")
            btn_row.pack(fill="x", pady=(0, 10))

            self.scan_btn = ctk.CTkButton(
                btn_row, text="🔍  Scan", width=140, height=38,
                fg_color="#0f3460", hover_color="#16213e", command=self._start_scan
            )
            self.scan_btn.pack(side="left", padx=(0, 8))

            self.fix_btn = ctk.CTkButton(
                btn_row, text="✨  Fix Non-Standard", width=180, height=38,
                fg_color="#e94560", hover_color="#c73e54", command=self._start_fix, state="disabled"
            )
            self.fix_btn.pack(side="left", padx=(0, 8))

            self.clear_btn = ctk.CTkButton(
                btn_row, text="Clear Log", width=100, height=38,
                fg_color="#333", hover_color="#444", command=self._clear_log
            )
            self.clear_btn.pack(side="right")

            # ── Stats ──
            self.stats_frame = ctk.CTkFrame(main)
            self.stats_frame.pack(fill="x", pady=(0, 10))

            self.stat_labels = {}
            for key, label in [
                ("total", "Total"),
                ("standard", "Standard"),
                ("non_standard", "Non-Standard"),
                ("errors", "Errors"),
            ]:
                f = ctk.CTkFrame(self.stats_frame, width=140, height=60)
                f.pack(side="left", expand=True, fill="both", padx=4, pady=8)
                f.pack_propagate(False)
                ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=11)).pack(pady=(8, 0))
                self.stat_labels[key] = ctk.CTkLabel(f, text="—", font=ctk.CTkFont(size=20, weight="bold"))
                self.stat_labels[key].pack()

            # ── Progress ──
            self.progress = ctk.CTkProgressBar(main, height=12)
            self.progress.pack(fill="x", pady=(0, 4))
            self.progress.set(0)
            self.progress_label = ctk.CTkLabel(main, text="", font=ctk.CTkFont(size=11))
            self.progress_label.pack(anchor="w")

            # ── Log ──
            log_frame = ctk.CTkFrame(main)
            log_frame.pack(fill="both", expand=True, pady=(6, 0))

            ctk.CTkLabel(log_frame, text="Log", font=ctk.CTkFont(size=13, weight="bold")).pack(
                anchor="w", padx=10, pady=(8, 4)
            )

            self.log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=12))
            self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # ── Helpers ──
        def _browse(self):
            from tkinter import filedialog
            path = filedialog.askdirectory(title="Select Texture Folder")
            if path:
                self.folder_path.set(path)

        def _log(self, msg: str):
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")

        def _clear_log(self):
            self.log_box.delete("1.0", "end")

        def _set_busy(self, busy: bool):
            self.is_busy = busy
            state = "disabled" if busy else "normal"
            self.scan_btn.configure(state=state)
            if not busy and self.scan_result and self.scan_result["non_standard"] > 0:
                self.fix_btn.configure(state="normal")
            else:
                self.fix_btn.configure(state="disabled")

        def _update_progress(self, current, total, name=""):
            if total == 0:
                self.progress.set(0)
            else:
                self.progress.set(current / total)
            self.progress_label.configure(text=f"{current}/{total}  {name}")

        def _update_stats(self, result: dict):
            for key in ("total", "standard", "non_standard", "errors"):
                self.stat_labels[key].configure(text=str(result[key]))

        # ── Actions ──
        def _start_scan(self):
            if self.is_busy:
                return
            folder = self.folder_path.get().strip()
            if not folder:
                self._log("[!] Please select a folder first.")
                return
            root = Path(folder)
            if not root.is_dir():
                self._log(f"[!] Folder not found: {folder}")
                return

            self._set_busy(True)
            self._log(f"Scanning → {root}")
            self.progress.set(0)

            def worker():
                def prog(c, t, n):
                    self.after(0, lambda: self._update_progress(c, t, n))

                result = scan_folder(root, progress_cb=prog)
                self.after(0, lambda: self._scan_done(result))

            threading.Thread(target=worker, daemon=True).start()

        def _scan_done(self, result: dict):
            self.scan_result = result
            self._update_stats(result)
            self._log(f"Done.  Standard: {result['standard']}  |  Non-Standard: {result['non_standard']}  |  Errors: {result['errors']}")

            if result["non_std_files"]:
                self._log("\n── Non-standard files (showing first 40) ──")
                for rel, w, h, tw, th in result["non_std_files"][:40]:
                    self._log(f"  {w:>4}x{h:<4} → {tw:>4}x{th:<4}   {rel}")
                if len(result["non_std_files"]) > 40:
                    self._log(f"  ... and {len(result['non_std_files']) - 40} more")
            else:
                self._log("All textures are already standard size. Nothing to fix.")

            self._set_busy(False)
            self.progress_label.configure(text="Scan complete")

        def _start_fix(self):
            if self.is_busy or not self.scan_result:
                return
            if self.scan_result["non_standard"] == 0:
                self._log("[!] No non-standard textures to fix.")
                return

            root = Path(self.folder_path.get().strip())
            dry = self.dry_run.get()
            backup = self.make_backup.get()

            self._set_busy(True)
            mode = "DRY-RUN" if dry else "FIX"
            self._log(f"\nStarting {mode} on {self.scan_result['non_standard']} files...")
            self.progress.set(0)

            def worker():
                def prog(c, t, n):
                    self.after(0, lambda: self._update_progress(c, t, n))

                def log(msg):
                    self.after(0, lambda: self._log(msg))

                fix_result = fix_textures(
                    root,
                    self.scan_result["non_std_files"],
                    dry_run=dry,
                    make_backup=backup,
                    progress_cb=prog,
                    log_cb=log,
                )
                self.after(0, lambda: self._fix_done(fix_result, dry))

            threading.Thread(target=worker, daemon=True).start()

        def _fix_done(self, result: dict, dry: bool):
            self._log(f"\n── Finished ──")
            self._log(f"Changed : {result['changed']}")
            self._log(f"Skipped : {result['skipped']}")
            self._log(f"Errors  : {result['errors']}")
            if result.get("backup_path") and not dry:
                self._log(f"Backup  : {result['backup_path']}")

            if not dry:
                # Re-scan to update stats
                self._log("\nRe-scanning to update statistics...")
                root = Path(self.folder_path.get().strip())
                new_result = scan_folder(root)
                self.scan_result = new_result
                self._update_stats(new_result)
                self._log(f"Now → Standard: {new_result['standard']}  Non-Standard: {new_result['non_standard']}")

            self._set_busy(False)
            self.progress_label.configure(text="Done")

    app = App()
    app.mainloop()


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # If any CLI flags are present → CLI mode, otherwise GUI
    if any(a in ("--cli", "--folder", "--scan", "--fix", "--dry-run", "--no-backup") for a in sys.argv[1:]):
        # Make sure --folder is present for CLI
        if "--folder" not in sys.argv:
            print("CLI mode requires --folder")
            print('Example:  python GTA_SA_Texture_Tool.py --cli --folder "H:\\textures" --scan')
            sys.exit(1)
        run_cli()
    else:
        run_gui()