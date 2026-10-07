"""
Hierarchical logging (spec §8-11).

Never one giant log file. This sets up:

  logs/application.log                 -- everything, INFO+
  logs/<component>.log                 -- gui/project/uv/queue/pipeline/reassembly
  logs/engine/<engine_name>.log        -- one file per engine
  logs/jobs/<job_id>.log               -- one file per job
  logs/parts/<part_id>.log             -- one file per part

Every record carries project/job/part/engine context fields (defaulting
to "-" when not applicable) so any single line is traceable back to
exactly what it was about, per spec §95.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.constants import LOG_ENGINE_SUBDIR, LOG_JOBS_SUBDIR, LOG_PARTS_SUBDIR

_CONTEXT_FIELDS = ("project_id", "job_id", "part_id", "engine")

_LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d %(levelname)-8s "
    "component=%(name)s project=%(project_id)s job=%(job_id)s "
    "part=%(part_id)s engine=%(engine)s message=%(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _ContextFilter(logging.Filter):
    """Fills in missing context fields so the formatter above never
    KeyErrors on a plain `logger.info("...")` call with no `extra=`."""

    def filter(self, record: logging.LogRecord) -> bool:
        for field in _CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, "-")
        return True


class LoggingManager:
    def __init__(self, root_dir: str = "logs", console_level: int = logging.INFO):
        self.root_dir = Path(root_dir)
        self.console_level = console_level
        self._configured_loggers: dict[str, logging.Logger] = {}
        self._context_filter = _ContextFilter()
        self._ensure_dirs()
        self._root_configured = False

    def _ensure_dirs(self) -> None:
        (self.root_dir / LOG_ENGINE_SUBDIR).mkdir(parents=True, exist_ok=True)
        (self.root_dir / LOG_JOBS_SUBDIR).mkdir(parents=True, exist_ok=True)
        (self.root_dir / LOG_PARTS_SUBDIR).mkdir(parents=True, exist_ok=True)

    def setup_root(self, level: int = logging.DEBUG) -> None:
        """Configure the top-level `application.log` sink plus console.
        Call once at startup, before any get_*_logger() calls."""
        if self._root_configured:
            return
        root = logging.getLogger("uvrs")
        root.setLevel(level)

        file_handler = logging.FileHandler(self.root_dir / "application.log", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        file_handler.addFilter(self._context_filter)
        root.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(self.console_level)
        console_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        console_handler.addFilter(self._context_filter)
        root.addHandler(console_handler)

        self._root_configured = True

    def get_component_logger(self, component: str) -> logging.Logger:
        """e.g. get_component_logger('queue') -> logs/queue.log, and
        also propagates up to application.log + console via 'uvrs'."""
        return self._get_or_create(f"uvrs.{component}", self.root_dir / f"{component}.log")

    def get_engine_logger(self, engine_name: str) -> logging.Logger:
        path = self.root_dir / LOG_ENGINE_SUBDIR / f"{engine_name}.log"
        return self._get_or_create(f"uvrs.engine.{engine_name}", path)

    def get_job_logger(self, job_id: str) -> logging.Logger:
        path = self.root_dir / LOG_JOBS_SUBDIR / f"{job_id}.log"
        return self._get_or_create(f"uvrs.jobs.{job_id}", path)

    def get_part_logger(self, part_id: str) -> logging.Logger:
        path = self.root_dir / LOG_PARTS_SUBDIR / f"{part_id}.log"
        return self._get_or_create(f"uvrs.parts.{part_id}", path)

    def _get_or_create(self, logger_name: str, path: Path) -> logging.Logger:
        if logger_name in self._configured_loggers:
            return self._configured_loggers[logger_name]
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True  # bubbles up to 'uvrs' -> application.log + console
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        handler.addFilter(self._context_filter)
        logger.addHandler(handler)
        self._configured_loggers[logger_name] = logger
        return logger
