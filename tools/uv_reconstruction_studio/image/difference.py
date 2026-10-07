"""
Difference visualization (spec §38): a diagnostic tool for the Preview
Workspace, NOT a quality score. Three modes as named in the spec:
absolute, structural, and edge difference.
"""

from __future__ import annotations

from enum import Enum, unique

import numpy as np
from PIL import Image, ImageChops


@unique
class DifferenceMode(str, Enum):
    ABSOLUTE = "absolute"
    STRUCTURAL = "structural"
    EDGE = "edge"


def _as_matched_rgb(a: Image.Image, b: Image.Image):
    a_rgb = a.convert("RGB")
    b_rgb = b.convert("RGB")
    if a_rgb.size != b_rgb.size:
        b_rgb = b_rgb.resize(a_rgb.size)
    return a_rgb, b_rgb


def compute_difference(a: Image.Image, b: Image.Image,
                        mode: DifferenceMode = DifferenceMode.ABSOLUTE) -> Image.Image:
    a_rgb, b_rgb = _as_matched_rgb(a, b)

    if mode == DifferenceMode.ABSOLUTE:
        return ImageChops.difference(a_rgb, b_rgb)

    if mode == DifferenceMode.STRUCTURAL:
        # Lightweight structural proxy: difference of locally-blurred
        # luminance, so large-scale shape/tone changes stand out over
        # pixel-level noise. (A full SSIM implementation is future work --
        # this avoids inventing an unverified "quality score", per spec §38.)
        import cv2
        gray_a = cv2.cvtColor(np.array(a_rgb), cv2.COLOR_RGB2GRAY).astype(np.float32)
        gray_b = cv2.cvtColor(np.array(b_rgb), cv2.COLOR_RGB2GRAY).astype(np.float32)
        blur_a = cv2.GaussianBlur(gray_a, (7, 7), 0)
        blur_b = cv2.GaussianBlur(gray_b, (7, 7), 0)
        diff = np.abs(blur_a - blur_b).astype(np.uint8)
        return Image.fromarray(diff).convert("RGB")

    if mode == DifferenceMode.EDGE:
        import cv2
        gray_a = cv2.cvtColor(np.array(a_rgb), cv2.COLOR_RGB2GRAY)
        gray_b = cv2.cvtColor(np.array(b_rgb), cv2.COLOR_RGB2GRAY)
        edges_a = cv2.Canny(gray_a, 50, 150)
        edges_b = cv2.Canny(gray_b, 50, 150)
        return Image.fromarray(cv2.absdiff(edges_a, edges_b)).convert("RGB")

    raise ValueError(f"Unknown difference mode: {mode}")
