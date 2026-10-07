#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
  CALIBRATE QA TOOL – fit the scorer to YOUR labeled examples
================================================================================

Why this exists
----------------
The built-in scorer in qa_texture_tool.py used hand-guessed thresholds
(e.g. "SSIM < 0.25 = bad"). Those numbers were never checked against your
actual art style, so they can end up backwards — good creative upscales
(which SHOULD look quite different from a blurry low-res original) get
penalized, while some bad ones slip through.

This script fixes that by fitting a small logistic-regression model to
examples YOU have already sorted by hand: a folder of upscales you know
are good, and a folder of upscales you know are bad. It reuses the exact
same feature-extraction code as the main tool, so the result is guaranteed
to match what the tool measures at runtime.

USAGE
-----
  python calibrate_qa.py --originals "D:/orig" --good "D:/examples_good" --bad "D:/examples_bad"

  --originals   Folder with the original (low-res) textures, matched by
                filename (case-insensitive, extension ignored) — same
                matching rule as the main tool.
  --good        Folder containing ONLY upscaled files you are confident
                are GOOD.
  --bad         Folder containing ONLY upscaled files you are confident
                are BAD.

These can just be copies/shortcuts of a few dozen files each pulled out of
your existing upscaled folder — they don't need to be a special format.

OUTPUT
------
  qa_calibration.json, written next to your qa_texture_tool.py script.
  The main tool auto-detects and uses it on the next run (GUI or -auto) —
  no other changes needed. Delete the file to go back to the old heuristic.

