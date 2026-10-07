"""Phase 5 tests: region calculation from guides and full part
generation (folders, thumbnails, metadata, project.parts population)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.models.guide import Guide, GuideOrientation
from core.models.project import Project, SourceTexture
from uv.parts_generator import PartGenerationError, generate_parts
from uv.regions import calculate_regions, validate_guides


class TestRegionCalculation(unittest.TestCase):
    def test_no_guides_yields_single_region(self):
        regions = calculate_regions(100, 100, [])
        self.assertEqual(len(regions), 1)
        self.assertEqual((regions[0].x, regions[0].y, regions[0].width, regions[0].height), (0, 0, 100, 100))

    def test_one_horizontal_one_vertical_yields_2x2_grid(self):
        guides = [
            Guide(orientation=GuideOrientation.HORIZONTAL, position=50),
            Guide(orientation=GuideOrientation.VERTICAL, position=50),
        ]
        regions = calculate_regions(100, 100, guides)
        self.assertEqual(len(regions), 4)
        sizes = {(r.width, r.height) for r in regions}
        self.assertEqual(sizes, {(50, 50)})

    def test_uneven_grid(self):
        guides = [Guide(orientation=GuideOrientation.VERTICAL, position=30)]
        regions = calculate_regions(100, 50, guides)
        self.assertEqual(len(regions), 2)
        widths = sorted(r.width for r in regions)
        self.assertEqual(widths, [30, 70])

    def test_duplicate_and_edge_guides_ignored(self):
        guides = [
            Guide(orientation=GuideOrientation.VERTICAL, position=0),    # on edge -> no-op
            Guide(orientation=GuideOrientation.VERTICAL, position=100),  # on edge -> no-op
            Guide(orientation=GuideOrientation.VERTICAL, position=40),
            Guide(orientation=GuideOrientation.VERTICAL, position=40),   # duplicate
        ]
        regions = calculate_regions(100, 100, guides)
        self.assertEqual(len(regions), 2)

    def test_validate_guides_flags_out_of_bounds(self):
        guides = [Guide(orientation=GuideOrientation.VERTICAL, position=500)]
        result = validate_guides(guides, image_width=100, image_height=100)
        self.assertFalse(result.ok)
        self.assertIn("500", result.errors[0])

    def test_validate_guides_ok_within_bounds(self):
        guides = [Guide(orientation=GuideOrientation.HORIZONTAL, position=50)]
        result = validate_guides(guides, image_width=100, image_height=100)
        self.assertTrue(result.ok)


class TestPartGeneration(unittest.TestCase):
    def _project_and_image(self):
        project = Project(name="Grid Test", source=SourceTexture(filename="tex.png", width=64, height=64))
        project.add_guide(Guide(orientation=GuideOrientation.HORIZONTAL, position=32))
        project.add_guide(Guide(orientation=GuideOrientation.VERTICAL, position=32))
        image = Image.new("RGBA", (64, 64), (10, 20, 30, 255))
        return project, image

    def test_generates_four_parts_with_folders(self):
        project, image = self._project_and_image()
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            new_parts = generate_parts(project, image, working_dir, default_padding=8)

            self.assertEqual(len(new_parts), 4)
            self.assertEqual(len(project.parts), 4)

            for part in new_parts:
                part_dir = working_dir / "parts" / part.id
                self.assertTrue((part_dir / "source" / "crop.png").exists())
                self.assertTrue((part_dir / "source" / "context.png").exists())  # padding > 0
                self.assertTrue((part_dir / "previews" / "thumbnail.png").exists())
                self.assertTrue((part_dir / "metadata.json").exists())
                for sub in ("output", "logs"):
                    self.assertTrue((part_dir / sub).is_dir())

                with Image.open(part_dir / "source" / "crop.png") as crop:
                    self.assertEqual(crop.size, (part.bounds.width, part.bounds.height))

                meta = json.loads((part_dir / "metadata.json").read_text())
                self.assertEqual(meta["id"], part.id)

    def test_no_padding_skips_context_file(self):
        project, image = self._project_and_image()
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            new_parts = generate_parts(project, image, working_dir, default_padding=0)
            for part in new_parts:
                self.assertFalse((working_dir / "parts" / part.id / "source" / "context.png").exists())

    def test_regenerating_appends_not_overwrites(self):
        project, image = self._project_and_image()
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            first = generate_parts(project, image, working_dir)
            second = generate_parts(project, image, working_dir)
            self.assertEqual(len(first), 4)
            self.assertEqual(len(second), 4)
            self.assertEqual(len(project.parts), 8)  # both generations kept
            # every part folder from the first generation is still there
            for part in first:
                self.assertTrue((working_dir / "parts" / part.id / "metadata.json").exists())

    def test_invalid_guides_raise(self):
        project, image = self._project_and_image()
        project.guides[0] = Guide(orientation=GuideOrientation.HORIZONTAL, position=9999)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PartGenerationError):
                generate_parts(project, image, Path(tmp))


if __name__ == "__main__":
    unittest.main()
