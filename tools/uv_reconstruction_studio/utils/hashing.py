"""
Hashing utilities (spec §42). Used to detect stale/mismatched data --
e.g. a cached job whose input crop no longer matches the current
source texture, or a version whose recorded output_hash doesn't match
what's actually on disk anymore.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Union

_CHUNK_SIZE = 1024 * 1024


def hash_file(path: Union[str, Path], algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def hash_bytes(data: bytes, algo: str = "sha256") -> str:
    return hashlib.new(algo, data).hexdigest()


def hash_image_pixels(image, algo: str = "sha256") -> str:
    """Hashes decoded pixel data (mode + size + raw bytes), not the
    encoded file -- two PNGs with identical pixels but different
    compression settings must hash identically here."""
    payload = f"{image.mode}:{image.size[0]}x{image.size[1]}:".encode("utf-8") + image.tobytes()
    return hash_bytes(payload, algo)
