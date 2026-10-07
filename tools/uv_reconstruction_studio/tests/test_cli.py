"""Phase 17 tests: the full project-oriented CLI workflow, called
directly as functions (faster and more precise than subprocess, and
exercises the exact same code main.py dispatches to)."""

from __future__ import annotations

import io
import contextlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cli.commands import (
    create_project_command,
    open_project_command,
    process_project_command,
    reassemble_command,
    reconstruct_part_command,
)
from project_system.manager import ProjectManager


def _make_source(path: Path, size=(64, 64)):
    Image.new("RGBA", size, (20, 40, 60, 255)).save(path)


class TestProjectCLIWorkflow(unittest.TestCase):
    def test_full_workflow_create_process_reassemble(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = tmp_dir / "source.png"
            _make_source(source)
            project_dir = tmp_dir / "proj"

            with contextlib.redirect_stdout(io.StringIO()):
                rc = create_project_command(
                    str(project_dir), str(source), "Test Project",
                    ["horizontal:32", "vertical:32"], generate=True, padding=8,
                )
            self.assertEqual(rc, 0)

            manager = ProjectManager()
            project = manager.load(project_dir, project_dir)
            self.assertEqual(len(project.parts), 4)

            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = open_project_command(str(project_dir))
            self.assertEqual(rc, 0)
            self.assertIn("Test Project", out.getvalue())

            with contextlib.redirect_stdout(io.StringIO()):
                rc = process_project_command(str(project_dir), "mock", json_mode=False, quiet=True)
            self.assertEqual(rc, 0)

            reloaded = ProjectManager().load(project_dir, project_dir)
            self.assertTrue(all(p.status.value == "completed" for p in reloaded.parts))

            output_path = tmp_dir / "final.png"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = reassemble_command(str(project_dir), str(output_path))
            self.assertEqual(rc, 0)
            self.assertTrue(output_path.exists())
            with Image.open(output_path) as final:
                self.assertEqual(final.size, (64, 64))

    def test_reconstruct_single_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = tmp_dir / "source.png"
            _make_source(source, size=(32, 32))
            project_dir = tmp_dir / "proj"

            with contextlib.redirect_stdout(io.StringIO()):
                create_project_command(str(project_dir), str(source), "Single Part Test", [], generate=True, padding=0)

            project = ProjectManager().load(project_dir, project_dir)
            self.assertEqual(len(project.parts), 1)
            part_id = project.parts[0].id

            with contextlib.redirect_stdout(io.StringIO()):
                rc = reconstruct_part_command(str(project_dir), part_id, "mock", json_mode=False, quiet=True)
            self.assertEqual(rc, 0)

            reloaded = ProjectManager().load(project_dir, project_dir)
            part = reloaded.get_part(part_id)
            self.assertEqual(part.status.value, "completed")
            self.assertEqual(len(part.history), 1)

    def test_open_nonexistent_project_fails_cleanly(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = open_project_command("/does/not/exist")
        self.assertEqual(rc, 2)

    def test_reassemble_without_processing_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = tmp_dir / "source.png"
            _make_source(source)
            project_dir = tmp_dir / "proj"
            with contextlib.redirect_stdout(io.StringIO()):
                create_project_command(str(project_dir), str(source), "No Process", [], generate=True, padding=0)
            with contextlib.redirect_stderr(io.StringIO()):
                rc = reassemble_command(str(project_dir), str(tmp_dir / "out.png"))
            self.assertEqual(rc, 2)  # parts have no selected version yet


class TestUseOriginalAndUseVersion(unittest.TestCase):
    def test_use_original_then_use_version_round_trip(self):
        from cli.commands import process_project_command, use_original_command, use_version_command

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = tmp_dir / "source.png"
            _make_source(source, size=(32, 32))
            project_dir = tmp_dir / "proj"

            with contextlib.redirect_stdout(io.StringIO()):
                create_project_command(str(project_dir), str(source), "UseOriginal Test", [], generate=True, padding=0)
                process_project_command(str(project_dir), "mock", json_mode=False, quiet=True)

            project = ProjectManager().load(project_dir, project_dir)
            part = project.parts[0]
            self.assertEqual(part.status.value, "completed")
            version_id = part.history[0].id

            with contextlib.redirect_stdout(io.StringIO()):
                rc = use_original_command(str(project_dir), part.id)
            self.assertEqual(rc, 0)
            reloaded = ProjectManager().load(project_dir, project_dir)
            self.assertEqual(reloaded.get_part(part.id).status.value, "using_original")

            # Reassembly must succeed and skip compositing for this part.
            with contextlib.redirect_stdout(io.StringIO()):
                rc = reassemble_command(str(project_dir), str(tmp_dir / "final.png"))
            self.assertEqual(rc, 0)

            # Switching back to the AI version must clear the "using original" status.
            with contextlib.redirect_stdout(io.StringIO()):
                rc = use_version_command(str(project_dir), part.id, version_id)
            self.assertEqual(rc, 0)
            reloaded2 = ProjectManager().load(project_dir, project_dir)
            reloaded_part = reloaded2.get_part(part.id)
            self.assertEqual(reloaded_part.status.value, "completed")
            self.assertEqual(reloaded_part.selected_version_id, version_id)

    def test_use_version_rejects_unknown_version_id(self):
        from cli.commands import process_project_command, use_version_command

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = tmp_dir / "source.png"
            _make_source(source, size=(32, 32))
            project_dir = tmp_dir / "proj"
            with contextlib.redirect_stdout(io.StringIO()):
                create_project_command(str(project_dir), str(source), "Bad Version", [], generate=True, padding=0)
                process_project_command(str(project_dir), "mock", json_mode=False, quiet=True)
            project = ProjectManager().load(project_dir, project_dir)
            part = project.parts[0]

            with contextlib.redirect_stderr(io.StringIO()):
                rc = use_version_command(str(project_dir), part.id, "not_a_real_version_id")
            self.assertEqual(rc, 2)

    def test_use_original_unknown_part_fails_cleanly(self):
        from cli.commands import use_original_command

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = tmp_dir / "source.png"
            _make_source(source)
            project_dir = tmp_dir / "proj"
            with contextlib.redirect_stdout(io.StringIO()):
                create_project_command(str(project_dir), str(source), "X", [], generate=True, padding=0)
            with contextlib.redirect_stderr(io.StringIO()):
                rc = use_original_command(str(project_dir), "no_such_part")
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
