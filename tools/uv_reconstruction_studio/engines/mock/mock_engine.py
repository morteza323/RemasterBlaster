"""
MockEngine (spec §78-79): a fake reconstruction engine so the whole
application -- queue, events, logging, GUI once it exists -- can be
exercised and demoed without Flux.2 or any real model installed.

It simulates: loading, stepped progress, stdout/stderr lines, success,
failure (opt-in via request.extra), and cooperative cancellation.
"""

from __future__ import annotations

import random
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

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


class MockEngine(ReconstructionEngine):
    name = "mock"

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        total_steps: int = 8,
        step_delay: float = 0.0,
    ) -> None:
        self.event_bus = event_bus
        self.total_steps = total_steps
        self.step_delay = step_delay
        self._cancel_flags: dict[str, threading.Event] = {}

    # -- ReconstructionEngine interface -----------------------------------

    def validate(self) -> EngineValidationResult:
        # The mock engine has no external dependencies, so validation
        # always succeeds -- this is what makes Development Mode
        # (spec §79) usable with zero setup.
        return EngineValidationResult(
            is_valid=True,
            messages=["Mock engine requires no executable or model."],
        )

    def get_capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            supports_prompt=True,
            supports_negative_prompt=True,
            supports_strength=True,
            supports_seed=True,
            supports_mask=True,
            supports_inpaint=False,
            supports_steps=True,
            supports_guidance=True,
            supports_batch=False,
            supports_upscale=True,
        )

    def cancel(self, job_id: str) -> None:
        self._cancel_flags.setdefault(job_id, threading.Event()).set()

    def estimate_resources(self, request: ReconstructionRequest) -> ResourceEstimate:
        return ResourceEstimate(
            estimated_vram_mb=0,
            estimated_ram_mb=64,
            estimated_duration_seconds=self.total_steps * self.step_delay,
            notes="Mock engine performs no real inference.",
        )

    def reconstruct(self, request: ReconstructionRequest) -> ReconstructionResult:
        return self._run(request, is_upscale=False)

    def upscale(self, request: UpscaleRequest) -> UpscaleResult:
        result = self._run(request, is_upscale=True)
        return UpscaleResult(
            success=result.success,
            output_path=result.output_path,
            duration_seconds=result.duration_seconds,
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
            error=result.error,
        )

    # -- internals ----------------------------------------------------------

    def _emit(self, event_type: EventType, **data) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event_type, engine=self.name, **data)

    def _run(self, request, is_upscale: bool) -> ReconstructionResult:
        job_id = request.job_id
        part_id = request.part_id
        cancel_flag = self._cancel_flags.setdefault(job_id, threading.Event())
        command = [
            "mock-engine",
            "--mode", "upscale" if is_upscale else "reconstruct",
            "--input", request.input_path,
            "--output", request.output_path,
        ]
        stdout_lines = []
        start = time.time()

        self._emit(EventType.ENGINE_STARTED, job_id=job_id, part_id=part_id, command=command)
        stdout_lines.append("Loading mock model...")
        self._emit(EventType.ENGINE_STDOUT, job_id=job_id, part_id=part_id, line=stdout_lines[-1])

        simulate_failure = bool(getattr(request, "extra", {}).get("simulate_failure"))

        for step in range(1, self.total_steps + 1):
            if cancel_flag.is_set():
                self._emit(EventType.JOB_CANCELLED, job_id=job_id, part_id=part_id)
                return ReconstructionResult(
                    success=False,
                    duration_seconds=time.time() - start,
                    command=command,
                    stdout="\n".join(stdout_lines),
                    error=EngineError(ErrorCategory.UNKNOWN_ERROR, "Cancelled by user"),
                )

            if self.step_delay:
                time.sleep(self.step_delay)

            line = f"Step {step}/{self.total_steps}"
            stdout_lines.append(line)
            self._emit(
                EventType.JOB_PROGRESS,
                job_id=job_id,
                part_id=part_id,
                step=step,
                total_steps=self.total_steps,
                progress=step / self.total_steps,
            )
            self._emit(EventType.ENGINE_STDOUT, job_id=job_id, part_id=part_id, line=line)

        if simulate_failure:
            stderr = "mock-engine: simulated failure (request.extra['simulate_failure'])"
            self._emit(EventType.ENGINE_STDERR, job_id=job_id, part_id=part_id, line=stderr)
            error = EngineError(ErrorCategory.ENGINE_ERROR, "Simulated engine failure", stderr)
            self._emit(EventType.JOB_ERROR, job_id=job_id, part_id=part_id, message=error.message)
            return ReconstructionResult(
                success=False,
                duration_seconds=time.time() - start,
                command=command,
                stdout="\n".join(stdout_lines),
                stderr=stderr,
                error=error,
            )

        stdout_lines.append("Saving output...")
        self._emit(EventType.ENGINE_STDOUT, job_id=job_id, part_id=part_id, line=stdout_lines[-1])

        output_path = Path(request.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        input_path = Path(request.input_path)
        if input_path.exists():
            shutil.copyfile(input_path, output_path)
        else:
            # No real input available (e.g. a synthetic demo run) --
            # still produce a real, non-empty file so downstream
            # output validation (spec §60) has something to check.
            output_path.write_bytes(b"MOCK_OUTPUT")

        actual_seed = request.seed if request.seed is not None else random.randint(1, 2**31 - 1)
        duration = time.time() - start
        self._emit(EventType.JOB_COMPLETED, job_id=job_id, part_id=part_id, output_path=str(output_path))

        return ReconstructionResult(
            success=True,
            output_path=str(output_path),
            actual_seed=actual_seed,
            duration_seconds=duration,
            command=command,
            stdout="\n".join(stdout_lines),
        )
