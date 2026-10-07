"""
ProjectManager (spec §45-46): owns the currently open Project, its
working directory, autosave scheduling, and crash recovery. Storage
format (zip vs directory) is delegated entirely to `ProjectStorage`.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.models.project import Project
from project_system.storage import (
    PROJECT_JSON_NAME,
    ProjectStorage,
    atomic_write_text,
    ensure_working_layout,
    storage_for_path,
)


@dataclass
class RecoveryInfo:
    project_name: str
    last_autosave: float
    autosave_path: Path


class ProjectManager:
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.project: Optional[Project] = None
        self.working_dir: Optional[Path] = None
        self.save_path: Optional[Path] = None
        self.storage: Optional[ProjectStorage] = None
        self.logger = logger

        self._autosave_thread: Optional[threading.Thread] = None
        self._autosave_stop = threading.Event()

    # -- lifecycle ------------------------------------------------------

    def create(self, project: Project, working_dir: Path) -> None:
        self.working_dir = Path(working_dir)
        ensure_working_layout(self.working_dir)
        self.project = project
        self.save_path = None
        self.storage = None

    def save_as(self, destination: Path, storage: Optional[ProjectStorage] = None) -> None:
        if self.project is None or self.working_dir is None:
            raise RuntimeError("No project is open -- call create() or load() first")
        destination = Path(destination)
        storage = storage or storage_for_path(destination)
        self.project.touch()
        storage.save(self.project, self.working_dir, destination)
        self.storage = storage
        self.save_path = destination
        if self.logger:
            self.logger.info("Project saved", extra={"project_id": self.project.id})

    def save(self) -> None:
        if self.save_path is None:
            raise RuntimeError("Project has never been saved -- call save_as() first")
        self.save_as(self.save_path, self.storage)

    def load(self, source: Path, working_dir: Path, storage: Optional[ProjectStorage] = None) -> Project:
        source = Path(source)
        self.working_dir = Path(working_dir)
        storage = storage or storage_for_path(source)
        self.project = storage.load(source, self.working_dir)
        self.storage = storage
        self.save_path = source
        if self.logger:
            self.logger.info("Project loaded", extra={"project_id": self.project.id})
        return self.project

    # -- autosave (spec §45) ---------------------------------------------

    def autosave_path(self) -> Path:
        if self.working_dir is None:
            raise RuntimeError("No project is open")
        return self.working_dir / ".autosave" / PROJECT_JSON_NAME

    def autosave(self) -> None:
        """Writes the in-memory project to a side location, never
        touching the user's main save file -- spec §45's rule that
        autosave must never corrupt the main project file."""
        if self.project is None or self.working_dir is None:
            return
        path = self.autosave_path()
        atomic_write_text(path, json.dumps(self.project.to_dict(), indent=2, ensure_ascii=False))
        if self.logger:
            self.logger.debug("Autosave written", extra={"project_id": self.project.id})

    def start_autosave(self, interval_seconds: int) -> None:
        """0 or negative disables autosave (spec §45's "Disabled" option)."""
        self.stop_autosave()
        if interval_seconds <= 0:
            return
        self._autosave_stop.clear()

        def _loop() -> None:
            while not self._autosave_stop.wait(interval_seconds):
                try:
                    self.autosave()
                except Exception:
                    if self.logger:
                        self.logger.exception("Autosave failed")

        self._autosave_thread = threading.Thread(target=_loop, daemon=True, name="autosave")
        self._autosave_thread.start()

    def stop_autosave(self) -> None:
        if self._autosave_thread is not None:
            self._autosave_stop.set()
            self._autosave_thread.join(timeout=2)
            self._autosave_thread = None

    # -- crash recovery (spec §46) ----------------------------------------

    @staticmethod
    def check_for_recovery(working_dir: Path) -> Optional[RecoveryInfo]:
        """Returns recovery info only if an autosave exists AND is
        strictly newer than the last real save (or there is no real
        save yet) -- i.e. only when it actually represents unsaved
        work worth offering to recover."""
        working_dir = Path(working_dir)
        autosave_file = working_dir / ".autosave" / PROJECT_JSON_NAME
        if not autosave_file.exists():
            return None
        try:
            data = json.loads(autosave_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        main_file = working_dir / PROJECT_JSON_NAME
        autosave_mtime = autosave_file.stat().st_mtime
        if main_file.exists() and main_file.stat().st_mtime >= autosave_mtime:
            return None

        name = data.get("project", {}).get("name", "Unknown Project")
        return RecoveryInfo(project_name=name, last_autosave=autosave_mtime, autosave_path=autosave_file)

    def recover(self, info: RecoveryInfo) -> Project:
        data = json.loads(info.autosave_path.read_text(encoding="utf-8"))
        self.project = Project.from_dict(data)
        # .autosave/project.json -> working dir is two levels up.
        self.working_dir = info.autosave_path.parent.parent
        if self.logger:
            self.logger.info("Project recovered from autosave", extra={"project_id": self.project.id})
        return self.project

    def discard_recovery(self, info: RecoveryInfo) -> None:
        if info.autosave_path.exists():
            info.autosave_path.unlink()
