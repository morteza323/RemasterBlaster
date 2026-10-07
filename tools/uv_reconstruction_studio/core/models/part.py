"""
Part: an independently processable UV region (spec §16-19).

A Part never stores pixels itself -- it stores bounds, settings, and
a `history` of version ids. Actual image bytes live under the
project's Part_XXXX/ folder (spec §17), written by the project/image
systems in a later phase. Keeping Part as pure metadata is what makes
non-destructive processing (spec §18) possible: nothing here can
overwrite the original.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Bounds:
    """A rectangular region in source-texture pixel space.

    Coordinates are ints -- reassembly (spec §57) is only correct if
    these map exactly back to the original texture with no rounding
    drift, so this type forbids float construction.
    """

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height"):
            value = getattr(self, name)
            if not isinstance(value, int):
                raise TypeError(f"Bounds.{name} must be an int, got {type(value).__name__}")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Bounds width/height must be positive")
        if self.x < 0 or self.y < 0:
            raise ValueError("Bounds x/y cannot be negative")

    def padded(self, padding: int) -> "Bounds":
        """Expand for AI context input (spec §19). Never goes negative;
        callers are responsible for clamping against source dimensions."""
        x = max(0, self.x - padding)
        y = max(0, self.y - padding)
        return Bounds(x=x, y=y, width=self.width + (self.x - x) + padding,
                      height=self.height + (self.y - y) + padding)

    def to_dict(self) -> Dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bounds":
        return cls(x=int(data["x"]), y=int(data["y"]),
                    width=int(data["width"]), height=int(data["height"]))


@unique
class PartStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    USING_ORIGINAL = "using_original"


@dataclass
class PartVersion:
    """One immutable reconstruction attempt (spec §40-42)."""

    engine: str
    output_path: str
    id: str = field(default_factory=lambda: f"v{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)
    model: Optional[str] = None
    quantization: Optional[str] = None
    model_path: Optional[str] = None
    prompt: str = ""
    negative_prompt: str = ""
    strength: Optional[float] = None
    steps: Optional[int] = None
    guidance: Optional[float] = None
    seed: Optional[int] = None
    input_hash: Optional[str] = None
    output_hash: Optional[str] = None
    padding: Optional[int] = None
    mask_hash: Optional[str] = None
    software_version: Optional[str] = None
    engine_version: Optional[str] = None
    command: Optional[List[str]] = None
    duration_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PartVersion":
        return cls(**data)


@dataclass
class Part:
    source_texture: str
    bounds: Bounds
    id: str = field(default_factory=lambda: f"part_{uuid.uuid4().hex[:6]}")
    name: str = ""
    material: Optional[str] = None
    prompt: str = ""
    negative_prompt: str = ""
    strength: float = 1.0
    steps: Optional[int] = None
    guidance: Optional[float] = None
    seed: Optional[int] = None
    engine: Optional[str] = None
    mask_path: Optional[str] = None
    padding: int = 0
    feather: int = 0
    status: PartStatus = PartStatus.PENDING
    selected_version_id: Optional[str] = None
    history: List[PartVersion] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.id

    @property
    def width(self) -> int:
        return self.bounds.width

    @property
    def height(self) -> int:
        return self.bounds.height

    def add_version(self, version: PartVersion) -> None:
        self.history.append(version)
        self.selected_version_id = version.id

    def selected_version(self) -> Optional[PartVersion]:
        if self.selected_version_id is None:
            return None
        for v in self.history:
            if v.id == self.selected_version_id:
                return v
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_texture": self.source_texture,
            "bounds": self.bounds.to_dict(),
            "material": self.material,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "strength": self.strength,
            "steps": self.steps,
            "guidance": self.guidance,
            "seed": self.seed,
            "engine": self.engine,
            "mask_path": self.mask_path,
            "padding": self.padding,
            "feather": self.feather,
            "status": self.status.value,
            "selected_version_id": self.selected_version_id,
            "history": [v.to_dict() for v in self.history],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Part":
        part = cls(
            source_texture=data["source_texture"],
            bounds=Bounds.from_dict(data["bounds"]),
            id=data["id"],
            name=data.get("name", ""),
            material=data.get("material"),
            prompt=data.get("prompt", ""),
            negative_prompt=data.get("negative_prompt", ""),
            strength=data.get("strength", 1.0),
            steps=data.get("steps"),
            guidance=data.get("guidance"),
            seed=data.get("seed"),
            engine=data.get("engine"),
            mask_path=data.get("mask_path"),
            padding=data.get("padding", 0),
            feather=data.get("feather", 0),
            status=PartStatus(data.get("status", PartStatus.PENDING.value)),
        )
        part.history = [PartVersion.from_dict(v) for v in data.get("history", [])]
        part.selected_version_id = data.get("selected_version_id")
        return part
