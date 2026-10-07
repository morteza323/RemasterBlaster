#!/usr/bin/env python3
"""
Remaster Blaster - Simple Launcher
==================================
Lists all available tools and lets you open the folder or run common ones.
"""

import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
TOOLS = ROOT / "tools"

TOOLS_INFO = [
    ("gta_texture_ai_upscaler", "Main AI Upscaler (Flux)", "main.py"),
    ("uv_reconstruction_studio", "UV Reconstruction Studio (GUI + CLI)", "main.py"),
    ("txd_mirror_builder", "TXD Mirror Builder", None),
    ("qa_tool", "Texture QA Tool", "qa_texture_tool.py"),
    ("san_resize_tool", "SAN Resize / Dimension Fixer", "san_resize_tool.py"),
    ("lod_maker", "LOD Maker", "lod_maker.py"),
    ("img_prefix_fixer", "IMG Prefix Fixer", "img_prefix_fixer.py"),
    ("img_texture_checker", "IMG Texture Checker", "img_texture_checker.py"),
    ("scan_missing", "Scan Missing Textures", "scan_missing.py"),
    ("remove_duplicated", "Remove Already-Fixed Files", "remove_duplicated.py"),
    ("copy_skipped", "Copy SKIPPED/FAILED to Need-to-Fix", "copy_skipped_to_need_to_fix.py"),
    ("filename_keywords", "Filename Keywords Analyzer", None),
    ("fast_renamer", "Fast Renamer", "fast_renamer.py"),
    ("fast_upscaler", "Fast Upscaler (no diffusion)", "gta_batch_upscaler.py"),
    ("texture_dimension_fixer", "Texture Dimension Fixer (from log)", "fix_gta_sa_textures.py"),
    ("find_missing_png", "Find Missing / Wrong Size PNGs", "scan_png_sizes.py"),
]


def main():
    print("=" * 70)
    print("  REMASTER BLASTER - GTA SA Texture Toolkit")
    print("=" * 70)
    print()

    for i, (folder, desc, entry) in enumerate(TOOLS_INFO, 1):
        path = TOOLS / folder
        status = "OK" if path.exists() else "MISSING"
        print(f"  {i:2d}. [{status}] {desc}")
        print(f"      → tools/{folder}/")
        if entry:
            print(f"      Run: python tools/{folder}/{entry}")
        print()

    print("-" * 70)
    print("Tip: Open any tool folder and read its README.md for detailed usage.")
    print("Most tools still have hard-coded paths — edit them before running.")
    print("=" * 70)

    if sys.platform == "win32":
        try:
            input("\nPress Enter to open the tools folder in Explorer...")
            os.startfile(TOOLS)
        except Exception:
            pass


if __name__ == "__main__":
    main()
