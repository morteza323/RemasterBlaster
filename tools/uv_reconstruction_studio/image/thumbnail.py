"""
Thumbnail generation (spec §64): the queue and part list must not load
every full-resolution asset simultaneously.
"""

from __future__ import annotations

from PIL import Image


def generate_thumbnail(image: Image.Image, max_size: int = 256) -> Image.Image:
    thumb = image.copy()
    thumb.thumbnail((max_size, max_size), Image.LANCZOS)
    return thumb
