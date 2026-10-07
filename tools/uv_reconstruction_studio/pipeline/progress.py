"""
Progress parser framework (spec §35).

Different CLI engines print progress differently, so there's one
parser per engine "dialect" plus a configurable regex fallback. If a
line doesn't match, `parse_line` returns None -- callers MUST treat
that as "unknown", never invent a percentage (spec §35's explicit
rule: show "Processing..." instead of fabricating progress).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProgressUpdate:
    step: Optional[int] = None
    total_steps: Optional[int] = None
    progress: Optional[float] = None  # 0.0-1.0, only when derivable


class ProgressParser(ABC):
    @abstractmethod
    def parse_line(self, line: str) -> Optional[ProgressUpdate]:
        """Return None if this line carries no progress information --
        never guess."""
        raise NotImplementedError


class GenericProgressParser(ProgressParser):
    """Configurable regex fallback. Default pattern matches the common
    "Step N/M" style most diffusion CLIs print."""

    def __init__(self, pattern: str = r"[Ss]tep\s+(\d+)\s*/\s*(\d+)"):
        self._regex = re.compile(pattern)

    def parse_line(self, line: str) -> Optional[ProgressUpdate]:
        match = self._regex.search(line)
        if not match:
            return None
        step, total = int(match.group(1)), int(match.group(2))
        progress = (step / total) if total else None
        return ProgressUpdate(step=step, total_steps=total, progress=progress)


class Flux2ProgressParser(GenericProgressParser):
    """Flux.2 CLI progress format. NOTE: this assumes a conventional
    'Step N/M' style, since no real Flux.2 CLI is available to observe
    in this environment (spec §111.3: reasonable engineering decision,
    not an invented external API contract). Update the pattern here
    once the actual CLI's real output format is known -- nothing else
    in the engine needs to change (spec §35's whole point)."""

    def __init__(self):
        super().__init__(pattern=r"[Ss]tep\s+(\d+)\s*/\s*(\d+)")


class RealESRGANProgressParser(ProgressParser):
    """real-esrgan-ncnn-vulkan typically reports a bare percentage per
    tile (e.g. "12.34%") rather than step/total -- same caveat as
    above regarding an unobserved real binary."""

    def __init__(self, pattern: str = r"(\d{1,3}(?:\.\d+)?)\s*%"):
        self._regex = re.compile(pattern)

    def parse_line(self, line: str) -> Optional[ProgressUpdate]:
        match = self._regex.search(line)
        if not match:
            return None
        pct = float(match.group(1))
        return ProgressUpdate(progress=min(1.0, max(0.0, pct / 100.0)))
