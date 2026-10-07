"""
Final export (spec §92): PNG, preserving dimensions/alpha/color mode
per the chosen AlphaMode. Configurable filename -- this module doesn't
invent one, the caller decides (e.g. "character_body_RECONSTRUCTED.png").
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from PIL import Image

from image.color import AlphaMode


def export_texture(image: Image.Image, path: Union[str, Path], alpha_mode: AlphaMode = AlphaMode.PRESERVE_ORIGINAL) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    out_image = image
    if alpha_mode == AlphaMode.IGNORE and out_image.mode != "RGB":
        out_image = out_image.convert("RGB")

    out_image.save(path, format="PNG")
    return path
