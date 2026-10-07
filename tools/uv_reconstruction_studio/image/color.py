"""
Color mode / bit depth / alpha handling (spec §58-59).

The rule from the spec is explicit: never silently change color space
or premultiply alpha; any conversion must be deliberate. Every
function here does exactly one named conversion and nothing implicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Optional

import numpy as np
from PIL import Image

_BIT_DEPTH_BY_MODE = {
    "1": 1, "L": 8, "P": 8, "RGB": 8, "RGBA": 8, "LA": 8,
    "I": 32, "F": 32, "I;16": 16,
}


@dataclass
class ColorInfo:
    mode: str
    bit_depth: int
    has_alpha: bool
    icc_profile: Optional[bytes]


def inspect_color(image: Image.Image) -> ColorInfo:
    return ColorInfo(
        mode=image.mode,
        bit_depth=_BIT_DEPTH_BY_MODE.get(image.mode, 8),
        has_alpha=image.mode in ("RGBA", "LA"),
        icc_profile=image.info.get("icc_profile"),
    )


def get_alpha_channel(image: Image.Image) -> Optional[Image.Image]:
    if image.mode in ("RGBA", "LA"):
        return image.split()[-1]
    return None


@unique
class AlphaMode(str, Enum):
    PRESERVE_ORIGINAL = "preserve_original"  # keep the source texture's alpha
    REPLACE = "replace"                      # use whatever alpha the new image carries
    COMPOSITE = "composite"                  # multiply original * reconstructed alpha
    IGNORE = "ignore"                        # drop alpha entirely, output RGB


def apply_alpha_mode(original: Image.Image, reconstructed: Image.Image, mode: AlphaMode) -> Image.Image:
    """Combine an original texture's alpha with a reconstructed patch's
    alpha per the selected policy (spec §58). Returns a NEW image;
    never mutates either input."""
    if mode == AlphaMode.IGNORE:
        return reconstructed.convert("RGB")

    original_alpha = get_alpha_channel(original)

    if mode == AlphaMode.PRESERVE_ORIGINAL:
        if original_alpha is None:
            return reconstructed.convert("RGB")
        out = reconstructed.convert("RGB")
        alpha = original_alpha if original_alpha.size == out.size else original_alpha.resize(out.size)
        out.putalpha(alpha)
        return out

    if mode == AlphaMode.REPLACE:
        recon_alpha = get_alpha_channel(reconstructed)
        if recon_alpha is None:
            # Nothing to replace WITH -- fall back to fully opaque rather
            # than silently reusing the original's alpha (that would be
            # PRESERVE_ORIGINAL behavior under a different name).
            return reconstructed.convert("RGBA")
        return reconstructed.convert("RGBA")

    if mode == AlphaMode.COMPOSITE:
        recon_alpha = get_alpha_channel(reconstructed)
        out = reconstructed.convert("RGB")
        if original_alpha is None and recon_alpha is None:
            return out.convert("RGBA")
        if original_alpha is None:
            out.putalpha(recon_alpha)
            return out
        if recon_alpha is None:
            alpha = original_alpha if original_alpha.size == out.size else original_alpha.resize(out.size)
            out.putalpha(alpha)
            return out
        a1 = np.asarray(original_alpha.resize(out.size), dtype=np.float32) / 255.0
        a2 = np.asarray(recon_alpha, dtype=np.float32) / 255.0
        combined = np.clip(a1 * a2 * 255.0, 0, 255).astype(np.uint8)
        out.putalpha(Image.fromarray(combined, mode="L"))
        return out

    raise ValueError(f"Unknown alpha mode: {mode}")


def convert_mode(image: Image.Image, mode: str) -> Image.Image:
    """Explicit, logged-by-caller color mode conversion -- the only
    sanctioned way to change an image's mode in this codebase."""
    return image.convert(mode)
