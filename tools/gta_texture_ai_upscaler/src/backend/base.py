"""
Abstract inference backend interface.
Allows swapping stable-diffusion.cpp with other engines later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class IInferenceBackend(ABC):
    """
    Contract for any image-to-image backend used by the upscaler.
    """

    @abstractmethod
    def initialize(self) -> None:
        """Load models, prepare runtime. Called once at startup."""
        ...

    @abstractmethod
    def encode_prompt_once(self, prompt: str, cache_path: Path) -> None:
        """
        Load text encoder → encode prompt → save embedding → unload text encoder.
        Must be called before process_image if embedding is required.
        """
        ...

    @abstractmethod
    def process_image(
        self,
        input_path: Path,
        output_path: Path,
        seed: int,
        strength: Optional[float] = None,
    ) -> None:
        """
        Run image-to-image on a single texture.
        Uses cached embedding if available.
        Must write result atomically (temp → rename).
        """
        ...

    @abstractmethod
    def shutdown(self) -> None:
        """Release resources."""
        ...

    @abstractmethod
    def is_ready(self) -> bool:
        """Return True if models are loaded and ready for inference."""
        ...
