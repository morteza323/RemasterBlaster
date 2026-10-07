"""
CustomCLIEngine (spec §84, §3): a fully generic, configuration-driven
ReconstructionEngine. This is the "no hardcoded CLI assumptions"
engine (spec §5) -- point it at any executable via CLIEngineConfig and
it becomes a working engine. Flux2Engine and RealESRGANEngine (Phases
9-10) are thin, pre-configured subclasses of this.

Responsibilities per spec §3: validating the executable/model,
building the command, launching and monitoring the subprocess,
streaming stdout/stderr, tracking progress, handling cancellation and
timeout, validating output, and writing per-job raw logs (spec §10-11:
stdout.log, stderr.log, command.txt, environment.json, result.json) --
all in one place so nothing above this layer has to know how any of
it works.
"""

from __future__ import annotations

import json
import shlex
import shutil
import threading
from pathlib import Path
from typing import Dict, Optional

from core.events.bus import EventBus
from core.events.types import EventType
from core.models.engine_models import (
    EngineCapabilities,
    EngineValidationResult,
    ErrorCategory,
    EngineError,
    ReconstructionRequest,
    ReconstructionResult,
    ResourceEstimate,
    UpscaleRequest,
    UpscaleResult,
)
from engines.base.engine import ReconstructionEngine
from engines.custom_cli.command_builder import ConfigurationError, build_command, values_for_reconstruction, values_for_upscale
from engines.custom_cli.config import CLIEngineConfig
from engines.custom_cli.process_runner import ProcessResult, run_process
from image.validation import validate_output_image
from pipeline.progress import GenericProgressParser, ProgressParser


