#!/usr/bin/env python3
"""
GTA SA Texture Dimension Fixer
--------------------------------
Repairs the files that were modified by the previous/bad dimension normalizer.

IMPORTANT:
- This script uses 123.txt (the old run log) as the source of ORIGINAL dimensions.
- If either original dimension was already a power of two, it restores that exact size.
  Example: 256x512 stays 256x512. It must NOT be converted to 512x1024.
- If neither dimension was a power of two, it uniformly scales the current image so
  one dimension becomes a power of two. This preserves aspect ratio.
- It never crops, rotates, or intentionally changes aspect ratio.
- Because there was no backup, this can restore dimensions/aspect ratio, but it cannot
  recover pixels that were permanently lost by a previous non-uniform resize.

Usage:
    python fix_gta_sa_textures.py "H:\\gta sa textures\\fucked up fixed with ai"

Optional:
    python fix_gta_sa_textures.py "FOLDER" --log "123.txt" --dry-run
"""

import argparse, os, re, math, shutil
from pathlib import Path
from PIL import Image

def is_pow2(n):
    return n > 0 and (n & (n - 1)) == 0

def best_target(w, h):
    if is_pow2(w) or is_pow2(h):
        return w, h

    candidates = []
    for dim_name, dim in (("width", w), ("height", h)):
        p = 2 ** round(math.log2(dim))
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

def parse_log(log_path):
    rows = []
    rx = re.compile(r'\[RESIZE\]\s+(.*?):\s+(\d+)x(\d+)\s+->\s+(\d+)x(\d+)')
    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = rx.search(line)
            if m:
                rows.append((m.group(1).strip(),
                             int(m.group(2)), int(m.group(3)),
                             int(m.group(4)), int(m.group(5))))
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="Folder containing the PNG textures")
    ap.add_argument("--log", default=None, help="Path to 123.txt log (optional)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.folder)

    if not root.is_dir():
        raise SystemExit(f"Folder not found: {root}")

    # Auto-detect log file
    if args.log:
        log = Path(args.log)
    else:
        # First try inside the texture folder, then current directory
        candidates = [
            root / "123.txt",
            Path("123.txt"),
        ]
        log = None
        for c in candidates:
            if c.is_file():
                log = c
                break
        if log is None:
            raise SystemExit(
                "Log not found.\n"
                "Looked for:\n"
                f"  - {root / '123.txt'}\n"
                f"  - 123.txt (current folder)\n"
                "First run the scanner so it creates 123.txt inside the texture folder."
            )

    if not log.is_file():
        raise SystemExit(f"Log not found: {log}")

    print(f"Using log: {log}")

    rows = parse_log(log)
    if not rows:
        raise SystemExit("No [RESIZE] entries found in the log.")

    # Index every PNG by basename. We use all matches because the log can contain
    # duplicate names in different subfolders.
    index = {}
    for p in root.rglob("*.png"):
        index.setdefault(p.name.lower(), []).append(p)

    # Backup only the files this script is about to modify.
    backup_root = root / "_dimension_fixer_backup"
    changed = 0
    missing = 0
    ambiguous = 0

    for filename, ow, oh, badw, badh in rows:
        matches = index.get(filename.lower(), [])
        if not matches:
            print(f"[MISSING] {filename} (expected original {ow}x{oh})")
            missing += 1
            continue

        target_w, target_h = best_target(ow, oh)

        for path in matches:
            try:
                with Image.open(path) as im:
                    current = im.size

                # If it is already the intended fixed size, leave it alone.
                if current == (target_w, target_h):
                    print(f"[KEEP]    {path.name}: {current[0]}x{current[1]}")
                    continue

                rel = path.relative_to(root)
                backup = backup_root / rel
                if not args.dry_run:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup)

                print(f"[FIX]     {path} : {current[0]}x{current[1]} -> {target_w}x{target_h}")

                if not args.dry_run:
                    with Image.open(path) as im:
                        # High-quality uniform resize. No crop, rotation, or aspect-ratio distortion.
                        im2 = im.resize((target_w, target_h), Image.Resampling.LANCZOS)
                        save_kwargs = {}
                        if "transparency" in im.info:
                            save_kwargs["transparency"] = im.info["transparency"]
                        im2.save(path, **save_kwargs)

                changed += 1

            except Exception as e:
                print(f"[ERROR]   {path}: {e}")

    print("\nDONE")
    print(f"Log entries: {len(rows)}")
    print(f"Files changed: {changed}")
    print(f"Missing names: {missing}")
    if not args.dry_run:
        print(f"Backups: {backup_root}")

if __name__ == "__main__":
    main()