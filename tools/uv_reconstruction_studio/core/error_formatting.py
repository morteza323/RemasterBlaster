"""
Human-actionable failure reports (spec §28, §70): turns a failed Job +
its EngineError into the structured message a person can actually act
on -- what failed, on which part/engine, the exact command that ran,
and a short "check this" list -- instead of a bare "Process failed" or
a raw Python traceback. Used by both the CLI (printed directly) and
the GUI (shown in an error dialog) so the two never diverge (spec §16).
"""

from __future__ import annotations

from typing import List, Optional

from core.models.engine_models import ErrorCategory
from core.models.job import Job
from core.models.part import Part

_CHECKLIST_BY_CATEGORY = {
    ErrorCategory.CONFIGURATION_ERROR: [
        "The engine's executable and model paths are set (Settings / --configure-engine)",
        "Required per-part fields (prompt, seed, etc.) are filled in for parameters this engine needs",
    ],
    ErrorCategory.SUBPROCESS_ERROR: [
        "The executable path is correct and the file actually exists at that path",
        "The executable has permission to run (on Windows: not blocked by SmartScreen/antivirus)",
        "GPU/VRAM settings match what's actually installed on this machine",
        "The full command below runs successfully when pasted into a terminal directly",
    ],
    ErrorCategory.MODEL_ERROR: [
        "The model path is correct and the model file/folder actually exists",
        "The model matches the quantization/profile configured for this engine",
    ],
    ErrorCategory.OUTPUT_ERROR: [
        "The output directory is writable",
        "There is enough disk space",
        "The engine actually finished (see stdout/stderr below) rather than exiting early",
    ],
    ErrorCategory.INPUT_ERROR: [
        "The input image exists and is a valid, readable image file",
    ],
    ErrorCategory.GPU_ERROR: [
        "A compatible GPU/driver is installed and detected (see --diagnostics)",
        "VRAM mode / GPU index in the engine profile matches actual hardware",
    ],
    ErrorCategory.FILE_SYSTEM_ERROR: [
        "The relevant path exists, is spelled correctly, and is writable",
        "No other process has the file open/locked",
    ],
}

_DEFAULT_CHECKLIST = [
    "Run --diagnostics (or the Diagnostics panel) to check engine/executable/model status",
    "Check the engine's log for this job under logs/engine/<engine>.log and logs/jobs/<job_id>.log",
]


def format_job_failure(job: Job, part: Optional[Part] = None, engine_display_name: Optional[str] = None) -> str:
    """Builds the multi-line report described in spec §28. Safe to
    call even with partial information -- every field is optional
    except the job itself."""
    lines: List[str] = []

    engine_label = engine_display_name or job.engine
    part_label = (part.name if part is not None else None) or job.part_id
    lines.append(f"{engine_label} reconstruction failed for {part_label}.")
    lines.append("")
    lines.append(f"Engine:    {engine_label}")
    lines.append(f"Part:      {part_label}")
    if job.error_category:
        lines.append(f"Category:  {job.error_category}")
    if job.error_message:
        lines.append(f"Error:     {job.error_message}")
    lines.append("")

    category = ErrorCategory(job.error_category) if job.error_category in {c.value for c in ErrorCategory} else None
    checklist = _CHECKLIST_BY_CATEGORY.get(category, _DEFAULT_CHECKLIST) if category else _DEFAULT_CHECKLIST
    lines.append("Check:")
    for i, item in enumerate(checklist, start=1):
        lines.append(f"  {i}. {item}")

    return "\n".join(lines)


def format_engine_error_summary(engine_name: str, part_id: str, message: str, command: Optional[List[str]] = None) -> str:
    """A shorter one-shot version for inline use (status bars, single-line
    log entries) where the full multi-line report above is too much."""
    text = f"[{engine_name}] {part_id}: {message}"
    if command:
        text += f"\n  command: {' '.join(command)}"
    return text
