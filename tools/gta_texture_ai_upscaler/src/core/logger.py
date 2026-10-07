"""
Central logging setup for GTA Texture AI Upscaler.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from .config import AppConfig, LoggingConfig


_LOGGER_NAME = "gta_upscaler"


def setup_logger(cfg: Optional[AppConfig] = None, level: Optional[str] = None) -> logging.Logger:
    """
    Configure root project logger with console + rotating file handler.
    Safe to call multiple times.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    log_cfg: LoggingConfig = cfg.logging if cfg else LoggingConfig()
    log_level = getattr(logging, (level or log_cfg.level).upper(), logging.INFO)
    logger.setLevel(log_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(log_level)
    logger.addHandler(console)

    # File
    if cfg:
        log_path = cfg.resolve_path(log_cfg.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=log_cfg.max_size_mb * 1024 * 1024,
            backupCount=log_cfg.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)
        logger.addHandler(file_handler)

    logger.debug("Logger initialized")
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")
    return logging.getLogger(_LOGGER_NAME)
