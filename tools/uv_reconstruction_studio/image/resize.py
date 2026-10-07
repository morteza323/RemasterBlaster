"""
Resizing (spec §12: nearest-neighbor preview vs. high-quality preview).
"""

from __future__ import annotations

from enum import Enum, unique

from PIL import Image


@unique
class ResizeMethod(str, Enum):
    NEAREST = "nearest"        # pixel-perfect view
    HIGH_QUALITY = "high_quality"  # LANCZOS, for zoomed-out/export previews


_FILTER_MAP = {
    ResizeMethod.NEAREST: Image.NEAREST,
    ResizeMethod.HIGH_QUALITY: Image.LANCZOS,
}


def resize_image(image: Image.Image, width: int, height: int,
                  method: ResizeMethod = ResizeMethod.HIGH_QUALITY) -> Image.Image:
    if width <= 0 or height <= 0:
        raise ValueError(f"Target size must be positive, got {width}x{height}")
    return image.resize((width, height), _FILTER_MAP[method])


def scale_image(image: Image.Image, scale: float,
                 method: ResizeMethod = ResizeMethod.HIGH_QUALITY) -> Image.Image:
    if scale <= 0:
        raise ValueError(f"Scale must be positive, got {scale}")
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    return resize_image(image, width, height, method)
