"""
RealESRGANEngine (spec §10 of the phase plan, §21 material presets'
"ESRGAN" processing mode): an upscale-only engine. Its capabilities
declare no prompt/strength/seed/mask support, so the GUI must disable
those controls for it (spec §4, §102) -- reconstruct() fails cleanly
rather than silently ignoring parameters it can't honor.

CAVEAT (same as Flux2Engine): the `-i/-o/-n/-s/-g` flags follow the
widely-published real-esrgan-ncnn-vulkan CLI convention, but are not
verified against a real binary in this environment. Adjust
CLIEngineConfig here if a different Real-ESRGAN build is used.
"""

from __future__ import annotations

from typing import Optional

from core.events.bus import EventBus
from core.models.engine_models import (
    EngineCapabilities,
    ErrorCategory,
    EngineError,
    ReconstructionRequest,
    ReconstructionResult,
)
from engines.custom_cli.config import CLIEngineConfig
from engines.custom_cli.engine import CustomCLIEngine
from engines.realesrgan.config import RealESRGANProfile
from pipeline.progress import ProgressParser, RealESRGANProgressParser

DEFAULT_CAPABILITIES = EngineCapabilities(supports_upscale=True)


class RealESRGANEngine(CustomCLIEngine):
    def __init__(
        self,
        profile: Optional[RealESRGANProfile] = None,
        event_bus: Optional[EventBus] = None,
        logs_dir: Optional[str] = None,
        progress_parser: Optional[ProgressParser] = None,
        capabilities: Optional[EngineCapabilities] = None,
    ) -> None:
        self.profile = profile or RealESRGANProfile()

        fixed_arguments = ["-n", self.profile.model_name]
        if self.profile.model_path:
            fixed_arguments += ["-m", self.profile.model_path]
        if self.profile.tile_size:
            fixed_arguments += ["-t", str(self.profile.tile_size)]
        if self.profile.gpu_id:
            fixed_arguments += ["-g", self.profile.gpu_id]
        fixed_arguments += list(self.profile.extra_arguments)

        config = CLIEngineConfig(
            name="realesrgan",
            executable=self.profile.executable,
            fixed_arguments=fixed_arguments,
            input_argument=["-i", "{input}"],
            output_argument=["-o", "{output}"],
            prompt_argument=None,
            negative_prompt_argument=None,
            strength_argument=None,
            seed_argument=None,
            steps_argument=None,
            guidance_argument=None,
            mask_argument=None,
            scale_argument=["-s", "{scale}"],
            timeout_seconds=self.profile.timeout_seconds,
        )

        super().__init__(
            config,
            capabilities=capabilities or DEFAULT_CAPABILITIES,
            progress_parser=progress_parser or RealESRGANProgressParser(),
            event_bus=event_bus,
            logs_dir=logs_dir,
        )
        self.name = "realesrgan"

    def reconstruct(self, request: ReconstructionRequest) -> ReconstructionResult:
        # spec §4/§102: an engine must not silently no-op on a
        # capability it doesn't declare -- fail with a clear reason.
        return ReconstructionResult(
            success=False,
            error=EngineError(
                ErrorCategory.CONFIGURATION_ERROR,
                "RealESRGANEngine does not support prompt-based reconstruct(); use upscale() instead "
                "(see get_capabilities(): supports_upscale=True, everything else False).",
            ),
        )
