"""
CLIEngineConfig (spec §5, §84-86): the "no hardcoded CLI assumptions"
contract. Every argument group is a list of tokens containing
{placeholder} markers; a group is only included in the built command
if every placeholder it references has a value for this request. That
one rule is what lets one engine class serve Flux.2, Real-ESRGAN, or
any future CLI tool purely through configuration (spec §84).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CLIEngineConfig:
    name: str
    executable: str
    model_path: Optional[str] = None
    working_directory: Optional[str] = None

    # Always included verbatim (after {model_path} substitution if referenced).
    fixed_arguments: List[str] = field(default_factory=list)

    # Required -- reconstruct()/upscale() raise ConfigurationError if
    # input/output values are somehow missing (they never should be).
    input_argument: List[str] = field(default_factory=lambda: ["--input", "{input}"])
    output_argument: List[str] = field(default_factory=lambda: ["--output", "{output}"])

    # Optional -- omitted whenever the referenced value is None/empty,
    # so the GUI's capability-driven control disabling (spec §102) is
    # mirrored exactly at the command-line level.
    prompt_argument: Optional[List[str]] = field(default_factory=lambda: ["--prompt", "{prompt}"])
    negative_prompt_argument: Optional[List[str]] = field(
        default_factory=lambda: ["--negative-prompt", "{negative_prompt}"]
    )
    strength_argument: Optional[List[str]] = field(default_factory=lambda: ["--strength", "{strength}"])
    seed_argument: Optional[List[str]] = field(default_factory=lambda: ["--seed", "{seed}"])
    steps_argument: Optional[List[str]] = field(default_factory=lambda: ["--steps", "{steps}"])
    guidance_argument: Optional[List[str]] = field(default_factory=lambda: ["--guidance", "{guidance}"])
    mask_argument: Optional[List[str]] = field(default_factory=lambda: ["--mask", "{mask}"])
    scale_argument: Optional[List[str]] = field(default_factory=lambda: ["--scale", "{scale}"])

    extra_arguments: List[str] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    timeout_seconds: Optional[float] = None

    def to_dict(self) -> Dict:
        return dict(self.__dict__)
