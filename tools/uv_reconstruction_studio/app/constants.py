"""
Global constants for UV Reconstruction Studio.

These are intentionally free of any engine-specific (e.g. Flux.2)
assumptions. Engine-specific defaults live in engine config files,
not here, per the architectural principle that the core application
must never depend on a specific AI engine.
"""

from pathlib import Path

# Default root for a project's on-disk working directory.
DEFAULT_PROJECT_DIR_NAME = "project"

# Hierarchical log layout (see logging_system/manager.py)
LOG_ROOT_DIR = Path("logs")
LOG_ENGINE_SUBDIR = "engine"
LOG_JOBS_SUBDIR = "jobs"
LOG_PARTS_SUBDIR = "parts"

# Autosave defaults (seconds). 0 = disabled.
DEFAULT_AUTOSAVE_INTERVAL_SECONDS = 60

# Conservative default: never assume multiple heavy AI processes
# should run concurrently unless the user's engine config allows it.
DEFAULT_MAX_CONCURRENT_JOBS = 1
