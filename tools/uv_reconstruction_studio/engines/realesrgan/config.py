"""
RealESRGANProfile (spec §10 of the phase plan, §82: "Do not make
Real-ESRGAN a mandatory dependency if it is an external executable").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RealESRGANProfile:
    executable: str = "realesrgan-ncnn-vulkan"
    model_name: str = "realesrgan-x4plus"
    model_path: Optional[str] = None       # directory containing the .param/.bin model files (-m)
    tile_size: Optional[int] = None        # 0 or None = auto; a fixed tile size reduces VRAM use (-t)
    gpu_id: Optional[str] = None
    extra_arguments: List[str] = field(default_factory=list)
    timeout_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RealESRGANProfile":
        return cls(**data)


DEFAULT_PROFILE = RealESRGANProfile()
