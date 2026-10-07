"""
The Reassembly Engine (spec §56-58, §90-91): independent of any AI
engine, takes the original texture plus each part's selected version
and composites the final texture at exact original coordinates
(spec §57's non-negotiable rule) with feathered seams (spec §20).

CONTRACT (spec §56 says reassembly receives "part outputs" -- already-
produced images, not raw AI output): a version's `output_path` image
must already be exactly `part.bounds.width x part.bounds.height` --
i.e. the *core* region only. If context padding was used to generate
that output, cropping the core region back out (image.crop.
core_region_offset) is the generation/pipeline's job, not reassembly's
-- reassembly never resizes or reinterprets AI output dimensions
(spec §57's explicit warning against inferring coordinates from
resized output).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PIL import Image

from core.events.bus import EventBus
from core.events.types import EventType
from core.models.part import Part, PartStatus
from image.blend import feathered_composite
from image.color import AlphaMode, apply_alpha_mode
from image.loader import load_image
from image.validation import ValidationResult


class ReassemblyError(Exception):
    pass


def validate_before_export(source_image: Image.Image, parts: List[Part]) -> ValidationResult:
    """spec §91's pre-export checklist: every non-skipped part must
    have either a selected version whose output actually exists on
    disk, or be explicitly marked USING_ORIGINAL -- reassembly never
    silently substitutes data for a part nobody resolved (spec §91's
    "Never silently substitute data")."""
    errors: List[str] = []
    for part in parts:
        if part.status in (PartStatus.SKIPPED, PartStatus.USING_ORIGINAL):
            continue

        if part.bounds.x + part.bounds.width > source_image.width or \
           part.bounds.y + part.bounds.height > source_image.height:
            errors.append(f"Part {part.id} ({part.name}): bounds fall outside the source texture")
            continue

        version = part.selected_version()
        if version is None:
            errors.append(f"Part {part.id} ({part.name}) has no selected output")
            continue
        if not Path(version.output_path).exists():
            errors.append(f"Part {part.id} ({part.name}): selected version output is missing on disk: "
                           f"{version.output_path}")

    return ValidationResult(ok=not errors, errors=errors)


def reassemble(
    source_image: Image.Image,
    parts: List[Part],
    alpha_mode: AlphaMode = AlphaMode.PRESERVE_ORIGINAL,
    default_feather: int = 0,
    event_bus: Optional[EventBus] = None,
) -> Image.Image:
    validation = validate_before_export(source_image, parts)
    if not validation.ok:
        raise ReassemblyError("Reassembly validation failed: " + "; ".join(validation.errors))

    if event_bus is not None:
        event_bus.publish(EventType.REASSEMBLY_STARTED, part_count=len(parts))

    result = source_image.copy()
    for index, part in enumerate(parts, start=1):
        if part.status in (PartStatus.SKIPPED, PartStatus.USING_ORIGINAL):
            continue  # original pixels are already present in `result`

        version = part.selected_version()
        if version is None:
            continue  # unreachable if validate_before_export passed, but never assume

        patch = load_image(version.output_path)
        expected_size = (part.bounds.width, part.bounds.height)
        if patch.size != expected_size:
            raise ReassemblyError(
                f"Part {part.id}: version output size {patch.size} does not match its bounds "
                f"{expected_size} -- the pipeline must crop AI output down to the core region "
                f"before saving a version (spec §19, §57)"
            )

        original_region = source_image.crop((
            part.bounds.x, part.bounds.y,
            part.bounds.x + part.bounds.width, part.bounds.y + part.bounds.height,
        ))
        patch_with_alpha = apply_alpha_mode(original_region, patch, alpha_mode)
        result = feathered_composite(
            result, patch_with_alpha, part.bounds, feather_radius=part.feather or default_feather
        )

        if event_bus is not None:
            event_bus.publish(EventType.REASSEMBLY_PROGRESS, part_id=part.id, index=index, total=len(parts))

    if alpha_mode == AlphaMode.IGNORE and result.mode != "RGB":
        result = result.convert("RGB")

    if event_bus is not None:
        event_bus.publish(EventType.REASSEMBLY_COMPLETED)

    return result
