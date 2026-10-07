"""
Hardware detection + multi-sensor safety guards.
Protects GPU, RAM, CPU temperature and utilization.
Pause is always recoverable (never stuck forever).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import psutil

from .config import AppConfig
from .logger import get_logger

logger = get_logger("hardware")


@dataclass
class HardwareInfo:
    cpu_name: str = "Unknown"
    cpu_cores: int = 0
    ram_total_gb: float = 0.0
    ram_available_gb: float = 0.0
    gpu_name: str = "Unknown"
    gpu_vram_total_mb: int = 0
    gpu_driver: str = "Unknown"
    has_nvidia: bool = False


def get_cpu_name() -> str:
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    return line.split(":")[1].strip()
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["wmic", "cpu", "get", "name"], text=True, stderr=subprocess.DEVNULL, timeout=5
        )
        lines = [l.strip() for l in out.splitlines() if l.strip() and "Name" not in l]
        if lines:
            return lines[0]
    except Exception:
        pass
    return "Unknown CPU"


def query_nvidia_smi() -> Optional[dict]:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        parts = [p.strip() for p in out.strip().split(",")]
        if len(parts) >= 6:
            return {
                "name": parts[0],
                "temp": int(float(parts[1])),
                "util": int(float(parts[2])),
                "mem_used": int(float(parts[3])),
                "mem_total": int(float(parts[4])),
                "driver": parts[5],
            }
    except Exception as e:
        logger.debug(f"nvidia-smi unavailable: {e}")
    return None


def get_cpu_temperature() -> Optional[int]:
    """Best-effort CPU package temperature."""
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        # Prefer package / Tctl / core
        for key in ("coretemp", "k10temp", "zenpower", "acpitz", "cpu_thermal"):
            if key in temps and temps[key]:
                entries = temps[key]
                # highest reading
                vals = [e.current for e in entries if e.current is not None]
                if vals:
                    return int(max(vals))
        # any sensor
        for entries in temps.values():
            vals = [e.current for e in entries if e.current is not None]
            if vals:
                return int(max(vals))
    except Exception:
        pass
    # Windows: try wmic (often unavailable) or OpenHardwareMonitor not assumed
    return None


def detect_hardware() -> HardwareInfo:
    info = HardwareInfo()
    info.cpu_name = get_cpu_name()
    info.cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 0
    mem = psutil.virtual_memory()
    info.ram_total_gb = round(mem.total / (1024**3), 1)
    info.ram_available_gb = round(mem.available / (1024**3), 1)

    nv = query_nvidia_smi()
    if nv:
        info.has_nvidia = True
        info.gpu_name = nv["name"]
        info.gpu_vram_total_mb = nv["mem_total"]
        info.gpu_driver = nv["driver"]
    else:
        info.gpu_name = "No NVIDIA GPU detected (or nvidia-smi missing)"
    return info


def get_gpu_temperature() -> Optional[int]:
    nv = query_nvidia_smi()
    return nv["temp"] if nv else None


def get_vram_usage() -> Tuple[Optional[int], Optional[int]]:
    nv = query_nvidia_smi()
    if nv:
        return nv["mem_used"], nv["mem_total"]
    return None, None


def get_ram_percent() -> float:
    return psutil.virtual_memory().percent


def get_free_ram_mb() -> float:
    return psutil.virtual_memory().available / (1024 * 1024)


def get_cpu_percent(sample_seconds: float = 0.8) -> float:
    return psutil.cpu_percent(interval=sample_seconds)


def print_hardware_report(info: HardwareInfo) -> None:
    print("=" * 60)
    print("HARDWARE REPORT")
    print("=" * 60)
    print(f"CPU          : {info.cpu_name}")
    print(f"Cores        : {info.cpu_cores}")
    print(f"RAM Total    : {info.ram_total_gb} GB")
    print(f"RAM Available: {info.ram_available_gb} GB")
    print(f"GPU          : {info.gpu_name}")
    if info.has_nvidia:
        print(f"VRAM Total   : {info.gpu_vram_total_mb} MB")
        print(f"Driver       : {info.gpu_driver}")
        temp = get_gpu_temperature()
        used, total = get_vram_usage()
        if temp is not None:
            print(f"GPU Temp     : {temp}°C")
        if used is not None:
            print(f"VRAM Used    : {used} / {total} MB")
    cpu_t = get_cpu_temperature()
    if cpu_t is not None:
        print(f"CPU Temp     : {cpu_t}°C")
    print("=" * 60)


class SafetyGuard:
    """
    Unified safety controller.
    - GPU / CPU temperature
    - RAM & VRAM headroom
    - CPU utilization
    - Mandatory rest every N hours
    All pauses loop until safe; never permanent hang.
    """

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        h = cfg.hardware
        self.gpu_pause = h.gpu_pause_temp_c
        self.gpu_resume = h.gpu_resume_temp_c
        self.cpu_pause = h.cpu_pause_temp_c
        self.cpu_resume = h.cpu_resume_temp_c
        self.interval = max(15, int(h.thermal_check_interval))
        self.max_ram_pct = h.max_ram_percent
        self.min_free_ram_mb = h.min_free_ram_mb
        self.min_free_vram_mb = h.min_free_vram_mb
        self.vram_warn_pct = h.vram_warn_percent
        self.max_cpu_pct = h.max_cpu_percent
        self.cpu_high_pause = h.cpu_high_pause_seconds
        self.rest_every_sec = float(h.rest_every_hours) * 3600.0
        self.rest_duration_sec = int(h.rest_duration_minutes) * 60
        self._session_start = time.time()
        self._last_rest = time.time()
        self._paused = False

    # ---------- individual checks ----------

    def _gpu_too_hot(self) -> bool:
        t = get_gpu_temperature()
        if t is None:
            return False
        return t >= self.gpu_pause

    def _cpu_too_hot(self) -> bool:
        t = get_cpu_temperature()
        if t is None:
            return False
        return t >= self.cpu_pause

    def _ram_pressure(self) -> bool:
        if get_ram_percent() >= self.max_ram_pct:
            return True
        if get_free_ram_mb() < self.min_free_ram_mb:
            return True
        return False

    def _vram_pressure(self) -> bool:
        used, total = get_vram_usage()
        if used is None or total is None or total <= 0:
            return False
        free = total - used
        if free < self.min_free_vram_mb:
            return True
        if (used / total) * 100 >= self.vram_warn_pct:
            # soft: only block if free also low
            if free < self.min_free_vram_mb * 1.5:
                return True
        return False

    def _cpu_overloaded(self) -> bool:
        try:
            return get_cpu_percent(0.5) >= self.max_cpu_pct
        except Exception:
            return False

    def needs_mandatory_rest(self) -> bool:
        return (time.time() - self._last_rest) >= self.rest_every_sec

    # ---------- wait helpers (always recoverable) ----------

    def _wait_until(self, label: str, is_bad, is_good, max_wait_minutes: int = 90) -> None:
        """
        Loop until condition is good.
        Logs every interval. Hard-caps wait so program never disappears forever.
        """
        self._paused = True
        deadline = time.time() + max_wait_minutes * 60
        logger.warning(f"PAUSED — {label}. Checking every {self.interval}s ...")
        print(f"\n*** PAUSED — {label} ***")
        print(f"Will check every {self.interval}s. Auto-resume when safe.\n")

        while time.time() < deadline:
            time.sleep(self.interval)
            try:
                gpu_t = get_gpu_temperature()
                cpu_t = get_cpu_temperature()
                ram = get_ram_percent()
                used, total = get_vram_usage()
                msg = f"  check | GPU={gpu_t}°C CPU={cpu_t}°C RAM={ram:.0f}%"
                if used is not None:
                    msg += f" VRAM={used}/{total}MB"
                logger.info(msg)
                print(msg)
            except Exception as e:
                logger.debug(f"sensor read error: {e}")

            if not is_bad() and is_good():
                logger.info(f"RESUMED — {label} cleared")
                print(f"*** RESUMED — {label} cleared ***\n")
                self._paused = False
                return

        # Timeout: resume anyway with warning (prevents infinite stuck)
        logger.error(
            f"Safety wait timed out after {max_wait_minutes} min for: {label}. "
            "Resuming carefully to avoid permanent hang."
        )
        print(f"*** TIMEOUT on pause ({label}) — resuming carefully ***\n")
        self._paused = False

    def wait_for_thermal(self) -> None:
        def bad():
            return self._gpu_too_hot() or self._cpu_too_hot()

        def good():
            gpu_t = get_gpu_temperature()
            cpu_t = get_cpu_temperature()
            gpu_ok = (gpu_t is None) or (gpu_t <= self.gpu_resume)
            cpu_ok = (cpu_t is None) or (cpu_t <= self.cpu_resume)
            return gpu_ok and cpu_ok

        self._wait_until("temperature too high", bad, good, max_wait_minutes=120)

    def wait_for_ram(self) -> None:
        def bad():
            return self._ram_pressure()

        def good():
            return not self._ram_pressure()

        self._wait_until("RAM pressure", bad, good, max_wait_minutes=60)

    def wait_for_vram(self) -> None:
        def bad():
            return self._vram_pressure()

        def good():
            return not self._vram_pressure()

        self._wait_until("VRAM pressure", bad, good, max_wait_minutes=45)

    def short_cpu_pause(self) -> None:
        logger.warning(f"CPU utilization high — pausing {self.cpu_high_pause}s")
        print(f"*** CPU high — short pause {self.cpu_high_pause}s ***")
        time.sleep(self.cpu_high_pause)

    def mandatory_rest(self) -> None:
        mins = self.cfg.hardware.rest_duration_minutes
        logger.warning(f"MANDATORY REST — {mins} minutes (every {self.cfg.hardware.rest_every_hours}h)")
        print(f"\n*** MANDATORY REST: {mins} minutes ***")
        print("System cooling down. Progress is saved. Will auto-continue.\n")
        # Sleep in small chunks so logs stay alive and Ctrl+C works
        end = time.time() + self.rest_duration_sec
        while time.time() < end:
            left = int(end - time.time())
            if left % 60 == 0 or left < 60:
                logger.info(f"  rest remaining: {left}s")
            time.sleep(min(30, max(1, left)))
        self._last_rest = time.time()
        logger.info("Mandatory rest finished — continuing")
        print("*** Rest finished — continuing ***\n")

    # ---------- public entry before each job ----------

    def ensure_safe_to_run(self) -> None:
        """
        Call before starting each texture.
        Handles all pause reasons; always returns when safe (or after timeout).
        """
        # 1) Mandatory rest
        if self.needs_mandatory_rest():
            self.mandatory_rest()

        # 2) Temperature
        if self._gpu_too_hot() or self._cpu_too_hot():
            self.wait_for_thermal()

        # 3) RAM
        if self._ram_pressure():
            self.wait_for_ram()

        # 4) VRAM
        if self._vram_pressure():
            self.wait_for_vram()

        # 5) CPU util (short)
        if self._cpu_overloaded():
            self.short_cpu_pause()

    def status_line(self) -> str:
        gpu_t = get_gpu_temperature()
        cpu_t = get_cpu_temperature()
        ram = get_ram_percent()
        used, total = get_vram_usage()
        parts = [f"RAM={ram:.0f}%"]
        if gpu_t is not None:
            parts.append(f"GPU={gpu_t}°C")
        if cpu_t is not None:
            parts.append(f"CPU={cpu_t}°C")
        if used is not None and total:
            parts.append(f"VRAM={used}/{total}MB")
        return " | ".join(parts)
