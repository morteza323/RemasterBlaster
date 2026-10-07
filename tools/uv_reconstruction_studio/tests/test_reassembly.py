"""Phase 15 tests: reassembly validation, exact-coordinate compositing,
alpha handling, skip/use-original handling, and export."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.events.bus import EventBus
from core.events.types import EventType
from core.models.part import Bounds, Part, PartStatus, PartVersion
from image.color import AlphaMode
from reassembly.assembler import ReassemblyError, reassemble, validate_before_export
from reassembly.export import export_texture


def _solid(w, h, color=(255, 0, 0), mode="RGB"):
    return Image.new(mode, (w, h), color)


class TestValidation(unittest.TestCase):
    def test_missing_selected_version_flagged(self):
        source = _solid(100, 100)
        part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=50, height=50))
        result = validate_before_export(source, [part])
        self.assertFalse(result.ok)
        self.assertIn("no selected output", result.errors[0])

    def test_using_original_status_is_not_flagged(self):
        source = _solid(100, 100)
        part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=50, height=50),
                     status=PartStatus.USING_ORIGINAL)
        result = validate_before_export(source, [part])
        self.assertTrue(result.ok)

    def test_out_of_bounds_part_flagged(self):
        source = _solid(50, 50)
        part = Part(source_texture="a.png", bounds=Bounds(x=40, y=40, width=30, height=30))
        result = validate_before_export(source, [part])
        self.assertFalse(result.ok)
        self.assertIn("outside the source texture", result.errors[0])

    def test_missing_output_file_flagged(self):
        source = _solid(50, 50)
        part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
        part.add_version(PartVersion(engine="mock", output_path="/does/not/exist.png"))
        result = validate_before_export(source, [part])
        self.assertFalse(result.ok)
        self.assertIn("missing on disk", result.errors[0])


class TestReassemble(unittest.TestCase):
    def test_composites_at_exact_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = _solid(100, 100, (0, 0, 0))

            part = Part(source_texture="a.png", bounds=Bounds(x=20, y=30, width=10, height=10))
            patch_path = tmp_dir / "patch.png"
            _solid(10, 10, (255, 255, 255)).save(patch_path)
            part.add_version(PartVersion(engine="mock", output_path=str(patch_path)))

            result = reassemble(source, [part], default_feather=0)
            # center of the patch region must now be white; outside untouched
            self.assertEqual(result.getpixel((25, 35))[:3], (255, 255, 255))
            self.assertEqual(result.getpixel((0, 0))[:3], (0, 0, 0))
            self.assertEqual(result.size, (100, 100))

    def test_skipped_and_using_original_parts_leave_pixels_untouched(self):
        source = _solid(50, 50, (10, 20, 30))
        skipped = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10),
                        status=PartStatus.SKIPPED)
        using_orig = Part(source_texture="a.png", bounds=Bounds(x=10, y=0, width=10, height=10),
                           status=PartStatus.USING_ORIGINAL)
        result = reassemble(source, [skipped, using_orig])
        self.assertEqual(result.convert("RGB").getpixel((5, 5)), (10, 20, 30))
        self.assertEqual(result.convert("RGB").getpixel((15, 5)), (10, 20, 30))

    def test_wrong_size_output_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = _solid(50, 50)
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            wrong_size_path = tmp_dir / "wrong.png"
            _solid(20, 20).save(wrong_size_path)
            part.add_version(PartVersion(engine="mock", output_path=str(wrong_size_path)))
            with self.assertRaises(ReassemblyError):
                reassemble(source, [part])

    def test_validation_failure_raises_before_compositing(self):
        source = _solid(50, 50)
        part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
        with self.assertRaises(ReassemblyError):
            reassemble(source, [part])

    def test_alpha_ignore_produces_rgb_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = _solid(30, 30, (0, 0, 0, 255), mode="RGBA")
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            patch_path = tmp_dir / "p.png"
            _solid(10, 10, (1, 2, 3, 128), mode="RGBA").save(patch_path)
            part.add_version(PartVersion(engine="mock", output_path=str(patch_path)))
            result = reassemble(source, [part], alpha_mode=AlphaMode.IGNORE)
            self.assertEqual(result.mode, "RGB")

    def test_events_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source = _solid(20, 20)
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            patch_path = tmp_dir / "p.png"
            _solid(10, 10).save(patch_path)
            part.add_version(PartVersion(engine="mock", output_path=str(patch_path)))

            bus = EventBus()
            events = []
            bus.subscribe_all(events.append)
            reassemble(source, [part], event_bus=bus)
            types = [e.type for e in events]
            self.assertIn(EventType.REASSEMBLY_STARTED, types)
            self.assertIn(EventType.REASSEMBLY_PROGRESS, types)
            self.assertIn(EventType.REASSEMBLY_COMPLETED, types)


class TestExport(unittest.TestCase):
    def test_export_writes_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "sub" / "final.png"
            img = _solid(20, 20)
            result_path = export_texture(img, out_path)
            self.assertTrue(result_path.exists())
            with Image.open(result_path) as reopened:
                self.assertEqual(reopened.size, (20, 20))
                self.assertEqual(reopened.format, "PNG")

    def test_export_ignore_alpha_forces_rgb(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "final.png"
            img = _solid(10, 10, (1, 2, 3, 4), mode="RGBA")
            export_texture(img, out_path, alpha_mode=AlphaMode.IGNORE)
            with Image.open(out_path) as reopened:
                self.assertEqual(reopened.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
