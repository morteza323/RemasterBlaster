"""
Mask primitives (spec §28: show/hide/invert/clear/fill/feather/expand/
contract). The interactive brush/eraser/gradient painting UI itself is
Phase 12 (masking editor); these are the non-destructive image
operations that editor will call into, plus what §20's seam blending
needs right now.
"""

from __future__ import annotations

from PIL import Image, ImageFilter, ImageOps


def create_blank_mask(width: int, height: int, fill: int = 0) -> Image.Image:
    """`fill=0` -> nothing selected/affected; `fill=255` -> everything."""
    return Image.new("L", (width, height), fill)


def invert_mask(mask: Image.Image) -> Image.Image:
    return ImageOps.invert(mask.convert("L"))


def feather_mask(mask: Image.Image, radius: int) -> Image.Image:
    if radius <= 0:
        return mask.copy()
    return mask.filter(ImageFilter.GaussianBlur(radius=radius))


def expand_mask(mask: Image.Image, amount: int) -> Image.Image:
    """Grow the white (selected) region by ~`amount` px."""
    if amount <= 0:
        return mask.copy()
    size = 2 * amount + 1
    return mask.filter(ImageFilter.MaxFilter(size))


def contract_mask(mask: Image.Image, amount: int) -> Image.Image:
    """Shrink the white (selected) region by ~`amount` px."""
    if amount <= 0:
        return mask.copy()
    size = 2 * amount + 1
    return mask.filter(ImageFilter.MinFilter(size))


def apply_mask(base: Image.Image, overlay: Image.Image, mask: Image.Image) -> Image.Image:
    """Where mask is white, take `overlay`; where black, take `base`.
    Never mutates `base` or `overlay`."""
    base_rgba = base.convert("RGBA")
    overlay_rgba = overlay.convert("RGBA").resize(base_rgba.size) \
        if overlay.size != base_rgba.size else overlay.convert("RGBA")
    mask_l = mask.convert("L").resize(base_rgba.size) if mask.size != base_rgba.size else mask.convert("L")
    return Image.composite(overlay_rgba, base_rgba, mask_l)
