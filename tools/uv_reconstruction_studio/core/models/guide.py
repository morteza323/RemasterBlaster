"""
Guide: a first-class UV editor object (spec §13-14).

Positions are stored as ints -- coordinates must map exactly to image
pixels with no floating-point ambiguity (spec §14).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Dict


@unique
class GuideOrientation(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


@dataclass
class Guide:
    orientation: GuideOrientation
    position: int
    id: str = field(default_factory=lambda: f"guide_{uuid.uuid4().hex[:8]}")
    locked: bool = False
    visible: bool = True
    snap_enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.position, int):
            raise TypeError(
                f"Guide position must be an exact pixel int, got {type(self.position).__name__}"
            )
        if self.position < 0:
            raise ValueError("Guide position cannot be negative")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "orientation": self.orientation.value,
            "position": self.position,
            "locked": self.locked,
            "visible": self.visible,
            "snap_enabled": self.snap_enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Guide":
        return cls(
            id=data["id"],
            orientation=GuideOrientation(data["orientation"]),
            position=int(data["position"]),
            locked=data.get("locked", False),
            visible=data.get("visible", True),
            snap_enabled=data.get("snap_enabled", True),
        )
