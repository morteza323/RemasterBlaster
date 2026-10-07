"""
Engine settings persistence (spec §24): lets a user configure Flux.2,
Real-ESRGAN, or any custom CLI engine profile WITHOUT editing Python
source. Profiles are saved as plain JSON, either globally (so the GUI
has something to load before any project is open) or per-project (a
project can pin its own engine settings, per spec §88's per-project
engine override).

This module doesn't know or care about Flux2Profile/RealESRGANProfile
shapes -- it just stores/retrieves whatever `.to_dict()` produced,
keyed by engine type and a user-chosen profile name. Reconstruct the
actual profile object with `Flux2Profile.from_dict(...)` etc.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

GLOBAL_SETTINGS_DIR = Path.home() / ".uvrs"
GLOBAL_ENGINES_FILE = GLOBAL_SETTINGS_DIR / "engines.json"


class EngineSettingsManager:
    """
    On-disk shape:

        {
            "flux2": {
                "default": "Flux.2 Klein 4B",
                "profiles": {"Flux.2 Klein 4B": {...Flux2Profile.to_dict()...}}
            },
            "realesrgan": {"default": "Default", "profiles": {"Default": {...}}}
        }
    """

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}
        else:
            self._data = {}

    def save(self) -> None:
        """Atomic write (temp file + replace), matching the same
        crash-safety guarantee project_system.storage uses for
        project.json -- settings must survive a mid-write crash too."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self.path)

    def list_profiles(self, engine_type: str) -> Dict[str, Dict[str, Any]]:
        return dict(self._data.get(engine_type, {}).get("profiles", {}))

    def get_profile(self, engine_type: str, name: str) -> Optional[Dict[str, Any]]:
        return self._data.get(engine_type, {}).get("profiles", {}).get(name)

    def save_profile(self, engine_type: str, name: str, profile_dict: Dict[str, Any], set_default: bool = False) -> None:
        engine_entry = self._data.setdefault(engine_type, {"default": None, "profiles": {}})
        engine_entry["profiles"][name] = profile_dict
        if set_default or engine_entry.get("default") is None:
            engine_entry["default"] = name
        self.save()

    def delete_profile(self, engine_type: str, name: str) -> None:
        engine_entry = self._data.get(engine_type)
        if engine_entry is None:
            return
        engine_entry["profiles"].pop(name, None)
        if engine_entry.get("default") == name:
            remaining = list(engine_entry["profiles"])
            engine_entry["default"] = remaining[0] if remaining else None
        self.save()

    def get_default_profile_name(self, engine_type: str) -> Optional[str]:
        return self._data.get(engine_type, {}).get("default")

    def get_default_profile(self, engine_type: str) -> Optional[Dict[str, Any]]:
        engine_entry = self._data.get(engine_type)
        if not engine_entry or engine_entry.get("default") is None:
            return None
        return engine_entry["profiles"].get(engine_entry["default"])

    def set_default(self, engine_type: str, name: str) -> None:
        engine_entry = self._data.setdefault(engine_type, {"default": None, "profiles": {}})
        if name in engine_entry["profiles"]:
            engine_entry["default"] = name
            self.save()


def global_settings_manager() -> EngineSettingsManager:
    return EngineSettingsManager(GLOBAL_ENGINES_FILE)


def project_settings_manager(working_dir: Union[str, Path]) -> EngineSettingsManager:
    return EngineSettingsManager(Path(working_dir) / "settings" / "engines.json")
