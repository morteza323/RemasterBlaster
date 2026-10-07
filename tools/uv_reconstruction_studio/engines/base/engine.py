"""
The Engine Abstraction Layer (spec §1-3).

This is the ONE seam between the application and any external AI
tool. Flux.2 is just the first implementation of this interface --
nothing above this layer (queue, GUI, CLI, reassembly) is allowed to
import a Flux-specific module or assume a Flux-specific CLI flag.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.engine_models import (
    EngineCapabilities,
    EngineValidationResult,
    ReconstructionRequest,
    ReconstructionResult,
    ResourceEstimate,
    UpscaleRequest,
    UpscaleResult,
)


class ReconstructionEngine(ABC):
    """Base contract every AI backend adapter must implement.

    Responsibilities that belong HERE (inside a concrete engine), not
    in the generic pipeline code above it (spec §3):
      locating/validating the executable and model, validating input,
      building the command line, launching and monitoring the
      subprocess, parsing stdout/stderr, tracking progress, handling
      cancellation and timeout, collecting and validating output, and
      writing engine-specific logs. The pipeline only ever sees the
      standardized Request/Result types.
    """

    #: Unique, stable registry key (e.g. "flux2", "mock"). Never a
    #: human-readable display name -- that belongs in get_capabilities
    #: metadata or engine config, not the class.
    name: str = "unnamed_engine"

    @abstractmethod
    def validate(self) -> EngineValidationResult:
        """Check executable/model/config are usable. Never raises --
        failures are reported via EngineValidationResult.errors so the
        GUI's diagnostic panel (spec §49) always has something to show."""
        raise NotImplementedError

    @abstractmethod
    def get_capabilities(self) -> EngineCapabilities:
        """Declare which parameters this engine understands, so the
        GUI can disable controls it doesn't support (spec §4, §102)."""
        raise NotImplementedError

    @abstractmethod
    def reconstruct(self, request: ReconstructionRequest) -> ReconstructionResult:
        raise NotImplementedError

    @abstractmethod
    def upscale(self, request: UpscaleRequest) -> UpscaleResult:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, job_id: str) -> None:
        """Request graceful termination of a running job (spec §33).
        Must be safe to call even if job_id isn't running."""
        raise NotImplementedError

    @abstractmethod
    def estimate_resources(self, request: ReconstructionRequest) -> ResourceEstimate:
        raise NotImplementedError
