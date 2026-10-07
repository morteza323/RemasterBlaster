#!/usr/bin/env python3
"""
LOD Maker for GTA SA texture packs
==================================
Compares ORIGINAL vs UPSCALED folders, finds LOD textures that exist in
original but are missing (or still low-res) in upscaled, then generates
them by downscaling a matching high-res upscaled source.

Logic:
  1. Index every PNG in original and upscaled (recursive).
  2. Detect LOD stems (name contains 'lod' as a token / substring patterns).
  3. For each original LOD file whose stem is NOT in upscaled (or force-rebuild):
       - Extract a "core" name by stripping LOD prefixes/suffixes.
       - Find best upscaled source: exact core, or *_{core}, or contains core,
         preferring non-LOD files with largest area.
       - Resize to ~1/4 of source (configurable), snap to power-of-2 sides.
       - Save under the ORIGINAL LOD filename into OUTPUT folder.

Usage (GUI or CLI):
  python lod_maker.py
  python lod_maker.py "H:\\original_textures" "H:\\upscaled" "H:\\upscaled_lods"

Requires: Pillow
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    from PIL import Image
except ImportError:
    print('Pillow required: pip install Pillow')
    raise SystemExit(1)

ALLOWED_SIDES = (32, 64, 128, 256, 512, 1024, 2048)
DEFAULT_DIVISOR = 4  # 1/4 of high-res side
MIN_SIDE = 32
MAX_SIDE = 512  # LODs should stay modest


def is_pow2(n: int) -> bool:
    return n >= 1 and (n & (n - 1)) == 0


def nearest_allowed(n: int, prefer_down: bool = True) -> int:
    n = max(MIN_SIDE, min(int(n), MAX_SIDE))
    best = ALLOWED_SIDES[0]
    best_d = abs(n - best)
    for s in ALLOWED_SIDES:
        if s > MAX_SIDE:
            break
        d = abs(n - s)
        if d < best_d or (d == best_d and prefer_down and s < best):
            best, best_d = s, d
    return max(MIN_SIDE, best)


def is_lod_name(stem: str) -> bool:
    """True if filename looks like an LOD texture."""
    s = stem.casefold()
    # token-style
    tokens = re.split(r'[_\-\s]+', s)
    if any(t == 'lod' or t.startswith('lod') or t.endswith('lod') for t in tokens):
        return True
    # embedded patterns
    if re.search(r'(^|_)lod($|_|[0-9])', s):
        return True
    if 'lod' in s and (
        s.startswith('lod') or '_lod' in s or 'lod_' in s or s.endswith('lod')
    ):
        return True
    return False


def extract_cores(stem: str) -> List[str]:
    """
    Produce candidate 'core' texture names from an LOD stem.
    Examples:
      cs_lod_grasstype10          -> grasstype10, cs_grasstype10
      lodcunty_grasstype10        -> grasstype10, cunty_grasstype10
      lodcunty_grasstype10_4blend -> grasstype10_4blend, grasstype10
      roads_lahills_sjmhoodlawn41_lod -> sjmhoodlawn41, roads_lahills_sjmhoodlawn41
    """
    s = stem
    low = s.casefold()
    cores: List[str] = []

    # strip common LOD prefixes
    stripped = s
    for prefix in (
        'cs_lod_', 'cs_lod', 'lodcunty_', 'lodcunty', 'lod_', 'lod',
        'lahills_lod_', 'lahills_lod', 'ce_lod_', 'ce_lod',
        'vgn_lod_', 'vgs_lod_', 'sf_lod_', 'la_lod_',
    ):
        if low.startswith(prefix.casefold()):
            stripped = s[len(prefix):]
            break

    # remove _lod / lod_ / trailing LOD / embedded
    stripped2 = re.sub(r'(?i)(^|_)lod(_|$)', r'\1\2', stripped)
    stripped2 = re.sub(r'(?i)_lod_', '_', stripped2)
    stripped2 = re.sub(r'(?i)^lod_', '', stripped2)
    stripped2 = re.sub(r'(?i)_lod$', '', stripped2)
    stripped2 = re.sub(r'(?i)lod$', '', stripped2)  # Grass_128HVLOD -> Grass_128HV
    stripped2 = re.sub(r'_{2,}', '_', stripped2).strip('_')

    if stripped2:
        cores.append(stripped2)

    # also take last 1–3 underscore tokens (often the real texture name)
    parts = [p for p in re.split(r'[_\-]+', stripped2) if p]
    if parts:
        cores.append(parts[-1])
        if len(parts) >= 2:
            cores.append('_'.join(parts[-2:]))
        if len(parts) >= 3:
            cores.append('_'.join(parts[-3:]))

    # unique preserve order, casefold keys later
    seen = set()
    out = []
    for c in cores:
        k = c.casefold()
        if k and k not in seen and not is_lod_name(c):
            seen.add(k)
            out.append(c)
        elif k and k not in seen:
            seen.add(k)
            out.append(c)
    return out


def index_pngs(root: Path) -> Dict[str, List[Path]]:
    """stem.casefold() -> list of paths"""
    by: Dict[str, List[Path]] = defaultdict(list)
    if not root.is_dir():
        return by
    for p in root.rglob('*.png'):
        if p.is_file():
            by[p.stem.casefold()].append(p)
    return by


def pick_best_source(
    cores: List[str],
    upscaled: Dict[str, List[Path]],
    all_up_stems: List[str],
) -> Optional[Path]:
    """Prefer exact core match (non-LOD), then suffix, then contains."""
    candidates: List[Tuple[int, int, Path]] = []  # score, area, path

    def area_of(p: Path) -> int:
        try:
            with Image.open(p) as im:
                w, h = im.size
                return w * h
        except Exception:
            return 0

    for core in cores:
        ck = core.casefold()
        # 1) exact
        for p in upscaled.get(ck, []):
            if is_lod_name(p.stem):
                score = 50
            else:
                score = 100
            candidates.append((score, area_of(p), p))

        # 2) ends with _core
        suf = '_' + ck
        for stem, paths in upscaled.items():
            if stem.endswith(suf) and not is_lod_name(stem):
                for p in paths:
                    candidates.append((80, area_of(p), p))
            elif stem.endswith(suf):
                for p in paths:
                    candidates.append((40, area_of(p), p))

        # 3) contains core as token (len >= 5)
        if len(ck) >= 5:
            for stem, paths in upscaled.items():
                tokens = stem.replace('-', '_').split('_')
                if ck in tokens and not is_lod_name(stem):
                    for p in paths:
                        candidates.append((60, area_of(p), p))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], -x[1], str(x[2]).casefold()))
    return candidates[0][2]


def make_lod_image(src: Path, divisor: int = DEFAULT_DIVISOR) -> Image.Image:
    im = Image.open(src).convert('RGBA')
    w, h = im.size
    tw = nearest_allowed(max(MIN_SIDE, w // divisor), prefer_down=True)
    th = nearest_allowed(max(MIN_SIDE, h // divisor), prefer_down=True)
    # keep aspect roughly
    if w >= 4 and h >= 4:
        ar = w / float(h)
        cur = tw / float(th) if th else 1.0
        if abs(cur - ar) > 0.15:
            if cur > ar:
                th = nearest_allowed(max(MIN_SIDE, int(round(tw / ar))))
            else:
                tw = nearest_allowed(max(MIN_SIDE, int(round(th * ar))))
    if (tw, th) != im.size:
        im = im.resize((tw, th), Image.Resampling.LANCZOS)
    return im


def run(
    original: Path,
    upscaled: Path,
    output: Path,
    divisor: int = DEFAULT_DIVISOR,
    force: bool = False,
    log=print,
) -> dict:
    original = original.resolve()
    upscaled = upscaled.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    log(f'Original : {original}')
    log(f'Upscaled : {upscaled}')
    log(f'Output   : {output}')
    log(f'Divisor  : 1/{divisor}  (LOD size ≈ high-res / {divisor})')
    log('Indexing PNGs...')

    orig_idx = index_pngs(original)
    up_idx = index_pngs(upscaled)
    log(f'  Original PNGs : {sum(len(v) for v in orig_idx.values()):,}')
    log(f'  Upscaled PNGs : {sum(len(v) for v in up_idx.values()):,}')

    # LOD files present in original
    orig_lods = {stem: paths for stem, paths in orig_idx.items() if is_lod_name(stem)}
    log(f'  LOD stems in original: {len(orig_lods):,}')

    stats = {
        'orig_lods': len(orig_lods),
        'already_in_upscaled': 0,
        'generated': 0,
        'no_source': 0,
        'errors': 0,
        'skipped_force_off': 0,
    }
    missing_report: List[str] = []
    generated_report: List[str] = []
    no_source_report: List[str] = []

    all_up_stems = list(up_idx.keys())

    for i, (stem, paths) in enumerate(sorted(orig_lods.items()), 1):
        # original filename to recreate
        src_orig = paths[0]
        out_name = src_orig.name  # keep exact original casing/name

        if stem in up_idx and not force:
            stats['already_in_upscaled'] += 1
            stats['skipped_force_off'] += 1
            continue

        cores = extract_cores(src_orig.stem)
        source = pick_best_source(cores, up_idx, all_up_stems)
        if source is None:
            stats['no_source'] += 1
            no_source_report.append(f'{out_name}  cores={cores}')
            if i % 200 == 0:
                log(f'  [{i}/{len(orig_lods)}] ...')
            continue

        try:
            lod_im = make_lod_image(source, divisor=divisor)
            dest = output / out_name
            # if name collision in output, still overwrite intentionally
            lod_im.save(dest, format='PNG', optimize=True)
            stats['generated'] += 1
            generated_report.append(
                f'{out_name}  <-  {source.name}  ({lod_im.size[0]}x{lod_im.size[1]})'
            )
            if stats['generated'] <= 30 or stats['generated'] % 100 == 0:
                log(f'  GEN {out_name} <- {source.name} → {lod_im.size[0]}x{lod_im.size[1]}')
        except Exception as e:
            stats['errors'] += 1
            log(f'  ERR {out_name}: {e}')

    # reports
    (output / '_lod_generated.txt').write_text(
        f'Generated LODs: {stats["generated"]}\n\n' + '\n'.join(generated_report) + '\n',
        encoding='utf-8',
    )
    (output / '_lod_no_source.txt').write_text(
        f'No upscaled source found: {stats["no_source"]}\n\n'
        + '\n'.join(no_source_report) + '\n',
        encoding='utf-8',
    )

    log('')
    log('=== SUMMARY ===')
    log(f'  LOD stems in original     : {stats["orig_lods"]:,}')
    log(f'  Already present in upscaled: {stats["already_in_upscaled"]:,}')
    log(f'  Generated                 : {stats["generated"]:,}')
    log(f'  No source found           : {stats["no_source"]:,}')
    log(f'  Errors                    : {stats["errors"]:,}')
    log(f'  Output folder             : {output}')
    log(f'  Report: _lod_generated.txt / _lod_no_source.txt')
    log('')
    log('Next: copy generated PNGs into your upscaled folder, then re-run the TXD mirror.')
    return stats


def main_gui() -> None:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.title('GTA SA — LOD Maker')
    root.geometry('820x560')

    orig_v = tk.StringVar(value=r'H:\gta sa textures\original')
    up_v = tk.StringVar(value=r'H:\upscaled')
    out_v = tk.StringVar(value=r'H:\upscaled_lods')
    div_v = tk.IntVar(value=DEFAULT_DIVISOR)
    force_v = tk.BooleanVar(value=False)

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill='both', expand=True)

    ttk.Label(
        frm,
        text='LOD Maker: builds missing LOD PNGs from high-res upscaled sources (≈1/4 size)',
        font=('', 9, 'italic'),
    ).pack(anchor='w', pady=(0, 8))

    def row(label, var, browse_dir=True):
        f = ttk.Frame(frm)
        f.pack(fill='x', pady=3)
        ttk.Label(f, text=label, width=18).pack(side='left')
        ttk.Entry(f, textvariable=var).pack(side='left', fill='x', expand=True, padx=4)

        def br():
            p = filedialog.askdirectory(title=label)
            if p:
                var.set(p)

        ttk.Button(f, text='Browse...', command=br, width=10).pack(side='left')

    row('Original textures', orig_v)
    row('Upscaled textures', up_v)
    row('Output LODs', out_v)

    f2 = ttk.Frame(frm)
    f2.pack(fill='x', pady=6)
    ttk.Label(f2, text='Size divisor', width=18).pack(side='left')
    ttk.Spinbox(f2, from_=3, to=8, textvariable=div_v, width=6).pack(side='left')
    ttk.Label(f2, text='  (4 = quarter size, 5 = ~1/5)').pack(side='left')
    ttk.Checkbutton(f2, text='Force rebuild even if LOD exists in upscaled', variable=force_v).pack(
        side='left', padx=16
    )

    txt = tk.Text(frm, height=20, wrap='none')
    txt.pack(fill='both', expand=True, pady=8)
    status = tk.StringVar(value='Ready')
    ttk.Label(frm, textvariable=status).pack(anchor='w')

    def log(s: str) -> None:
        root.after(0, lambda: (txt.insert('end', s + '\n'), txt.see('end')))

    running = {'v': False}

    def start() -> None:
        if running['v']:
            return
        running['v'] = True
        status.set('Running...')
        txt.delete('1.0', 'end')

        def job():
            try:
                st = run(
                    Path(orig_v.get()),
                    Path(up_v.get()),
                    Path(out_v.get()),
                    divisor=int(div_v.get()),
                    force=bool(force_v.get()),
                    log=log,
                )
                root.after(
                    0,
                    lambda: (
                        status.set('DONE'),
                        messagebox.showinfo(
                            'Done',
                            f"Generated: {st['generated']:,}\n"
                            f"No source: {st['no_source']:,}\n"
                            f"Already had: {st['already_in_upscaled']:,}",
                        ),
                    ),
                )
            except Exception as e:
                root.after(0, lambda: (status.set('Failed'), messagebox.showerror('Error', str(e))))
            finally:
                running['v'] = False

        threading.Thread(target=job, daemon=True).start()

    ttk.Button(frm, text='START', command=start).pack(anchor='w', pady=4)
    root.mainloop()


def main() -> int:
    if len(sys.argv) >= 4:
        ap = argparse.ArgumentParser(description='GTA SA LOD Maker')
        ap.add_argument('original')
        ap.add_argument('upscaled')
        ap.add_argument('output')
        ap.add_argument('--divisor', type=int, default=DEFAULT_DIVISOR)
        ap.add_argument('--force', action='store_true')
        args = ap.parse_args()
        run(Path(args.original), Path(args.upscaled), Path(args.output),
            divisor=args.divisor, force=args.force)
        return 0
    main_gui()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
