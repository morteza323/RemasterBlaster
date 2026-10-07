"""
Material and prompt presets (spec §21, §24).

MaterialPreset bundles the defaults a material implies (strength,
steps, guidance, preferred engine/padding/feather, and prompt
prefix/suffix/negative fragments). PromptPreset is a user-saved
combination a person names and re-applies later ("My Character Skin",
"Metal Remaster"). Both are plain, JSON-exportable dataclasses (spec
§24: "editable and exportable as JSON").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.models.part import Part

BUILTIN_MATERIAL_NAMES = [
    "Skin", "Face", "Hair", "Cloth", "Leather", "Metal", "Wood", "Stone",
    "Concrete", "Plastic", "Rubber", "Glass", "Armor", "Weapon", "Logo",
    "Decal", "Organic", "Hard Surface", "Environment",
]


@dataclass
class MaterialPreset:
    name: str
    default_strength: Optional[float] = None
    default_steps: Optional[int] = None
    default_guidance: Optional[float] = None
    prompt_prefix: str = ""
    prompt_suffix: str = ""
    negative_prompt: str = ""
    preferred_engine: Optional[str] = None
    preferred_padding: Optional[int] = None
    preferred_feather: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MaterialPreset":
        return cls(**data)


@dataclass
class PromptPreset:
    """A user-saved, reusable prompt/settings combination (spec §24)."""

    name: str
    prompt: str = ""
    negative_prompt: str = ""
    strength: Optional[float] = None
    steps: Optional[int] = None
    guidance: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromptPreset":
        return cls(**data)


def _default_material_presets() -> Dict[str, MaterialPreset]:
    """A handful of built-ins get sensible starting prompt fragments;
    the rest are blank placeholders the user fills in (spec §21 lists
    19 names but doesn't mandate specific wording for each)."""
    seeded: Dict[str, MaterialPreset] = {
        "Skin": MaterialPreset(name="Skin", default_strength=0.25,
                                prompt_prefix="detailed human skin texture, subsurface scattering",
                                negative_prompt="plastic, waxy, blurry"),
        "Leather": MaterialPreset(name="Leather", default_strength=0.45,
                                   prompt_prefix="high quality leather grain, natural texture",
                                   negative_prompt="plastic, smooth, synthetic"),
        "Metal": MaterialPreset(name="Metal", default_strength=0.65,
                                 prompt_prefix="brushed metal surface, realistic specular highlights",
                                 negative_prompt="rusty, plastic, matte"),
        "Cloth": MaterialPreset(name="Cloth", default_strength=0.55,
                                 prompt_prefix="woven fabric texture, natural fiber detail",
                                 negative_prompt="plastic, shiny, smooth"),
        "Wood": MaterialPreset(name="Wood", default_strength=0.5,
                                prompt_prefix="natural wood grain texture",
                                negative_prompt="plastic, painted, smooth"),
        "Logo": MaterialPreset(name="Logo", default_strength=0.1,
                                prompt_prefix="crisp flat graphic, preserve exact shape and edges",
                                negative_prompt="blurry, distorted, painterly"),
    }
    for name in BUILTIN_MATERIAL_NAMES:
        seeded.setdefault(name, MaterialPreset(name=name))
    return seeded


class PresetManager:
    def __init__(self) -> None:
        self.material_presets: Dict[str, MaterialPreset] = _default_material_presets()
        self.prompt_presets: Dict[str, PromptPreset] = {}

    # -- material presets --------------------------------------------------

    def get_material(self, name: str) -> Optional[MaterialPreset]:
        return self.material_presets.get(name)

    def list_materials(self) -> List[str]:
        return sorted(self.material_presets)

    def add_material(self, preset: MaterialPreset) -> None:
        self.material_presets[preset.name] = preset

    def apply_material_to_part(self, part: Part, material_name: str) -> None:
        """spec §26's 'Apply Material' action: fills the part's
        settings from the preset's declared defaults. Only fields the
        preset actually sets (non-None) are overwritten -- a preset
        that doesn't specify e.g. guidance leaves the part's existing
        guidance untouched."""
        preset = self.material_presets.get(material_name)
        if preset is None:
            raise KeyError(f"No material preset named {material_name!r}")
        part.material = preset.name
        if preset.default_strength is not None:
            part.strength = preset.default_strength
        if preset.default_steps is not None:
            part.steps = preset.default_steps
        if preset.default_guidance is not None:
            part.guidance = preset.default_guidance
        if preset.preferred_engine is not None:
            part.engine = preset.preferred_engine
        if preset.preferred_padding is not None:
            part.padding = preset.preferred_padding
        if preset.preferred_feather is not None:
            part.feather = preset.preferred_feather
        if preset.negative_prompt:
            part.negative_prompt = preset.negative_prompt

    # -- prompt presets ----------------------------------------------------

    def add_prompt_preset(self, preset: PromptPreset) -> None:
        self.prompt_presets[preset.name] = preset

    def get_prompt_preset(self, name: str) -> Optional[PromptPreset]:
        return self.prompt_presets.get(name)

    def list_prompt_presets(self) -> List[str]:
        return sorted(self.prompt_presets)

    def apply_prompt_preset_to_part(self, part: Part, preset_name: str) -> None:
        preset = self.prompt_presets.get(preset_name)
        if preset is None:
            raise KeyError(f"No prompt preset named {preset_name!r}")
        part.prompt = preset.prompt
        if preset.negative_prompt:
            part.negative_prompt = preset.negative_prompt
        if preset.strength is not None:
            part.strength = preset.strength
        if preset.steps is not None:
            part.steps = preset.steps
        if preset.guidance is not None:
            part.guidance = preset.guidance

    # -- import/export (spec §24) -------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "materials": {k: v.to_dict() for k, v in self.material_presets.items()},
            "prompts": {k: v.to_dict() for k, v in self.prompt_presets.items()},
        }

    def export_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PresetManager":
        manager = cls()
        for name, payload in data.get("materials", {}).items():
            manager.material_presets[name] = MaterialPreset.from_dict(payload)
        for name, payload in data.get("prompts", {}).items():
            manager.prompt_presets[name] = PromptPreset.from_dict(payload)
        return manager

    @classmethod
    def import_json(cls, text: str) -> "PresetManager":
        return cls.from_dict(json.loads(text))
