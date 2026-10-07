"""
Diagnostic report export (spec §51): a ZIP containing system info,
application log, engine logs, recent job logs, a configuration
summary, and any failed commands -- with secrets redacted (spec §51:
"Do not expose sensitive environment variables or secrets").
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Optional, Union

from diagnostics.engines import run_engine_diagnostics
from diagnostics.system import collect_system_info
from engines.custom_cli.config import CLIEngineConfig
from engines.registry import EngineRegistry

_REDACTED = "***REDACTED***"


def _redact_config(config: CLIEngineConfig) -> Dict:
    data = config.to_dict()
    data["environment"] = {k: _REDACTED for k in data.get("environment", {})}
    return data


def _collect_failed_commands(cli_logs_dir: Path) -> list:
    """Scans a CustomCLIEngine logs_dir (jobs/<job_id>/{command.txt,
    result.json}) for non-zero exit codes."""
    failed = []
    if not cli_logs_dir.is_dir():
        return failed
    for job_dir in sorted(cli_logs_dir.iterdir()):
        result_file = job_dir / "result.json"
        command_file = job_dir / "command.txt"
        if not result_file.exists():
            continue
        try:
            result_data = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if result_data.get("exit_code") not in (0, None):
            failed.append({
                "job_id": job_dir.name,
                "exit_code": result_data.get("exit_code"),
                "command": command_file.read_text(encoding="utf-8") if command_file.exists() else "",
            })
    return failed


def export_diagnostic_report(
    output_zip_path: Union[str, Path],
    logs_dir: Union[str, Path],
    registry: EngineRegistry,
    engine_configs: Optional[Dict[str, CLIEngineConfig]] = None,
    cli_logs_dir: Optional[Union[str, Path]] = None,
    max_job_logs: int = 20,
) -> Path:
    output_zip_path = Path(output_zip_path)
    logs_dir = Path(logs_dir)

    system_info = collect_system_info()
    engine_diagnostics = run_engine_diagnostics(registry)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        (tmp_dir / "system_info.json").write_text(json.dumps(asdict(system_info), indent=2), encoding="utf-8")
        (tmp_dir / "engine_diagnostics.json").write_text(
            json.dumps([asdict(e) for e in engine_diagnostics], indent=2), encoding="utf-8"
        )

        app_log = logs_dir / "application.log"
        if app_log.exists():
            shutil.copy(app_log, tmp_dir / "application.log")

        engine_logs_dir = logs_dir / "engine"
        if engine_logs_dir.is_dir():
            shutil.copytree(engine_logs_dir, tmp_dir / "engine_logs")

        jobs_dir = logs_dir / "jobs"
        if jobs_dir.is_dir():
            job_files = sorted(jobs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            recent_dir = tmp_dir / "recent_job_logs"
            recent_dir.mkdir()
            for job_file in job_files[:max_job_logs]:
                shutil.copy(job_file, recent_dir / job_file.name)

        config_summary = {name: _redact_config(cfg) for name, cfg in (engine_configs or {}).items()}
        (tmp_dir / "configuration_summary.json").write_text(json.dumps(config_summary, indent=2), encoding="utf-8")

        if cli_logs_dir is not None:
            failed_commands = _collect_failed_commands(Path(cli_logs_dir))
            (tmp_dir / "failed_commands.json").write_text(json.dumps(failed_commands, indent=2), encoding="utf-8")

        output_zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(tmp_dir):
                for fname in files:
                    full = Path(root) / fname
                    zf.write(full, full.relative_to(tmp_dir))

    return output_zip_path