TIPS FOR GOOD RESULTS
----------------------
  • 20-40 examples of each is a reasonable minimum; more is better,
    especially if your bad upscales fail in more than one way (e.g. some
    are blurry messes, some are "different object" hallucinations —
    include several of each kind).
  • If training accuracy comes back below ~90%, look at which specific
    files it still gets wrong (this script does not print per-file
    ambiguity — you can improve results fastest by adding more examples
    like the ones you're least confident about).
  • Re-run this script any time you add more labeled examples; it just
    overwrites qa_calibration.json.
================================================================================
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def _load_main_module():
    """Import qa_texture_tool.py from the same folder as this script."""
    candidates = sorted(
        p for p in HERE.glob("qa_texture_tool*.py")
        if p.name != Path(__file__).name
    )
    if not candidates:
        print("ERROR: couldn't find a qa_texture_tool*.py file next to calibrate_qa.py.")
        print("Put this script in the same folder as your main tool script and try again.")
        sys.exit(1)
    main_path = candidates[0]
    spec = importlib.util.spec_from_file_location("qa_main", main_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qa_main"] = mod  # required so @dataclass can resolve the module
    spec.loader.exec_module(mod)
    return mod, main_path


def _collect(folder: Path) -> dict:
    m = {}
    for p in Path(folder).rglob("*"):
        if p.is_file():
            key = p.stem.lower()
            if key not in m:
                m[key] = p
    return m


def _build_rows(mod, orig_map: dict, folder: str, label: int, feature_order):
    up_map = _collect(Path(folder))
    rows = []
    skipped_no_match = 0
    skipped_error = 0
    for stem, up_path in up_map.items():
        op = orig_map.get(stem)
        if op is None:
            skipped_no_match += 1
            continue
        try:
            with mod.Image.open(op) as o_im:
                o = o_im.convert("RGBA").copy()
            with mod.Image.open(up_path) as u_im:
                u = u_im.convert("RGBA").copy()
            pair = mod.TexturePair(
                name=stem, original_path=op, upscaled_path=up_path,
                orig_w=o.size[0], orig_h=o.size[1],
                up_w=u.size[0], up_h=u.size[1],
            )
            feats = mod.extract_raw_features(o, u, pair)
            if feats.get("degenerate"):
                skipped_error += 1
                continue
            rows.append(([feats[k] for k in feature_order], label))
        except Exception as e:
            skipped_error += 1
            print(f"  skip {stem}: {e}")
    if skipped_no_match:
        print(f"  ({skipped_no_match} files had no matching original in '{folder}' — skipped)")
    if skipped_error:
        print(f"  ({skipped_error} files failed to analyze — skipped)")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--originals", required=True, help="Folder of original (low-res) textures")
    ap.add_argument("--good", required=True, help="Folder of upscaled files known to be GOOD")
    ap.add_argument("--bad", required=True, help="Folder of upscaled files known to be BAD")
    ap.add_argument("--epochs", type=int, default=4000, help="Training iterations (default 4000)")
    ap.add_argument("--lr", type=float, default=0.5, help="Learning rate (default 0.5)")
    ap.add_argument("--l2", type=float, default=0.02, help="L2 regularization strength (default 0.02)")
    args = ap.parse_args()

    mod, main_path = _load_main_module()
    print(f"Using tool module: {main_path.name}\n")

    print("Scanning originals folder...")
    orig_map = _collect(Path(args.originals))
    print(f"  {len(orig_map)} original files found\n")

    feature_order = ["ratio", "ssim", "local_min", "perc", "perc2", "mean_dist", "lap_var_log"]

    print("Warming up perceptual model (AlexNet, one-time load)...")
    mod._get_perceptual_net()
    print()

    print("Extracting features from GOOD examples...")
    good_rows = _build_rows(mod, orig_map, args.good, 1, feature_order)
    print(f"  -> {len(good_rows)} usable good examples\n")

    print("Extracting features from BAD examples...")
    bad_rows = _build_rows(mod, orig_map, args.bad, 0, feature_order)
    print(f"  -> {len(bad_rows)} usable bad examples\n")

    if len(good_rows) < 6 or len(bad_rows) < 6:
        print("ERROR: need at least ~6-10 usable examples of EACH class to calibrate reliably.")
        print("Add more labeled files (ideally 20-40+ of each) and try again.")
        sys.exit(1)

    X = np.array([r[0] for r in good_rows + bad_rows], dtype=np.float64)
    y = np.array([r[1] for r in good_rows + bad_rows], dtype=np.float64)

    mu = X.mean(axis=0)
    sigma = X.std(axis=0)
    sigma[sigma < 1e-6] = 1.0
    Xs = (X - mu) / sigma

    # Logistic regression via plain gradient descent (no sklearn dependency).
    n, d = Xs.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(args.epochs):
        z = np.clip(Xs @ w + b, -60, 60)
        p = 1.0 / (1.0 + np.exp(-z))
        grad_w = Xs.T @ (p - y) / n + args.l2 * w
        grad_b = float(np.mean(p - y))
        w -= args.lr * grad_w
        b -= args.lr * grad_b

    z = np.clip(Xs @ w + b, -60, 60)
    p = 1.0 / (1.0 + np.exp(-z))
    pred = (p >= 0.5).astype(int)
    acc = float((pred == y).mean())
    tp = int(((pred == 1) & (y == 1)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())

    print("=" * 60)
    print(f"Training accuracy: {acc*100:.1f}%")
    print(f"  Good correctly kept : {tp}/{tp+fn}")
    print(f"  Bad correctly caught: {tn}/{tn+fp}")
    if fp:
        print(f"  WARNING: {fp} bad example(s) would still be scored as good")
    if fn:
        print(f"  WARNING: {fn} good example(s) would still be scored as bad")
    if acc < 0.90:
        print()
        print("  Below 90% — add more labeled examples (especially ones similar")
        print("  to your trickiest cases) and re-run this script.")
    print("=" * 60)

    calibration = {
        "feature_order": feature_order,
        "mu": mu.tolist(),
        "sigma": sigma.tolist(),
        "weights": w.tolist(),
        "bias": b,
        "n_good": len(good_rows),
        "n_bad": len(bad_rows),
        "train_accuracy": acc,
    }

    out_path = main_path.parent / "qa_calibration.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(calibration, f, indent=2)

    print(f"\nSaved -> {out_path}")
    print("The main tool will pick this up automatically next run (GUI or -auto).")
    print("Use threshold 50 (the calibrated score is a 0-100 'probability of good',")
    print("so 50 is the natural cutoff) — e.g. --threshold 50 in -auto mode.")


if __name__ == "__main__":
    main()
