#!/usr/bin/env python3
"""
resize_nonstd.py
ONLY resize these exact sizes (everything else untouched):

  1254 x 1254  →  1024 x 1024
  1774 x  887  →  2048 x 1024
  887  x 1774  →  1024 x 2048

Usage:
  python resize_nonstd.py "H:\\gta sa textures\\fucked up fixed with ai"
  python resize_nonstd.py "H:\\folder" --dry-run
"""
from __future__ import annotations
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Pillow required:  pip install Pillow")
    sys.exit(1)

# exact source size → target size
RULES = {
    (1254, 1254): (1024, 1024),
    (1774,  887): (2048, 1024),
    ( 887, 1774): (1024, 2048),
}


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python resize_nonstd.py <png_folder> [--dry-run]')
        return 1

    root = Path(sys.argv[1]).expanduser().resolve()
    dry = '--dry-run' in sys.argv[2:]

    if not root.is_dir():
        print(f'Folder not found: {root}')
        return 1

    pngs = sorted(root.rglob('*.png'))
    print(f'Folder : {root}')
    print(f'PNGs   : {len(pngs)}')
    print(f'Mode   : {"DRY-RUN (no writes)" if dry else "WRITE"}')
    print('Rules  :')
    for src, dst in RULES.items():
        print(f'  {src[0]}x{src[1]}  →  {dst[0]}x{dst[1]}')
    print()

    changed = 0
    skipped = 0
    errors = 0

    for p in pngs:
        rel = p.relative_to(root)
        try:
            with Image.open(p) as im:
                w, h = im.size
                key = (w, h)
                if key not in RULES:
                    skipped += 1
                    continue
                out_w, out_h = RULES[key]
                rgba = im.convert('RGBA')

            if dry:
                print(f'WOULD  {rel}  {w}x{h} → {out_w}x{out_h}')
            else:
                out = rgba.resize((out_w, out_h), Image.Resampling.LANCZOS)
                out.save(p, format='PNG', optimize=True)
                print(f'DONE   {rel}  {w}x{h} → {out_w}x{out_h}')
            changed += 1
        except Exception as e:
            errors += 1
            print(f'ERROR  {rel}: {e}')

    print()
    print(f'Changed : {changed}')
    print(f'Skipped : {skipped}  (size not in rules)')
    print(f'Errors  : {errors}')
    if dry:
        print('(dry-run — nothing was written)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())