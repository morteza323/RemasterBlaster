#!/usr/bin/env python3
"""
scan_png_sizes.py
Scan a folder of PNGs and write every file's dimensions to a report.
Does NOT modify any file.

Additionally writes a compatible log file (123.txt) that can be used
directly by fix_gta_sa_textures.py for the non-standard textures.

Usage:
  python scan_png_sizes.py "H:\\upscaled"
  python scan_png_sizes.py "H:\\upscaled" "report.txt"

Allowed (standard) sides for GTA SA: 64, 128, 256, 512, 1024, 2048
A texture is STANDARD only if BOTH width and height are in that set.
"""
from __future__ import annotations
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Pillow is required.  pip install Pillow")
    sys.exit(1)

ALLOWED = {64, 128, 256, 512, 1024, 2048}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scan_png_sizes.py <png_folder> [report.txt]")
        return 1

    root = Path(sys.argv[1]).expanduser().resolve()
    if not root.is_dir():
        print(f"Folder not found: {root}")
        return 1

    out_path = Path(sys.argv[2]).expanduser().resolve() if len(sys.argv) >= 3 else (root / "_png_size_report.txt")

    # Log file that is compatible with fix_gta_sa_textures.py
    log_path = root / "123.txt"

    pngs = sorted(root.rglob("*.png"))
    if not pngs:
        print(f"No PNG files under: {root}")
        return 1

    lines: list[str] = []
    lines.append(f"PNG size scan")
    lines.append(f"Folder: {root}")
    lines.append(f"Total PNGs: {len(pngs)}")
    lines.append(f"Allowed sides: {sorted(ALLOWED)}")
    lines.append(f"STANDARD = both width and height in allowed set")
    lines.append("")
    lines.append(f"{'STATUS':<12} {'W':>6} {'H':>6}  PATH")
    lines.append("-" * 100)

    standard = 0
    non_standard = 0
    errors = 0
    non_std_list: list[str] = []
    resize_log_lines: list[str] = []

    for p in pngs:
        rel = p.relative_to(root)
        try:
            with Image.open(p) as im:
                w, h = im.size
            ok = (w in ALLOWED) and (h in ALLOWED)
            if ok:
                status = "STANDARD"
                standard += 1
            else:
                status = "NON-STD"
                non_standard += 1
                non_std_list.append(f"{w}x{h}\t{rel}")
                # Format compatible with fix_gta_sa_textures.py
                # We treat current size as the "original" size so the fixer
                # can calculate a proper power-of-two target.
                resize_log_lines.append(f"[RESIZE] {p.name}: {w}x{h} -> {w}x{h}")
            lines.append(f"{status:<12} {w:>6} {h:>6}  {rel}")
        except Exception as e:
            errors += 1
            lines.append(f"{'ERROR':<12} {'?':>6} {'?':>6}  {rel}  ({e})")

    lines.append("")
    lines.append("=" * 60)
    lines.append(f"SUMMARY")
    lines.append(f"  STANDARD     : {standard}")
    lines.append(f"  NON-STANDARD : {non_standard}")
    lines.append(f"  ERRORS       : {errors}")
    lines.append(f"  TOTAL        : {len(pngs)}")
    lines.append("")

    if non_std_list:
        lines.append("=== NON-STANDARD files only ===")
        for s in non_std_list:
            lines.append(s)

    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"Report written: {out_path}")

    # Write the compatible log for the fixer
    if resize_log_lines:
        log_path.write_text("\n".join(resize_log_lines) + "\n", encoding="utf-8")
        print(f"Compatible log written: {log_path}")
        print(f"  ({len(resize_log_lines)} non-standard files ready for fix_gta_sa_textures.py)")
    else:
        print("No non-standard files found. No 123.txt log created.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())