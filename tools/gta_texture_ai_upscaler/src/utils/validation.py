"""
Validate model files and sd-cli binary before running.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..core.config import AppConfig
from ..core.logger import get_logger

logger = get_logger("validation")


@dataclass
class ValidationResult:
    ok: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_models(cfg: AppConfig) -> ValidationResult:
    result = ValidationResult()
    checks = [
        ("Diffusion GGUF", cfg.models.diffusion_gguf, [".gguf"]),
        ("Text Encoder", cfg.models.text_encoder, [".safetensors", ".gguf"]),
        ("VAE", cfg.models.vae, [".safetensors", ".gguf"]),
    ]
    for label, rel, allowed_ext in checks:
        path = cfg.resolve_path(rel)
        if not path.exists():
            result.add_error(f"{label} not found: {path}")
            continue
        if path.suffix.lower() not in allowed_ext:
            result.add_warning(f"{label} has unexpected extension: {path.suffix}")
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb < 10:
            result.add_warning(f"{label} seems very small ({size_mb:.1f} MB): {path}")
        logger.info(f"  {label}: OK ({size_mb:.1f} MB) → {path}")
    return result


def validate_sd_cli(cfg: AppConfig) -> ValidationResult:
    result = ValidationResult()
    cli = cfg.models.sd_cli
    path = Path(cli)

    # Try as given path first, then PATH lookup
    resolved: Optional[Path] = None
    if path.is_file():
        resolved = path
    else:
        found = shutil.which(cli)
        if found:
            resolved = Path(found)

    if resolved is None:
        result.add_error(
            f"sd-cli not found: '{cli}'. "
            "Download/build stable-diffusion.cpp and set models.sd_cli in settings.yaml "
            "to the full path of sd-cli (or sd-cli.exe on Windows)."
        )
        return result

    # Try --help to confirm it runs
    try:
        proc = subprocess.run(
            [str(resolved), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Many builds return 0 or 1 on --help; just check it produced output
        out = (proc.stdout or "") + (proc.stderr or "")
        if "diffusion" in out.lower() or "stable-diffusion" in out.lower() or "usage" in out.lower() or len(out) > 50:
            logger.info(f"  sd-cli: OK → {resolved}")
        else:
            result.add_warning(f"sd-cli ran but output looks unexpected: {resolved}")
    except FileNotFoundError:
        result.add_error(f"sd-cli binary not executable: {resolved}")
    except subprocess.TimeoutExpired:
        result.add_warning("sd-cli --help timed out (still may work)")
    except Exception as e:
        result.add_warning(f"Could not fully verify sd-cli: {e}")

    return result


def run_full_validation(cfg: AppConfig) -> ValidationResult:
    logger.info("Running pre-flight validation...")
    r1 = validate_models(cfg)
    r2 = validate_sd_cli(cfg)
    combined = ValidationResult(ok=r1.ok and r2.ok)
    combined.errors = r1.errors + r2.errors
    combined.warnings = r1.warnings + r2.warnings
    if combined.errors:
        for e in combined.errors:
            logger.error(f"VALIDATION ERROR: {e}")
    if combined.warnings:
        for w in combined.warnings:
            logger.warning(f"VALIDATION WARNING: {w}")
    if combined.ok:
        logger.info("Pre-flight validation passed.")
    return combined
