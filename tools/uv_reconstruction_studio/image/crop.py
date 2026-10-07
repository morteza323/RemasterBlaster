"""
Cropping and context padding (spec §19, §56-57).

`crop_with_padding` is the AI-input side of context expansion; the
companion `core_region_offset` tells the caller exactly where, inside
that padded crop, the *original* (non-padded) region sits -- because
only that core region should normally be composited back into the
final texture (spec §19's explicit warning).
"""

from __future__ import annotations

from typing import Tuple

from PIL import Image

from core.models.part import Bounds


def clamp_bounds_to_image(bounds: Bounds, image_width: int, image_height: int) -> Bounds:
    """Shrinks `bounds` so it never reads past the image edge. Used
    after padding, since Bounds.padded() only clamps the low (x/y)
    side, not the high side."""
    x = min(bounds.x, max(0, image_width - 1))
    y = min(bounds.y, max(0, image_height - 1))
    width = min(bounds.width, image_width - x)
    height = min(bounds.height, image_height - y)
    if width <= 0 or height <= 0:
        raise ValueError(
            f"Bounds {bounds} falls entirely outside a {image_width}x{image_height} image"
        )
    return Bounds(x=x, y=y, width=width, height=height)


def crop_region(image: Image.Image, bounds: Bounds) -> Image.Image:
    return image.crop((bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height))


def crop_with_padding(
    image: Image.Image, bounds: Bounds, padding: int
) -> Tuple[Image.Image, Bounds]:
    """Returns (padded_crop, actual_padded_bounds). `actual_padded_bounds`
    may be smaller than a naive `bounds.padded(padding)` if the source
    region sits near an image edge."""
    padded = bounds.padded(padding) if padding > 0 else bounds
    clamped = clamp_bounds_to_image(padded, image.width, image.height)
    return crop_region(image, clamped), clamped


def core_region_offset(original: Bounds, padded_clamped: Bounds) -> Bounds:
    """Where `original` sits *within* a padded crop's local (0,0)-relative
    coordinate space -- i.e. the sub-rectangle of the AI's output that
    should actually be composited back (spec §19)."""
    offset_x = original.x - padded_clamped.x
    offset_y = original.y - padded_clamped.y
    if offset_x < 0 or offset_y < 0:
        raise ValueError("original bounds are not contained within padded_clamped bounds")
    if offset_x + original.width > padded_clamped.width or offset_y + original.height > padded_clamped.height:
        raise ValueError("original bounds extend beyond padded_clamped bounds")
    return Bounds(x=offset_x, y=offset_y, width=original.width, height=original.height)
