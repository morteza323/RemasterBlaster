"""Phase 13 tests: prompt layer composition and material/prompt presets."""

from __future__ import annotations

import unittest

from core.models.part import Bounds, Part
from prompts.composer import PromptComposition, compose_final_prompt
from prompts.presets import MaterialPreset, PresetManager, PromptPreset


class TestComposer(unittest.TestCase):
    def test_all_layers_concatenate_in_order(self):
        result = compose_final_prompt(master="game texture restoration", material="high quality leather grain",
                                       part="dark brown strap", user_override="preserve stitching")
        self.assertEqual(
            result,
            "game texture restoration, high quality leather grain, dark brown strap, preserve stitching",
        )

    def test_empty_layers_are_skipped(self):
        result = compose_final_prompt(master="base", material="", part="", user_override="  ")
        self.assertEqual(result, "base")

    def test_composition_dataclass_matches_function(self):
        comp = PromptComposition(master="a", material="b", part="c", user_override="d")
        self.assertEqual(comp.final_prompt(), compose_final_prompt("a", "b", "c", "d"))


class TestPresetManager(unittest.TestCase):
    def test_builtin_materials_present(self):
        manager = PresetManager()
        self.assertIn("Leather", manager.list_materials())
        self.assertIn("Logo", manager.list_materials())
        self.assertEqual(len(manager.list_materials()), 19)

    def test_apply_material_sets_only_declared_fields(self):
        manager = PresetManager()
        part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10), guidance=7.5)
        manager.apply_material_to_part(part, "Leather")
        self.assertEqual(part.material, "Leather")
        self.assertEqual(part.strength, 0.45)
        self.assertIn("leather grain", manager.get_material("Leather").prompt_prefix)
        # guidance wasn't declared by the Leather preset -- must be untouched
        self.assertEqual(part.guidance, 7.5)

    def test_material_prompt_fragment_feeds_the_composer(self):
        # spec §22: the material *layer* of the final prompt comes from
        # the preset's prompt_prefix/suffix, composed alongside the
        # part's own prompt -- it is not written into part.prompt directly.
        manager = PresetManager()
        leather = manager.get_material("Leather")
        material_fragment = ", ".join(f for f in (leather.prompt_prefix, leather.prompt_suffix) if f)
        final = compose_final_prompt(master="game texture restoration", material=material_fragment,
                                      part="dark brown strap")
        self.assertIn("leather grain", final)
        self.assertIn("dark brown strap", final)

    def test_apply_unknown_material_raises(self):
        manager = PresetManager()
        part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
        with self.assertRaises(KeyError):
            manager.apply_material_to_part(part, "Unobtainium")

    def test_custom_material_preset(self):
        manager = PresetManager()
        manager.add_material(MaterialPreset(name="My Character Skin", default_strength=0.15,
                                             prompt_prefix="stylized character skin"))
        part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
        manager.apply_material_to_part(part, "My Character Skin")
        self.assertEqual(part.strength, 0.15)

    def test_prompt_preset_apply(self):
        manager = PresetManager()
        manager.add_prompt_preset(PromptPreset(name="Metal Remaster", prompt="brushed metal", strength=0.7))
        part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
        manager.apply_prompt_preset_to_part(part, "Metal Remaster")
        self.assertEqual(part.prompt, "brushed metal")
        self.assertEqual(part.strength, 0.7)

    def test_export_import_round_trip(self):
        manager = PresetManager()
        manager.add_prompt_preset(PromptPreset(name="Custom", prompt="test prompt"))
        manager.add_material(MaterialPreset(name="CustomMat", default_strength=0.33))

        exported = manager.export_json()
        restored = PresetManager.import_json(exported)

        self.assertIn("Custom", restored.list_prompt_presets())
        self.assertEqual(restored.get_prompt_preset("Custom").prompt, "test prompt")
        self.assertEqual(restored.get_material("CustomMat").default_strength, 0.33)
        # builtins from the original set are still there too
        self.assertIn("Leather", restored.list_materials())


if __name__ == "__main__":
    unittest.main()
