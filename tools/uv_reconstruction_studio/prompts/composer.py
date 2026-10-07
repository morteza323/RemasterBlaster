"""
Prompt composition (spec §22-23): a final prompt is always built from
up to four layers -- Master + Material + Part + User Override -- kept
separately so the GUI can display and let the user edit any one layer
without destroying the others.
"""

from __future__ import annotations

from dataclasses import dataclass


def compose_final_prompt(master: str = "", material: str = "", part: str = "", user_override: str = "") -> str:
    segments = [master, material, part, user_override]
    return ", ".join(s.strip() for s in segments if s and s.strip())


@dataclass
class PromptComposition:
    """Mutable holder for the four layers, e.g. bound directly to four
    text fields in a future GUI panel (spec §22's four labeled boxes)."""

    master: str = ""
    material: str = ""
    part: str = ""
    user_override: str = ""

    def final_prompt(self) -> str:
        return compose_final_prompt(self.master, self.material, self.part, self.user_override)
