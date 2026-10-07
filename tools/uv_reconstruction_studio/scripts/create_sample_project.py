#!/usr/bin/env python3
"""
Sample project generator (spec §80): produces a small synthetic
texture and a ready-to-use project -- Load / Split / Queue / Preview /
Reassemble / Save -- without needing a real game texture or any AI
model installed. Useful for a first run, for demos, and as a fixture
for manual testing.

Usage:
    python3 scripts/create_sample_project.py [output_dir]

Then, e.g.:
    python3 main.py --project <output_dir>/sample_project
    python3 main.py --project <output_dir>/sample_project --process
    python3 main.py --project <output_dir>/sample_project --reassemble --output final.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli.commands import create_project_command  # noqa: E402


def _make_sample_texture(path: Path, size: int = 256) -> None:
    """A simple checkerboard-plus-shapes 'texture' -- enough visual
    structure to see guides/parts/reassembly do something, without
    needing real game art."""
    image = Image.new("RGBA", (size, size), (40, 40, 50, 255))
    draw = ImageDraw.Draw(image)

    tile = size // 8
    for row in range(8):
        for col in range(8):
            if (row + col) % 2 == 0:
                x0, y0 = col * tile, row * tile
                draw.rectangle([x0, y0, x0 + tile, y0 + tile], fill=(60, 60, 75, 255))

    draw.ellipse([size * 0.15, size * 0.15, size * 0.45, size * 0.45], fill=(180, 90, 60, 255))
    draw.rectangle([size * 0.55, size * 0.15, size * 0.9, size * 0.45], fill=(90, 140, 90, 255))
    draw.polygon(
        [(size * 0.5, size * 0.55), (size * 0.9, size * 0.9), (size * 0.1, size * 0.9)],
        fill=(90, 100, 180, 255),
    )
    image.save(path)


def main() -> int:
    output_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample_output")
    output_root.mkdir(parents=True, exist_ok=True)

    texture_path = output_root / "sample_texture.png"
    _make_sample_texture(texture_path)

    project_dir = output_root / "sample_project"
    rc = create_project_command(
        str(project_dir), str(texture_path), "Sample Project",
        guide_specs=["horizontal:128", "vertical:128"],
        generate=True, padding=16,
    )
    if rc != 0:
        return rc

    print(f"\nSample project ready at: {project_dir}")
    print("Try:")
    print(f"  python3 main.py --project {project_dir}")
    print(f"  python3 main.py --project {project_dir} --process")
    print(f"  python3 main.py --project {project_dir} --reassemble --output {output_root / 'final.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
