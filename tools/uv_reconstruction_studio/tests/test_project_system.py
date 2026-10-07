"""Phase 2 tests: directory storage, zip (.uvrs) storage, atomic
writes surviving a mid-write failure, autosave, and crash recovery."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core.models.guide import Guide, GuideOrientation
from core.models.part import Bounds, Part
from core.models.project import Project, SourceTexture
from project_system.manager import ProjectManager
from project_system.storage import (
    DirectoryProjectStorage,
    ZipProjectStorage,
    atomic_write_text,
)


def _sample_project() -> Project:
    project = Project(name="Round Trip", source=SourceTexture(filename="tex.png", width=200, height=200))
    project.add_guide(Guide(orientation=GuideOrientation.HORIZONTAL, position=100))
    project.add_part(Part(source_texture="tex.png", bounds=Bounds(x=0, y=0, width=100, height=100),
                           prompt="restore leather grain"))
    return project


class TestAtomicWrite(unittest.TestCase):
    def test_writes_and_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "file.json"
            atomic_write_text(path, "hello")
            self.assertEqual(path.read_text(), "hello")
            atomic_write_text(path, "world")
            self.assertEqual(path.read_text(), "world")

    def test_failure_leaves_original_untouched_and_no_tmp_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.json"
            atomic_write_text(path, "original")

            with mock.patch("os.replace", side_effect=OSError("simulated crash")):
                with self.assertRaises(OSError):
                    atomic_write_text(path, "corrupted")

            self.assertEqual(path.read_text(), "original")
            leftovers = [p for p in Path(tmp).iterdir() if p.name != "file.json"]
            self.assertEqual(leftovers, [])


class TestDirectoryStorage(unittest.TestCase):
    def test_round_trip(self):
        project = _sample_project()
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp) / "MyProject"
            storage = DirectoryProjectStorage()
            storage.save(project, working_dir, working_dir)

            self.assertTrue((working_dir / "project.json").exists())
            for sub in ("texture", "parts", "logs", "presets", "history", "settings"):
                self.assertTrue((working_dir / sub).is_dir())

            loaded = storage.load(working_dir, working_dir)
            self.assertEqual(loaded.id, project.id)
            self.assertEqual(len(loaded.parts), 1)
            self.assertEqual(loaded.parts[0].prompt, "restore leather grain")
            self.assertEqual(loaded.guides[0].position, 100)


class TestZipStorage(unittest.TestCase):
    def test_round_trip_through_uvrs_file(self):
        project = _sample_project()
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            uvrs_path = Path(tmp) / "MyProject.uvrs"
            storage = ZipProjectStorage()
            storage.save(project, staging, uvrs_path)

            self.assertTrue(uvrs_path.exists())

            extract_dir = Path(tmp) / "extracted"
            loaded = storage.load(uvrs_path, extract_dir)
            self.assertEqual(loaded.id, project.id)
            self.assertEqual(len(loaded.parts), 1)
            self.assertEqual(loaded.parts[0].bounds.width, 100)
            self.assertTrue((extract_dir / "parts").is_dir())

    def test_rejects_non_zip_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "not_a_project.uvrs"
            bad_path.write_text("not a zip")
            storage = ZipProjectStorage()
            with self.assertRaises(ValueError):
                storage.load(bad_path, Path(tmp) / "out")


class TestProjectManager(unittest.TestCase):
    def test_create_save_load_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProjectManager()
            project = _sample_project()
            working_dir = Path(tmp) / "proj"
            manager.create(project, working_dir)
            manager.save_as(working_dir)

            manager2 = ProjectManager()
            loaded = manager2.load(working_dir, working_dir)
            self.assertEqual(loaded.id, project.id)

    def test_resave_uses_remembered_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProjectManager()
            project = _sample_project()
            working_dir = Path(tmp) / "proj"
            manager.create(project, working_dir)
            manager.save_as(working_dir)

            manager.project.name = "Renamed"
            manager.save()  # no path given -- must reuse save_path/storage

            reloaded = DirectoryProjectStorage().load(working_dir, working_dir)
            self.assertEqual(reloaded.name, "Renamed")

    def test_save_without_save_as_raises(self):
        manager = ProjectManager()
        manager.project = _sample_project()
        manager.working_dir = Path(tempfile.mkdtemp())
        with self.assertRaises(RuntimeError):
            manager.save()


class TestAutosaveAndRecovery(unittest.TestCase):
    def test_autosave_writes_without_touching_main_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProjectManager()
            project = _sample_project()
            working_dir = Path(tmp) / "proj"
            manager.create(project, working_dir)
            manager.save_as(working_dir)
            main_mtime_before = (working_dir / "project.json").stat().st_mtime

            manager.project.name = "Changed after save"
            manager.autosave()

            self.assertTrue(manager.autosave_path().exists())
            # Main save file must be untouched by autosave.
            self.assertEqual((working_dir / "project.json").stat().st_mtime, main_mtime_before)
            main_data = json.loads((working_dir / "project.json").read_text())
            self.assertNotEqual(main_data["project"]["name"], "Changed after save")

    def test_start_stop_autosave_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProjectManager()
            project = _sample_project()
            working_dir = Path(tmp) / "proj"
            manager.create(project, working_dir)
            manager.save_as(working_dir)

            manager.start_autosave(interval_seconds=0.05)
            time.sleep(0.2)
            manager.stop_autosave()

            self.assertTrue(manager.autosave_path().exists())

    def test_disabled_autosave_starts_no_thread(self):
        manager = ProjectManager()
        manager.project = _sample_project()
        manager.working_dir = Path(tempfile.mkdtemp())
        manager.start_autosave(interval_seconds=0)
        self.assertIsNone(manager._autosave_thread)

    def test_recovery_detected_when_autosave_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProjectManager()
            project = _sample_project()
            working_dir = Path(tmp) / "proj"
            manager.create(project, working_dir)
            manager.save_as(working_dir)

            time.sleep(0.01)
            manager.project.name = "Unsaved Change"
            manager.autosave()

            info = ProjectManager.check_for_recovery(working_dir)
            self.assertIsNotNone(info)
            self.assertEqual(info.project_name, "Unsaved Change")

            recovered = manager.recover(info)
            self.assertEqual(recovered.name, "Unsaved Change")

            manager.discard_recovery(info)
            self.assertFalse(info.autosave_path.exists())

    def test_no_recovery_when_autosave_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProjectManager()
            project = _sample_project()
            working_dir = Path(tmp) / "proj"
            manager.create(project, working_dir)
            manager.autosave()
            time.sleep(0.01)
            manager.save_as(working_dir)  # real save happens AFTER autosave

            info = ProjectManager.check_for_recovery(working_dir)
            self.assertIsNone(info)

    def test_no_recovery_when_no_autosave_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = ProjectManager.check_for_recovery(Path(tmp))
            self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
