"""
Project schema (spec §43-44), with explicit format versioning so
future versions can migrate old projects rather than break on them.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.version import PROJECT_FORMAT_VERSION, APP_VERSION
from core.models.guide import Guide
from core.models.part import Part


@dataclass
class SourceTexture:
    filename: str
    width: int
    height: int
    format: str = "PNG"
    hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceTexture":
        return cls(**data)


@dataclass
class Project:
    name: str
    source: SourceTexture
    id: str = field(default_factory=lambda: f"project_{uuid.uuid4().hex[:8]}")
    format_version: int = PROJECT_FORMAT_VERSION
    application_version: str = APP_VERSION
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)
    guides: List[Guide] = field(default_factory=list)
    parts: List[Part] = field(default_factory=list)
    engines: Dict[str, Any] = field(default_factory=dict)
    presets: Dict[str, Any] = field(default_factory=dict)
    reassembly: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def touch(self) -> None:
        self.modified = time.time()

    def add_guide(self, guide: Guide) -> None:
        self.guides.append(guide)
        self.touch()

    def remove_guide(self, guide_id: str) -> bool:
        before = len(self.guides)
        self.guides = [g for g in self.guides if g.id != guide_id]
        changed = len(self.guides) != before
        if changed:
            self.touch()
        return changed

    def add_part(self, part: Part) -> None:
        self.parts.append(part)
        self.touch()

    def get_part(self, part_id: str) -> Optional[Part]:
        for p in self.parts:
            if p.id == part_id:
                return p
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format_version": self.format_version,
            "application_version": self.application_version,
            "project": {
                "id": self.id,
                "name": self.name,
                "created": self.created,
                "modified": self.modified,
            },
            "source": self.source.to_dict(),
            "guides": [g.to_dict() for g in self.guides],
            "parts": [p.to_dict() for p in self.parts],
            "engines": self.engines,
            "presets": self.presets,
            "reassembly": self.reassembly,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        """Load a project, migrating older format_version payloads first.

        Only format_version == PROJECT_FORMAT_VERSION is understood
        today; this is the single seam a future migration step hooks
        into rather than scattering version checks through the app.
        """
        data = _migrate(data)
        proj_meta = data["project"]
        project = cls(
            name=proj_meta["name"],
            source=SourceTexture.from_dict(data["source"]),
            id=proj_meta["id"],
            format_version=data.get("format_version", PROJECT_FORMAT_VERSION),
            application_version=data.get("application_version", APP_VERSION),
            created=proj_meta.get("created", time.time()),
            modified=proj_meta.get("modified", time.time()),
            engines=data.get("engines", {}),
            presets=data.get("presets", {}),
            reassembly=data.get("reassembly", {}),
            history=data.get("history", []),
        )
        project.guides = [Guide.from_dict(g) for g in data.get("guides", [])]
        project.parts = [Part.from_dict(p) for p in data.get("parts", [])]
        return project


def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    version = data.get("format_version", PROJECT_FORMAT_VERSION)
    if version > PROJECT_FORMAT_VERSION:
        raise ValueError(
            f"Project format_version {version} is newer than this build supports "
            f"({PROJECT_FORMAT_VERSION}). Update the application."
        )
    # No migrations defined yet -- format_version has never changed.
    return data
