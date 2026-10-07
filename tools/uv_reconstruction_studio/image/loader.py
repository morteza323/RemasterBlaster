"""
Image loading (spec §60, §64). `load_image` forces decode immediately
(`.load()`) so a corrupt file fails loudly at import time rather than
lazily, later, inside some unrelated operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

from PIL import Image


@dataclass
class ImageInfo:
    width: int
    height: int
    format: str
    mode: str
    has_alpha: bool


def load_image(path: Union[str, Path]) -> Image.Image:
    img = Image.open(path)
    img.load()
    return img


def get_image_info(path: Union[str, Path]) -> ImageInfo:
    with Image.open(path) as img:
        return ImageInfo(
            width=img.width,
            height=img.height,
            format=img.format or "",
            mode=img.mode,
            has_alpha=img.mode in ("RGBA", "LA") or "transparency" in img.info,
        )
