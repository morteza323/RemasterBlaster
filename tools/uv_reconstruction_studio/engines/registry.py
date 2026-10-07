"""
EngineRegistry (spec §4, §83): where engines register themselves so
the rest of the app can look one up by name without importing it
directly. Adding a new engine later means writing a new adapter class
and calling `register()` -- nothing else in the application changes.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Type

from engines.base.engine import ReconstructionEngine


class EngineRegistry:
    def __init__(self) -> None:
        self._factories: Dict[str, Callable[..., ReconstructionEngine]] = {}
        self._default: Optional[str] = None

    def register(
        self,
        name: str,
        factory: Callable[..., ReconstructionEngine],
        set_default: bool = False,
    ) -> None:
        self._factories[name] = factory
        if set_default or self._default is None:
            self._default = name

    def create(self, name: str, **config) -> ReconstructionEngine:
        if name not in self._factories:
            available = ", ".join(sorted(self._factories)) or "(none registered)"
            raise KeyError(f"Unknown engine '{name}'. Available: {available}")
        return self._factories[name](**config)

    def list_engines(self) -> List[str]:
        return sorted(self._factories)

    def set_default(self, name: str) -> None:
        if name not in self._factories:
            raise KeyError(f"Cannot set default: unknown engine '{name}'")
        self._default = name

    def get_default(self) -> Optional[str]:
        return self._default


# Process-wide registry. A GUI/CLI session imports this single
# instance so engines registered at startup (mock, flux2, ...) are
# visible everywhere.
default_registry = EngineRegistry()
