"""
Seam blending (spec §20, §56-58): compositing a reconstructed part
back into the full texture at its exact original coordinates, with a
soft feathered edge so the seam isn't visible. This is what the
Reassembly Engine (Phase 15) will call per-part; nothing here decides
*which* version of a part to use -- that's Part.selected_version(),
decided upstream.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from core.models.part import Bounds


def _feathered_region_mask(width: int, height: int, feather_radius: int) -> Image.Image:
    """A `width`x`height` mask, solid white in the interior, fading to
    black within `feather_radius` px of every edge."""
    if feather_radius <= 0:
        return Image.new("L", (width, height), 255)

    pad = feather_radius
    canvas = Image.new("L", (width + 2 * pad, height + 2 * pad), 0)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([pad, pad, pad + width - 1, pad + height - 1], fill=255)
    blurred = canvas.filter(ImageFilter.GaussianBlur(radius=max(1, feather_radius / 2)))
    return blurred.crop((pad, pad, pad + width, pad + height))


def feathered_composite(
    base: Image.Image, patch: Image.Image, region: Bounds, feather_radius: int = 0
) -> Image.Image:
    """Returns a NEW image: `base` with `patch` blended in at `region`.
    `patch` must already be exactly `region.width` x `region.height`
    (i.e. already cropped to the core region -- see image.crop.core_region_offset
    for pulling that out of a padded AI output first)."""
    if patch.size != (region.width, region.height):
        raise ValueError(
            f"patch size {patch.size} does not match region size "
            f"{(region.width, region.height)} -- crop to the core region first"
        )
    if region.x + region.width > base.width or region.y + region.height > base.height:
        raise ValueError(f"region {region} does not fit inside base image {base.size}")

    base_rgba = base.convert("RGBA")
    patch_rgba = patch.convert("RGBA")

    layer = Image.new("RGBA", base_rgba.size, (0, 0, 0, 0))
    layer.paste(patch_rgba, (region.x, region.y))

    mask = _feathered_region_mask(region.width, region.height, feather_radius)
    mask_full = Image.new("L", base_rgba.size, 0)
    mask_full.paste(mask, (region.x, region.y))

    return Image.composite(layer, base_rgba, mask_full)
