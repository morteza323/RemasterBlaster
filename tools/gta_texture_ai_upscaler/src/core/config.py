"""
Configuration loader and typed settings for GTA Texture AI Upscaler.
v3.0 – aspect-ratio preserving dynamic resolution + hyper-realistic defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dataclass
class ModelsConfig:
    diffusion_gguf: str = "models/flux-2-klein-4b-Q4_K_M.gguf"
    text_encoder: str = "models/qwen_3_4b_fp4_flux2.safetensors"
    vae: str = "models/flux2-vae.safetensors"
    sd_cli: str = "sd-cli"
    # Optional ESRGAN / RealESRGAN post-upscale (4x). Empty string = disabled.
    upscale_model: str = ""


@dataclass
class InferenceConfig:
    # Dynamic resolution (preferred) — base size before ESRGAN x4
    target_long_side: int = 512
    max_side: int = 512
    min_side: int = 64
    size_multiple: int = 8
    # Fallback fixed size (only used if image size cannot be read)
    width: int = 512
    height: int = 512
    steps: int = 6
    cfg_scale: float = 1.0
    sampling_method: str = "euler"
    # Lower strength = closer to original (less hallucination / color shift)
    strength: float = 0.42
    # V6: pre-upscale the source before Flux so tiny textures are not fed as blocky pixels.
    pre_upscale_source: bool = True
    pre_upscale_filter: str = "LANCZOS"
    post_sharpen: float = 1.10
    post_detail_radius: float = 0.7
    diffusion_fa: bool = True
    offload_to_cpu: bool = True
    preserve_filename: bool = True
    force_png_extension: bool = False
    # ESRGAN post-upscale repeats (1 = one 4x pass)
    upscale_repeats: int = 1
    upscale_tile_size: int = 128
    png_compress_level: int = 6
    fidelity_fusion: bool = False
    filename_material_hints: bool = False
    image_analysis_hints: bool = True
    gta_knowledge_base: bool = True
    custom_knowledge_txt: str = ""
    uv_context_hints: bool = True
    preserve_source_colors: bool = True
    color_hue_strength: float = 0.98
    color_saturation_strength: float = 0.92
    color_value_drift_limit: float = 0.22
    color_detail_strength: float = 0.55
    fidelity_mid_strength: float = 0.62
    fidelity_high_strength: float = 0.88
    fidelity_blur_small: float = 1.6
    fidelity_blur_large: float = 5.0
    fidelity_color_strength: float = 0.90


@dataclass
class KnowledgeConfig:
    builtin_file: str = "knowledge/gta_sa_keywords.txt"
    custom_txt: str = ""
    enabled: bool = True
    max_prompt_entries: int = 12


@dataclass
class PromptConfig:
    master_prompt_file: str = "config/master_prompt.txt"
    embedding_cache: str = "data/embeddings/master_prompt.emb"


@dataclass
class HardwareConfig:
    gpu_pause_temp_c: int = 72
    gpu_resume_temp_c: int = 62
    thermal_check_interval: int = 60
    cpu_pause_temp_c: int = 75
    cpu_resume_temp_c: int = 65
    max_ram_percent: int = 82
    min_free_ram_mb: int = 2048
    min_free_vram_mb: int = 1200
    vram_warn_percent: int = 85
    max_cpu_percent: int = 92
    cpu_high_pause_seconds: int = 45
    rest_every_hours: float = 3.0
    rest_duration_minutes: int = 15


@dataclass
class BatchConfig:
    jobs_before_cooldown: int = 30
    cooldown_seconds: int = 20
    max_retries: int = 2
    atomic_write: bool = True
    consecutive_fail_pause: int = 5
    consecutive_fail_pause_seconds: int = 120


@dataclass
class DatabaseConfig:
    path: str = "data/jobs/jobs.db"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/upscaler.log"
    max_size_mb: int = 50
    backup_count: int = 5


@dataclass
class AppConfig:
    name: str = "GTA Texture AI Upscaler"
    version: str = "3.8.0"
    data_dir: str = "data"
    log_dir: str = "logs"
    output_dir: str = "output"
    input_dir: str = "input"
    handmade_dir: str = r"H:\gta sa textures\fucked up fixed with ai"
    models: ModelsConfig = field(default_factory=ModelsConfig)
    inference: InferenceConfig = field(default_factory=InferenceConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    batch: BatchConfig = field(default_factory=BatchConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    project_root: Path = field(default_factory=Path.cwd)

    def resolve_path(self, relative: str) -> Path:
        p = Path(relative)
        if p.is_absolute():
            return p
        return self.project_root / p


def _dict_to_dataclass(cls, data: Dict[str, Any]):
    if data is None:
        return cls()
    field_names = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in field_names}
    return cls(**filtered)


def load_config(config_path: Optional[str] = None, project_root: Optional[Path] = None) -> AppConfig:
    root = project_root or Path.cwd()
    if config_path is None:
        config_path = root / "config" / "settings.yaml"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    app_raw = raw.get("app", {})
    cfg = AppConfig(
        name=app_raw.get("name", "GTA Texture AI Upscaler"),
        version=app_raw.get("version", "3.0.0"),
        data_dir=app_raw.get("data_dir", "data"),
        log_dir=app_raw.get("log_dir", "logs"),
        output_dir=app_raw.get("output_dir", "output"),
        input_dir=app_raw.get("input_dir", "input"),
        handmade_dir=app_raw.get(
            "handmade_dir",
            r"H:\\gta sa textures\\fucked up fixed with ai",
        ),
        models=_dict_to_dataclass(ModelsConfig, raw.get("models")),
        inference=_dict_to_dataclass(InferenceConfig, raw.get("inference")),
        prompt=_dict_to_dataclass(PromptConfig, raw.get("prompt")),
        knowledge=_dict_to_dataclass(KnowledgeConfig, raw.get("knowledge")),
        hardware=_dict_to_dataclass(HardwareConfig, raw.get("hardware")),
        batch=_dict_to_dataclass(BatchConfig, raw.get("batch")),
        database=_dict_to_dataclass(DatabaseConfig, raw.get("database")),
        logging=_dict_to_dataclass(LoggingConfig, raw.get("logging")),
        project_root=root,
    )
    return cfg


def load_master_prompt(cfg: AppConfig) -> str:
    path = cfg.resolve_path(cfg.prompt.master_prompt_file)
    if not path.exists():
        raise FileNotFoundError(f"Master prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()
