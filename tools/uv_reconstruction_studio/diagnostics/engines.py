"""
Engine diagnostics (spec §49-50): runs validate() against every
registered engine and returns structured, GUI/CLI-renderable results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from engines.registry import EngineRegistry


@dataclass
class EngineDiagnostic:
    name: str
    is_valid: bool
    messages: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def run_engine_diagnostics(registry: EngineRegistry) -> List[EngineDiagnostic]:
    results: List[EngineDiagnostic] = []
    for name in registry.list_engines():
        engine = registry.create(name)
        validation = engine.validate()
        results.append(EngineDiagnostic(
            name=name, is_valid=validation.is_valid,
            messages=list(validation.messages), errors=list(validation.errors),
        ))
    return results