class CustomCLIEngine(ReconstructionEngine):
    def __init__(
        self,
        config: CLIEngineConfig,
        capabilities: Optional[EngineCapabilities] = None,
        progress_parser: Optional[ProgressParser] = None,
        event_bus: Optional[EventBus] = None,
        logs_dir: Optional[str] = None,
    ) -> None:
        self.config = config
        self.name = config.name
        self._capabilities = capabilities or EngineCapabilities()
        self._align_config_with_capabilities()
        self.progress_parser = progress_parser or GenericProgressParser()
        self.event_bus = event_bus
        self.logs_dir = Path(logs_dir) if logs_dir else None
        self._cancel_flags: Dict[str, threading.Event] = {}

    # -- ReconstructionEngine interface -----------------------------------

    def validate(self) -> EngineValidationResult:
        messages, errors = [], []

        resolved = self.config.executable
        exists = Path(resolved).exists() or shutil.which(resolved) is not None
        if not exists:
            errors.append(f"Executable not found: {resolved}")
        else:
            messages.append(f"Executable found: {resolved}")

        if self.config.model_path:
            if Path(self.config.model_path).exists():
                messages.append(f"Model found: {self.config.model_path}")
            else:
                errors.append(f"Model not found: {self.config.model_path}")

        return EngineValidationResult(is_valid=not errors, messages=messages, errors=errors)

    def get_capabilities(self) -> EngineCapabilities:
        return self._capabilities

    def _align_config_with_capabilities(self) -> None:
        """Guarantees the command builder can NEVER send a flag for a
        parameter get_capabilities() declares unsupported (spec §20:
        "do not pretend [an engine] supports mask/inpainting if the
        actual backend does not"). Every current and future subclass
        gets this for free -- no subclass has to remember to do it
        itself, and it can't silently drift out of sync again."""
        caps = self._capabilities
        if not caps.supports_prompt:
            self.config.prompt_argument = None
        if not caps.supports_negative_prompt:
            self.config.negative_prompt_argument = None
        if not caps.supports_strength:
            self.config.strength_argument = None
        if not caps.supports_seed:
            self.config.seed_argument = None
        if not caps.supports_steps:
            self.config.steps_argument = None
        if not caps.supports_guidance:
            self.config.guidance_argument = None
        if not caps.supports_mask:
            self.config.mask_argument = None
        if not caps.supports_upscale:
            self.config.scale_argument = None

    def cancel(self, job_id: str) -> None:
        self._cancel_flags.setdefault(job_id, threading.Event()).set()

    def estimate_resources(self, request: ReconstructionRequest) -> ResourceEstimate:
        return ResourceEstimate(notes="CustomCLIEngine has no built-in resource profiling for an arbitrary executable.")

    def reconstruct(self, request: ReconstructionRequest) -> ReconstructionResult:
        values = values_for_reconstruction(request, self.config.model_path)
        return self._execute(request.job_id, request.part_id, values, request.output_path)

    def upscale(self, request: UpscaleRequest) -> UpscaleResult:
        values = values_for_upscale(request, self.config.model_path)
        result = self._execute(request.job_id, request.part_id, values, request.output_path)
        return UpscaleResult(
            success=result.success, output_path=result.output_path, duration_seconds=result.duration_seconds,
            command=result.command, stdout=result.stdout, stderr=result.stderr, error=result.error,
        )

    # -- internals ----------------------------------------------------------

    def _execute(self, job_id: str, part_id: str, values: Dict, output_path: str) -> ReconstructionResult:
        try:
            command = build_command(self.config, values)
        except ConfigurationError as exc:
            return ReconstructionResult(success=False, error=EngineError(ErrorCategory.CONFIGURATION_ERROR, str(exc)))

        cancel_event = self._cancel_flags.setdefault(job_id, threading.Event())

        def _on_stdout(line: str) -> None:
            if self.event_bus is not None:
                self.event_bus.publish(EventType.ENGINE_STDOUT, job_id=job_id, part_id=part_id,
                                        engine=self.name, line=line)
                update = self.progress_parser.parse_line(line)
                if update is not None:
                    self.event_bus.publish(EventType.JOB_PROGRESS, job_id=job_id, part_id=part_id,
                                            engine=self.name, step=update.step,
                                            total_steps=update.total_steps, progress=update.progress)

        def _on_stderr(line: str) -> None:
            if self.event_bus is not None:
                self.event_bus.publish(EventType.ENGINE_STDERR, job_id=job_id, part_id=part_id,
                                        engine=self.name, line=line)

        if self.event_bus is not None:
            self.event_bus.publish(EventType.ENGINE_STARTED, job_id=job_id, part_id=part_id,
                                    engine=self.name, command=command)

        try:
            proc_result = run_process(
                command,
                cwd=self.config.working_directory,
                env=self.config.environment,
                timeout_seconds=self.config.timeout_seconds,
                cancel_event=cancel_event,
                on_stdout_line=_on_stdout,
                on_stderr_line=_on_stderr,
            )
        except OSError as exc:
            # Most common real-world failure: the configured executable
            # doesn't exist, isn't executable, or the working directory
            # is invalid. subprocess.Popen raises this directly rather
            # than returning a nonzero exit code -- without this catch
            # it would crash the worker thread with a raw traceback
            # instead of a normal, actionable failed result (spec §28).
            error = EngineError(
                ErrorCategory.SUBPROCESS_ERROR,
                f"Could not launch '{self.config.executable}': {exc}",
                details=str(exc),
            )
            if self.event_bus is not None:
                self.event_bus.publish(EventType.JOB_ERROR, job_id=job_id, part_id=part_id,
                                        engine=self.name, message=error.message)
            return ReconstructionResult(success=False, command=command, error=error)

        self._write_job_logs(job_id, proc_result)

        if proc_result.cancelled:
            error = EngineError(ErrorCategory.UNKNOWN_ERROR, "Cancelled by user")
            if self.event_bus is not None:
                self.event_bus.publish(EventType.JOB_CANCELLED, job_id=job_id, part_id=part_id, engine=self.name)
            return ReconstructionResult(success=False, duration_seconds=proc_result.duration_seconds,
                                         command=command, stdout=proc_result.stdout, stderr=proc_result.stderr, error=error)

        if proc_result.timed_out:
            error = EngineError(ErrorCategory.SUBPROCESS_ERROR,
                                 f"Process timed out after {self.config.timeout_seconds}s")
            return self._failure(command, proc_result, error, job_id, part_id)

        if proc_result.exit_code != 0:
            error = EngineError(ErrorCategory.SUBPROCESS_ERROR,
                                 f"Process exited with code {proc_result.exit_code}",
                                 details=proc_result.stderr)
            return self._failure(command, proc_result, error, job_id, part_id)

        output_check = validate_output_image(output_path)
        if not output_check.ok:
            error = EngineError(ErrorCategory.OUTPUT_ERROR, "; ".join(output_check.errors))
            return self._failure(command, proc_result, error, job_id, part_id)

        if self.event_bus is not None:
            self.event_bus.publish(EventType.JOB_COMPLETED, job_id=job_id, part_id=part_id,
                                    engine=self.name, output_path=output_path)

        return ReconstructionResult(
            success=True, output_path=output_path, duration_seconds=proc_result.duration_seconds,
            command=command, stdout=proc_result.stdout, stderr=proc_result.stderr,
        )

    def _failure(self, command, proc_result: ProcessResult, error: EngineError, job_id, part_id) -> ReconstructionResult:
        if self.event_bus is not None:
            self.event_bus.publish(EventType.JOB_ERROR, job_id=job_id, part_id=part_id,
                                    engine=self.name, message=error.message)
        return ReconstructionResult(success=False, duration_seconds=proc_result.duration_seconds,
                                     command=command, stdout=proc_result.stdout, stderr=proc_result.stderr, error=error)

    def _write_job_logs(self, job_id: str, proc_result: ProcessResult) -> None:
        """spec §10-11: never hide raw subprocess output, and every job
        must be able to reproduce exactly how it was run."""
        if self.logs_dir is None:
            return
        job_dir = self.logs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        (job_dir / "stdout.log").write_text(proc_result.stdout, encoding="utf-8")
        (job_dir / "stderr.log").write_text(proc_result.stderr, encoding="utf-8")
        (job_dir / "command.txt").write_text(
            " ".join(shlex.quote(part) for part in proc_result.command), encoding="utf-8"
        )
        (job_dir / "environment.json").write_text(
            json.dumps(self.config.environment, indent=2), encoding="utf-8"
        )
        (job_dir / "result.json").write_text(json.dumps({
            "exit_code": proc_result.exit_code,
            "duration_seconds": proc_result.duration_seconds,
            "timed_out": proc_result.timed_out,
            "cancelled": proc_result.cancelled,
            "start_time": proc_result.start_time,
            "end_time": proc_result.end_time,
            "cwd": proc_result.cwd,
        }, indent=2), encoding="utf-8")
