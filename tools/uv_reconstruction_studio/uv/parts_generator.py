"""
"GENERATE QUEUE" (spec §15, §17): turns the current guides into Part
objects, each with its own on-disk folder (source/output/logs/previews),
metadata, and a preview thumbnail. Never touches or overwrites parts
that already exist on the project -- generating again just appends
more parts from the current guide layout (spec §15's "do not
immediately destroy or overwrite previous results").
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List

from PIL import Image

from core.models.part import Part
from core.models.project import Project
from image.crop import crop_region, crop_with_padding
from image.thumbnail import generate_thumbnail
from uv.regions import calculate_regions, validate_guides

PART_FOLDER_SUBDIRS = ("source", "output", "logs", "previews")


class PartGenerationError(Exception):
    pass


def create_part_folder(working_dir: Path, part_id: str) -> Path:
    part_dir = Path(working_dir) / "parts" / part_id
    for sub in PART_FOLDER_SUBDIRS:
        (part_dir / sub).mkdir(parents=True, exist_ok=True)
    return part_dir


def generate_parts(
    project: Project,
    source_image: Image.Image,
    working_dir: Path,
    default_padding: int = 0,
    thumbnail_size: int = 128,
) -> List[Part]:
    """Validates guides, computes regions, creates one Part + on-disk
    folder per region, appends them to `project.parts`, and returns
    just the newly created parts."""
    guide_result = validate_guides(project.guides, source_image.width, source_image.height)
    if not guide_result.ok:
        raise PartGenerationError("Guide validation failed: " + "; ".join(guide_result.errors))

    regions = calculate_regions(source_image.width, source_image.height, project.guides)
    if not regions:
        raise PartGenerationError(
            "No regions were produced -- add at least one guide, or the image is degenerate"
        )

    working_dir = Path(working_dir)
    new_parts: List[Part] = []

    for idx, bounds in enumerate(regions, start=1):
        part = Part(
            source_texture=project.source.filename,
            bounds=bounds,
            name=f"part_{idx:04d}",
            padding=default_padding,
        )
        part_dir = create_part_folder(working_dir, part.id)

        core_crop = crop_region(source_image, bounds)
        core_crop.save(part_dir / "source" / "crop.png")

        if default_padding > 0:
            context_crop, _clamped_bounds = crop_with_padding(source_image, bounds, default_padding)
            context_crop.save(part_dir / "source" / "context.png")

        thumb = generate_thumbnail(core_crop, max_size=thumbnail_size)
        thumb.save(part_dir / "previews" / "thumbnail.png")

        metadata = {
            "id": part.id,
            "name": part.name,
            "bounds": bounds.to_dict(),
            "padding": default_padding,
            "created": time.time(),
        }
        (part_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        project.add_part(part)
        new_parts.append(part)

    return new_parts
