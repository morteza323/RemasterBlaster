"""
System diagnostics (spec §50): Python/dependency versions, CPU/RAM,
and best-effort GPU detection. Never assumes a GPU or CUDA exists
(spec §101) -- detection failures degrade to an empty list, never an
exception.
"""

from __future__ import annotations

import importlib
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_TRACKED_DEPENDENCIES = ("PIL", "numpy", "cv2", "psutil")


@dataclass
class SystemInfo:
    python_version: str
    platform: str
    cpu_count: Optional[int] = None
    total_ram_mb: Optional[float] = None
    available_ram_mb: Optional[float] = None
    dependency_versions: Dict[str, str] = field(default_factory=dict)
    gpu_names: List[str] = field(default_factory=list)


def _dependency_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    for pkg in _TRACKED_DEPENDENCIES:
        try:
            module = importlib.import_module(pkg)
            versions[pkg] = getattr(module, "__version__", "unknown")
        except ImportError:
            versions[pkg] = "not installed"
    return versions


def _cpu_ram_info():
    try:
        import psutil
    except ImportError:
        return None, None, None
    try:
        cpu_count = psutil.cpu_count(logical=True)
        vm = psutil.virtual_memory()
        return cpu_count, vm.total / (1024 * 1024), vm.available / (1024 * 1024)
    except Exception:
        return None, None, None


def _detect_gpus() -> List[str]:
    """Best-effort only -- tries `nvidia-smi` if it's on PATH. Returns
    an empty list on any failure (no GPU, no driver, tool missing,
    timeout); this function must never raise."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return []
    try:
        result = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def collect_system_info() -> SystemInfo:
    cpu_count, total_ram, available_ram = _cpu_ram_info()
    return SystemInfo(
        python_version=sys.version,
        platform=platform.platform(),
        cpu_count=cpu_count,
        total_ram_mb=total_ram,
        available_ram_mb=available_ram,
        dependency_versions=_dependency_versions(),
        gpu_names=_detect_gpus(),
    )
