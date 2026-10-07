"""Tests for engine settings persistence (spec §24): global vs.
project scope, defaults, round-tripping into real profile dataclasses,
and the CLI commands that drive it."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engines.flux2.config import Flux2Profile
from engines.realesrgan.config import RealESRGANProfile
from project_system.settings import EngineSettingsManager, project_settings_manager


class TestEngineSettingsManager(unittest.TestCase):
    def test_save_and_load_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engines.json"
            manager = EngineSettingsManager(path)
            manager.save_profile("flux2", "My Profile", Flux2Profile(executable="/opt/flux-cli").to_dict())

            reloaded = EngineSettingsManager(path)
            data = reloaded.get_profile("flux2", "My Profile")
            self.assertIsNotNone(data)
            profile = Flux2Profile.from_dict(data)
            self.assertEqual(profile.executable, "/opt/flux-cli")

    def test_first_saved_profile_becomes_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = EngineSettingsManager(Path(tmp) / "engines.json")
            manager.save_profile("realesrgan", "A", RealESRGANProfile().to_dict())
            self.assertEqual(manager.get_default_profile_name("realesrgan"), "A")
            manager.save_profile("realesrgan", "B", RealESRGANProfile(model_name="other").to_dict())
            # second profile does NOT silently become default
            self.assertEqual(manager.get_default_profile_name("realesrgan"), "A")

    def test_explicit_set_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = EngineSettingsManager(Path(tmp) / "engines.json")
            manager.save_profile("flux2", "A", Flux2Profile().to_dict())
            manager.save_profile("flux2", "B", Flux2Profile().to_dict(), set_default=True)
            self.assertEqual(manager.get_default_profile_name("flux2"), "B")

    def test_delete_profile_reassigns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = EngineSettingsManager(Path(tmp) / "engines.json")
            manager.save_profile("flux2", "A", Flux2Profile().to_dict())
            manager.delete_profile("flux2", "A")
            self.assertIsNone(manager.get_default_profile("flux2"))

    def test_corrupt_file_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engines.json"
            path.write_text("{not valid json", encoding="utf-8")
            manager = EngineSettingsManager(path)
            self.assertEqual(manager.list_profiles("flux2"), {})

    def test_atomic_save_leaves_no_tmp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engines.json"
            manager = EngineSettingsManager(path)
            manager.save_profile("flux2", "A", Flux2Profile().to_dict())
            leftovers = [p for p in Path(tmp).iterdir() if p.name != "engines.json"]
            self.assertEqual(leftovers, [])

    def test_project_settings_manager_uses_settings_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp) / "proj"
            manager = project_settings_manager(working_dir)
            self.assertEqual(manager.path, working_dir / "settings" / "engines.json")


class TestConfigureEngineCLI(unittest.TestCase):
    def test_configure_and_list_round_trip(self):
        from cli.commands import configure_engine_command, list_engine_profiles_command

        with tempfile.TemporaryDirectory() as tmp:
            global_file = Path(tmp) / "engines.json"
            with mock.patch("project_system.settings.GLOBAL_ENGINES_FILE", global_file), \
                 mock.patch("cli.commands.global_settings_manager",
                            lambda: EngineSettingsManager(global_file)):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = configure_engine_command(
                        "flux2", "Test Profile",
                        ["executable=/opt/flux-cli", "gpu=0", "timeout_seconds=30"],
                        "global", None, True,
                    )
                self.assertEqual(rc, 0)

                manager = EngineSettingsManager(global_file)
                saved = manager.get_default_profile("flux2")
                self.assertEqual(saved["executable"], "/opt/flux-cli")
                self.assertEqual(saved["gpu"], "0")
                self.assertEqual(saved["timeout_seconds"], 30.0)  # coerced to float

                with contextlib.redirect_stdout(io.StringIO()) as out:
                    rc = list_engine_profiles_command("flux2", "global", None)
                self.assertEqual(rc, 0)
                self.assertIn("Test Profile", out.getvalue())

    def test_unknown_field_rejected(self):
        from cli.commands import configure_engine_command

        with tempfile.TemporaryDirectory() as tmp:
            global_file = Path(tmp) / "engines.json"
            with mock.patch("cli.commands.global_settings_manager",
                            lambda: EngineSettingsManager(global_file)):
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = configure_engine_command("flux2", "X", ["no_such_field=1"], "global", None, False)
                self.assertEqual(rc, 2)

    def test_project_scope_requires_project_path(self):
        from cli.commands import configure_engine_command

        with contextlib.redirect_stderr(io.StringIO()):
            rc = configure_engine_command("flux2", "X", [], "project", None, False)
        self.assertEqual(rc, 2)


class TestRegistryPicksUpSavedProfile(unittest.TestCase):
    def test_build_full_registry_uses_saved_profile(self):
        from cli.commands import build_full_registry

        with tempfile.TemporaryDirectory() as tmp:
            global_file = Path(tmp) / "engines.json"
            manager = EngineSettingsManager(global_file)
            manager.save_profile("flux2", "Configured", Flux2Profile(executable="/custom/flux").to_dict())

            with mock.patch("cli.commands.global_settings_manager", lambda: manager):
                registry = build_full_registry()
                engine = registry.create("flux2")
                self.assertEqual(engine.profile.executable, "/custom/flux")


if __name__ == "__main__":
    unittest.main()
