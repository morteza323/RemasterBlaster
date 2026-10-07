"""Detect empty / near-black / flat textures that Flux should skip."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

try:
    from PIL import Image
    import numpy as np
except ImportError:
    Image = None
    np = None


@dataclass
class SkipDecision:
    should_skip: bool
    reason: str = ""
    black_ratio: float = 0.0
    variance: float = 0.0


def analyze_texture(
    path: Path,
    black_threshold: float = 0.92,
    max_variance: float = 80.0,
    near_black: int = 18,
) -> SkipDecision:
    """
    Return whether this texture is unsuitable for generative Flux remaster.
    - near-all-black / empty UI frames
    - extremely flat solid-color logos with almost no detail
    """
    if Image is None or np is None:
        return SkipDecision(False, "pillow/numpy missing – no skip analysis")

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            # Downsample for speed
            im_small = im.resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(im_small, dtype=np.float32)
    except Exception as e:
        return SkipDecision(False, f"unreadable: {e}")

    # Per-pixel luminance
    lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    black_ratio = float((lum < near_black).mean())
    variance = float(lum.var())

    if black_ratio >= black_threshold:
        return SkipDecision(
            True,
            f"near_empty_black (black_ratio={black_ratio:.3f})",
            black_ratio,
            variance,
        )

    # Very flat image (solid color / simple logo) – low variance
    if variance < max_variance and black_ratio < 0.15:
        # solid bright flat logo
        return SkipDecision(
            True,
            f"flat_simple (variance={variance:.1f})",
            black_ratio,
            variance,
        )

    # Mostly black with tiny content still often hallucinates
    if black_ratio >= 0.85 and variance < 400:
        return SkipDecision(
            True,
            f"mostly_black_low_detail (black_ratio={black_ratio:.3f}, var={variance:.1f})",
            black_ratio,
            variance,
        )

    return SkipDecision(False, "ok", black_ratio, variance)
