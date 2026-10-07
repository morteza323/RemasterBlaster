"""
Flux2Engine (spec §9 of the phase plan, §2): the "initial/default"
engine, but architecturally just one more CustomCLIEngine configuration
(spec §1's core rule -- nothing above this file knows Flux.2 exists).

IMPORTANT CAVEAT (spec §111.3 "make reasonable engineering decisions
without inventing unsupported external APIs"): no real Flux.2 CLI
binary is available in this environment to observe its actual flags
or output format. The argument names below (--prompt, --strength,
--seed, ...) and the capability flags follow the most common
conventions for local diffusion CLIs and spec §2's own example
Settings panel, but they are NOT verified against a real Flux.2 Klein
release. Everything engine-specific lives in this one file and in
CLIEngineConfig's templates -- update them here once the real CLI's
contract is known; nothing else in the application needs to change.
"""

from __future__ import annotations

from typing import Optional

from core.events.bus import EventBus
from core.models.engine_models import EngineCapabilities
from engines.custom_cli.config import CLIEngineConfig
from engines.custom_cli.engine import CustomCLIEngine
from engines.flux2.config import Flux2Profile
from pipeline.progress import Flux2ProgressParser, ProgressParser

# Conservative: mask/inpaint support is unconfirmed for Flux.2 Klein,
# so it's declared unsupported until verified -- the GUI will disable
# those controls accordingly (spec §4, §102) rather than show ones
# that might silently no-op against the real CLI.
DEFAULT_CAPABILITIES = EngineCapabilities(
    supports_prompt=True,
    supports_negative_prompt=True,
    supports_strength=True,
    supports_seed=True,
    supports_steps=True,
    supports_guidance=True,
    supports_mask=False,
    supports_inpaint=False,
    supports_batch=False,
    supports_upscale=False,
)


class Flux2Engine(CustomCLIEngine):
    def __init__(
        self,
        profile: Optional[Flux2Profile] = None,
        event_bus: Optional[EventBus] = None,
        logs_dir: Optional[str] = None,
        progress_parser: Optional[ProgressParser] = None,
        capabilities: Optional[EngineCapabilities] = None,
    ) -> None:
        self.profile = profile or Flux2Profile()

        fixed_arguments = ["--model", self.profile.model, "--quantization", self.profile.quantization,
                            "--gpu", self.profile.gpu, "--vram-mode", self.profile.vram_mode]
        if self.profile.model_path:
            fixed_arguments += ["--model-path", self.profile.model_path]
        fixed_arguments += list(self.profile.extra_arguments)

        config = CLIEngineConfig(
            name="flux2",
            executable=self.profile.executable,
            model_path=self.profile.model_path,
            fixed_arguments=fixed_arguments,
            timeout_seconds=self.profile.timeout_seconds,
        )

        super().__init__(
            config,
            capabilities=capabilities or DEFAULT_CAPABILITIES,
            progress_parser=progress_parser or Flux2ProgressParser(),
            event_bus=event_bus,
            logs_dir=logs_dir,
        )
        self.name = "flux2"
