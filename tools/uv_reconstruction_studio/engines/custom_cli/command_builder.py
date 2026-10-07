"""
Command builder (spec §5, §105): turns a CLIEngineConfig + a values
dict into the exact argv list that will be launched, or raises
ConfigurationError if a *required* placeholder has no value. This is
also what powers "View Generated Command" / "Copy Command" (spec
§105) -- the returned list is exactly what gets run, nothing hidden.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from engines.custom_cli.config import CLIEngineConfig

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class ConfigurationError(Exception):
    pass


def _referenced_keys(tokens: List[str]) -> List[str]:
    keys: List[str] = []
    for token in tokens:
        keys.extend(_PLACEHOLDER_RE.findall(token))
    return keys


def _substitute_group(
    tokens: List[str], values: Dict[str, Optional[str]], required: bool
) -> Optional[List[str]]:
    if not tokens:
        return [] if required else None
    missing = [k for k in _referenced_keys(tokens) if values.get(k) is None]
    if missing:
        if required:
            raise ConfigurationError(
                f"Missing required value(s) for command template {tokens!r}: {missing}"
            )
        return None
    return [t.format(**values) for t in tokens]


def build_command(config: CLIEngineConfig, values: Dict[str, Optional[str]]) -> List[str]:
    values = dict(values)
    values.setdefault("model_path", config.model_path)

    command: List[str] = [config.executable]
    command.extend(_substitute_group(config.fixed_arguments, values, required=True) or [])
    command.extend(_substitute_group(config.input_argument, values, required=True) or [])
    command.extend(_substitute_group(config.output_argument, values, required=True) or [])

    for group in (
        config.prompt_argument, config.negative_prompt_argument, config.strength_argument,
        config.seed_argument, config.steps_argument, config.guidance_argument,
        config.mask_argument, config.scale_argument,
    ):
        if group is None:
            continue
        substituted = _substitute_group(group, values, required=False)
        if substituted is not None:
            command.extend(substituted)

    command.extend(config.extra_arguments)
    return command


def values_for_reconstruction(request, model_path: Optional[str]) -> Dict[str, Optional[str]]:
    return {
        "input": request.input_path,
        "output": request.output_path,
        "model_path": model_path,
        "prompt": request.prompt or None,
        "negative_prompt": request.negative_prompt or None,
        "strength": None if request.strength is None else str(request.strength),
        "seed": None if request.seed is None else str(request.seed),
        "steps": None if request.steps is None else str(request.steps),
        "guidance": None if request.guidance is None else str(request.guidance),
        "mask": request.mask_path or None,
        "scale": None,
    }


def values_for_upscale(request, model_path: Optional[str]) -> Dict[str, Optional[str]]:
    return {
        "input": request.input_path,
        "output": request.output_path,
        "model_path": model_path,
        "prompt": None,
        "negative_prompt": None,
        "strength": None,
        "seed": None,
        "steps": None,
        "guidance": None,
        "mask": None,
        "scale": str(request.scale),
    }
