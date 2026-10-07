"""
Image validation (spec §60): explicit pre- and post-processing checks
so a bad input or a corrupted engine output is caught and reported
(spec §70, §96), never silently passed through the pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from PIL import Image, UnidentifiedImageError


@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)

    @staticmethod
    def success() -> "ValidationResult":
        return ValidationResult(ok=True)

    @staticmethod
    def failure(*errors: str) -> "ValidationResult":
        return ValidationResult(ok=False, errors=list(errors))

    def merge(self, other: "ValidationResult") -> "ValidationResult":
        return ValidationResult(ok=self.ok and other.ok, errors=self.errors + other.errors)


def validate_input_image(path: Union[str, Path]) -> ValidationResult:
    """Validate dimensions/format/readability/existence/permissions/
    decodability before a part ever reaches an engine (spec §60)."""
    path = Path(path)
    if not path.exists():
        return ValidationResult.failure(f"Input file does not exist: {path}")
    if not os.access(path, os.R_OK):
        return ValidationResult.failure(f"Input file is not readable (permission denied): {path}")
    try:
        with Image.open(path) as img:
            img.verify()
    except (UnidentifiedImageError, OSError) as exc:
        return ValidationResult.failure(f"Input file failed to decode: {exc}")
    # Re-open after verify() -- PIL invalidates the file handle used by verify().
    try:
        with Image.open(path) as img:
            if img.width <= 0 or img.height <= 0:
                return ValidationResult.failure(f"Input image has invalid dimensions: {img.size}")
    except (UnidentifiedImageError, OSError) as exc:
        return ValidationResult.failure(f"Input file failed to re-open after verify: {exc}")
    return ValidationResult.success()


def validate_output_image(
    path: Union[str, Path],
    expected_width: Optional[int] = None,
    expected_height: Optional[int] = None,
    expected_format: Optional[str] = None,
) -> ValidationResult:
    """Validate an engine's output before it's accepted as a version
    (spec §60): exists, non-zero, decodable, and matches the expected
    dimensions/format if given. Never assume a subprocess exiting 0
    means the output is actually valid."""
    path = Path(path)
    errors: List[str] = []

    if not path.exists():
        return ValidationResult.failure(f"Output file does not exist: {path}")
    if path.stat().st_size == 0:
        return ValidationResult.failure(f"Output file is zero bytes: {path}")

    try:
        with Image.open(path) as img:
            img.load()
            if expected_width is not None and img.width != expected_width:
                errors.append(f"Width mismatch: expected {expected_width}, got {img.width}")
            if expected_height is not None and img.height != expected_height:
                errors.append(f"Height mismatch: expected {expected_height}, got {img.height}")
            if expected_format is not None and img.format != expected_format:
                errors.append(f"Format mismatch: expected {expected_format}, got {img.format}")
    except (UnidentifiedImageError, OSError) as exc:
        return ValidationResult.failure(f"Output image failed to decode: {exc}")

    return ValidationResult(ok=not errors, errors=errors)
