"""
Project storage backends (spec §43).

A `.uvrs` project may be a ZIP archive or a plain directory -- the
rest of the application only talks to the `ProjectStorage` interface,
never to zipfile/os directly, so switching formats (or adding a third
backend later) never touches project_system.manager or anything above
it.

Both backends operate on a *working directory* layout:

    <working_dir>/
    ├── project.json
    ├── texture/
    ├── parts/
    ├── logs/
    ├── presets/
    ├── history/
    └── settings/          engine profiles for this project (project_system/settings.py)

For DirectoryProjectStorage the working dir *is* the saved project.
For ZipProjectStorage the working dir is a staging area that gets
zipped on save() and unzipped into on load().
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Tuple

from core.models.project import Project

PROJECT_JSON_NAME = "project.json"
WORKING_SUBDIRS = ("texture", "parts", "logs", "presets", "history", "settings")


def ensure_working_layout(working_dir: Path) -> None:
    working_dir.mkdir(parents=True, exist_ok=True)
    for sub in WORKING_SUBDIRS:
        (working_dir / sub).mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    """Write-then-rename so a crash mid-write can never leave `path`
    truncated or partially written (spec §45, §73). `os.replace` is
    atomic on POSIX and Windows as long as src/dst share a filesystem,
    which they do here because the temp file is a sibling of `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Never leave a stray temp file or a half-written target.
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


class ProjectStorage(ABC):
    """Abstracts *where and how* a project is persisted. Callers only
    ever see `Project` objects and working-directory paths."""

    @abstractmethod
    def save(self, project: Project, working_dir: Path, destination: Path) -> None:
        """Persist `project.json` into `working_dir` (which already
        contains texture/parts/logs/etc.) and commit `working_dir` to
        `destination` in whatever form this backend uses."""
        raise NotImplementedError

    @abstractmethod
    def load(self, source: Path, working_dir: Path) -> Project:
        """Populate `working_dir` from `source` and return the parsed
        Project. `working_dir` is caller-owned scratch space."""
        raise NotImplementedError


class DirectoryProjectStorage(ProjectStorage):
    """The project *is* a directory on disk; `destination` and
    `working_dir` are the same path."""

    def save(self, project: Project, working_dir: Path, destination: Path) -> None:
        ensure_working_layout(working_dir)
        atomic_write_text(
            working_dir / PROJECT_JSON_NAME,
            json.dumps(project.to_dict(), indent=2, ensure_ascii=False),
        )

    def load(self, source: Path, working_dir: Path) -> Project:
        project_json = source / PROJECT_JSON_NAME
        if not project_json.exists():
            raise FileNotFoundError(f"No {PROJECT_JSON_NAME} found under {source}")
        data = json.loads(project_json.read_text(encoding="utf-8"))
        return Project.from_dict(data)


class ZipProjectStorage(ProjectStorage):
    """The project is a `.uvrs` ZIP archive. `working_dir` is a
    staging directory; contents are zipped into `destination` on
    save(), and `destination` is extracted into `working_dir` on
    load()."""

    def save(self, project: Project, working_dir: Path, destination: Path) -> None:
        ensure_working_layout(working_dir)
        atomic_write_text(
            working_dir / PROJECT_JSON_NAME,
            json.dumps(project.to_dict(), indent=2, ensure_ascii=False),
        )

        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
        )
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(working_dir):
                    for fname in files:
                        full = Path(root) / fname
                        arcname = full.relative_to(working_dir)
                        zf.write(full, arcname)
            os.replace(tmp_name, destination)
        except BaseException:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise

    def load(self, source: Path, working_dir: Path) -> Project:
        if not zipfile.is_zipfile(source):
            raise ValueError(f"{source} is not a valid .uvrs (zip) project file")
        ensure_working_layout(working_dir)
        with zipfile.ZipFile(source, "r") as zf:
            zf.extractall(working_dir)
        project_json = working_dir / PROJECT_JSON_NAME
        if not project_json.exists():
            raise FileNotFoundError(f"{source} does not contain {PROJECT_JSON_NAME}")
        data = json.loads(project_json.read_text(encoding="utf-8"))
        return Project.from_dict(data)


def storage_for_path(path: Path) -> ProjectStorage:
    """Pick a backend from a destination path's suffix -- `.uvrs` (or
    any file) uses the ZIP backend, an extensionless/directory-style
    path uses the directory backend."""
    if path.suffix.lower() == ".uvrs" or path.suffix == "":
        return ZipProjectStorage() if path.suffix.lower() == ".uvrs" else DirectoryProjectStorage()
    return ZipProjectStorage()
