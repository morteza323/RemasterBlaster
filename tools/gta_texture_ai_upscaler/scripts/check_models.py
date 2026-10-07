#!/usr/bin/env python3
"""Quick check that required model files exist."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.config import load_config

def main():
    cfg = load_config(project_root=ROOT)
    print("Checking model files...\n")
    all_ok = True
    for label, rel in [
        ("Diffusion GGUF", cfg.models.diffusion_gguf),
        ("Text Encoder", cfg.models.text_encoder),
        ("VAE", cfg.models.vae),
    ]:
        p = cfg.resolve_path(rel)
        ok = p.exists()
        size = f"{p.stat().st_size / (1024**3):.2f} GB" if ok else "—"
        status = "OK" if ok else "MISSING"
        print(f"  [{status:7}] {label:20} {p}  ({size})")
        if not ok:
            all_ok = False
    print()
    if all_ok:
        print("All model files present.")
    else:
        print("Some files are missing. Download them into the models/ folder.")
        print("See README.md for exact links.")
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
