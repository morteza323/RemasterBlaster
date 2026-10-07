"""
Region calculation from guides (spec §15 steps 1-2).

Horizontal + vertical guides partition the source texture into a grid
of rectangular regions, the way slice guides work in an image editor:
every pair of adjacent guide positions (plus the image edges) becomes
one region's span on that axis.
"""

from __future__ import annotations

from typing import List

from core.models.guide import Guide, GuideOrientation
from core.models.part import Bounds
from image.validation import ValidationResult


def validate_guides(guides: List[Guide], image_width: int, image_height: int) -> ValidationResult:
    errors: List[str] = []
    for g in guides:
        if g.orientation == GuideOrientation.HORIZONTAL and not (0 <= g.position <= image_height):
            errors.append(
                f"Guide {g.id} (horizontal) position {g.position} is outside image height {image_height}"
            )
        if g.orientation == GuideOrientation.VERTICAL and not (0 <= g.position <= image_width):
            errors.append(
                f"Guide {g.id} (vertical) position {g.position} is outside image width {image_width}"
            )
    return ValidationResult(ok=not errors, errors=errors)


def calculate_regions(image_width: int, image_height: int, guides: List[Guide]) -> List[Bounds]:
    """Returns regions in row-major order (top-to-bottom, left-to-right)
    so part numbering reads naturally in the queue/part list."""
    h_positions = sorted({0, image_height} | {
        g.position for g in guides
        if g.orientation == GuideOrientation.HORIZONTAL and 0 < g.position < image_height
    })
    v_positions = sorted({0, image_width} | {
        g.position for g in guides
        if g.orientation == GuideOrientation.VERTICAL and 0 < g.position < image_width
    })

    regions: List[Bounds] = []
    for j in range(len(h_positions) - 1):
        y0, y1 = h_positions[j], h_positions[j + 1]
        for i in range(len(v_positions) - 1):
            x0, x1 = v_positions[i], v_positions[i + 1]
            if x1 > x0 and y1 > y0:
                regions.append(Bounds(x=x0, y=y0, width=x1 - x0, height=y1 - y0))
    return regions
