"""
Optional separate RealESRGAN x4 pass over already-processed Flux outputs.
Run AFTER the main Flux batch is done if you want 4x final resolution.

Usage:
  python scripts/esrgans_batch.py --input "H:/gta sa textures/upscaled" --output "H:/gta sa textures/upscaled_x4"
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_SD_CLI = r"H:\flux2\sd-master-6b3edaa-bin-win-cuda12-x64\sd-cli.exe"
DEFAULT_ESRGAN = r"H:\flux2\sd-master-6b3edaa-bin-win-cuda12-x64\RealESRGAN_x4plus.safetensors"


def main():
    p = argparse.ArgumentParser(description="Batch RealESRGAN x4 on existing textures")
    p.add_argument("--input", required=True, help="Folder of Flux-upscaled textures")
    p.add_argument("--output", required=True, help="Output folder for 4x results")
    p.add_argument("--sd-cli", default=DEFAULT_SD_CLI)
    p.add_argument("--upscale-model", default=DEFAULT_ESRGAN)
    p.add_argument("--tile", type=int, default=128)
    args = p.parse_args()

    inp = Path(args.input)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    sd = Path(args.sd_cli)
    model = Path(args.upscale_model)
    if not sd.is_file():
        print(f"sd-cli not found: {sd}")
        return 1
    if not model.is_file():
        print(f"ESRGAN model not found: {model}")
        return 1

    files = [f for f in inp.rglob("*") if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp"}]
    print(f"Found {len(files)} images")
    for i, src in enumerate(files, 1):
        rel = src.relative_to(inp)
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            print(f"[{i}/{len(files)}] skip {rel}")
            continue
        # sd-cli upscale mode
        cmd = [
            str(sd),
            "-M", "upscale",
            "--upscale-model", str(model),
            "--upscale-tile-size", str(args.tile),
            "-i", str(src),
            "-o", str(dst),
        ]
        print(f"[{i}/{len(files)}] {rel} ...", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            print(f"  FAIL: {(r.stderr or r.stdout)[-500:]}")
        else:
            print(f"  OK → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
