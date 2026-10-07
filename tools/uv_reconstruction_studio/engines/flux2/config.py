"""
Flux2Profile (spec §2, §85-87): the fields the Settings UI's "AI Engine
Configuration" panel edits. This is deliberately a plain, JSON-friendly
dataclass -- spec §87 requires engine profiles to be exportable as JSON
without secrets, and there are no credentials here to worry about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Flux2Profile:
    # spec's initial/default configuration (requirement #8):
    model: str = "Flux.2 Klein 4B"
    quantization: str = "4Q_K_M"
    model_path: Optional[str] = None
    executable: str = "flux-cli"
    gpu: str = "auto"
    vram_mode: str = "auto"
    extra_arguments: List[str] = field(default_factory=list)
    timeout_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Flux2Profile":
        return cls(**data)


DEFAULT_PROFILE = Flux2Profile()
