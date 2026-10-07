"""
Engine-facing data contracts (spec §3-4).

These types are the *only* thing the rest of the application knows
about an engine. Nothing outside engines/ should ever branch on
"if engine_name == 'flux2'" -- it should ask an engine for its
EngineCapabilities and adapt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Dict, List, Optional


@dataclass
class EngineCapabilities:
    supports_prompt: bool = False
    supports_negative_prompt: bool = False
    supports_strength: bool = False
    supports_seed: bool = False
    supports_mask: bool = False
    supports_inpaint: bool = False
    supports_steps: bool = False
    supports_guidance: bool = False
    supports_batch: bool = False
    supports_upscale: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return dict(self.__dict__)


@dataclass
class EngineValidationResult:
    is_valid: bool
    messages: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@unique
class ErrorCategory(str, Enum):
    INPUT_ERROR = "INPUT_ERROR"
    OUTPUT_ERROR = "OUTPUT_ERROR"
    ENGINE_ERROR = "ENGINE_ERROR"
    MODEL_ERROR = "MODEL_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    SUBPROCESS_ERROR = "SUBPROCESS_ERROR"
    MEMORY_ERROR = "MEMORY_ERROR"
    GPU_ERROR = "GPU_ERROR"
    FILE_SYSTEM_ERROR = "FILE_SYSTEM_ERROR"
    PROJECT_ERROR = "PROJECT_ERROR"
    MASK_ERROR = "MASK_ERROR"
    REASSEMBLY_ERROR = "REASSEMBLY_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class EngineError(Exception):
    """Raised by an engine adapter; always carries a structured category
    (spec §71) so the GUI never has to show a bare traceback (spec §70)."""

    def __init__(self, category: ErrorCategory, message: str, details: str = ""):
        super().__init__(message)
        self.category = category
        self.message = message
        self.details = details


@dataclass
class ReconstructionRequest:
    job_id: str
    part_id: str
    input_path: str
    output_path: str
    prompt: str = ""
    negative_prompt: str = ""
    strength: float = 1.0
    steps: Optional[int] = None
    guidance: Optional[float] = None
    seed: Optional[int] = None
    mask_path: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReconstructionResult:
    success: bool
    output_path: Optional[str] = None
    actual_seed: Optional[int] = None
    duration_seconds: Optional[float] = None
    command: Optional[List[str]] = None
    stdout: str = ""
    stderr: str = ""
    error: Optional[EngineError] = None


@dataclass
class UpscaleRequest:
    job_id: str
    part_id: str
    input_path: str
    output_path: str
    scale: float = 2.0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UpscaleResult:
    success: bool
    output_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    command: Optional[List[str]] = None
    stdout: str = ""
    stderr: str = ""
    error: Optional[EngineError] = None


@dataclass
class ResourceEstimate:
    estimated_vram_mb: Optional[int] = None
    estimated_ram_mb: Optional[int] = None
    estimated_duration_seconds: Optional[float] = None
    notes: str = ""
