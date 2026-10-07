"""Phase 3 tests: crop/padding, resize, mask ops, seam blending,
difference maps, alpha handling, validation, thumbnails, hashing.
Uses small synthetic in-memory images -- no fixture files needed."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from core.models.part import Bounds
from image import color, difference, mask as maskmod, validation
from image.blend import feathered_composite
from image.crop import clamp_bounds_to_image, core_region_offset, crop_region, crop_with_padding
from image.loader import get_image_info, load_image
from image.resize import ResizeMethod, resize_image, scale_image
from image.thumbnail import generate_thumbnail
from utils.hashing import hash_bytes, hash_file, hash_image_pixels


def _solid(width, height, color_=(255, 0, 0), mode="RGB"):
    return Image.new(mode, (width, height), color_)


class TestCrop(unittest.TestCase):
    def test_crop_region_exact(self):
        img = _solid(100, 100)
        cropped = crop_region(img, Bounds(x=10, y=20, width=30, height=40))
        self.assertEqual(cropped.size, (30, 40))

    def test_padding_matches_spec_example(self):
        # spec §19: bounds 512,256,512,256 + padding 64 -> 448,192,640,384
        b = Bounds(x=512, y=256, width=512, height=256)
        padded = b.padded(64)
        self.assertEqual((padded.x, padded.y, padded.width, padded.height), (448, 192, 640, 384))

    def test_clamp_near_edge(self):
        clamped = clamp_bounds_to_image(Bounds(x=90, y=90, width=50, height=50), 100, 100)
        self.assertEqual((clamped.x, clamped.y, clamped.width, clamped.height), (90, 90, 10, 10))

    def test_crop_with_padding_and_core_offset_roundtrip(self):
        img = _solid(200, 200)
        original = Bounds(x=90, y=90, width=20, height=20)
        patch, padded_clamped = crop_with_padding(img, original, padding=30)
        self.assertEqual(patch.size, (padded_clamped.width, padded_clamped.height))

        offset = core_region_offset(original, padded_clamped)
        core_crop = patch.crop((offset.x, offset.y, offset.x + offset.width, offset.y + offset.height))
        self.assertEqual(core_crop.size, (20, 20))

    def test_core_offset_rejects_mismatched_bounds(self):
        with self.assertRaises(ValueError):
            core_region_offset(Bounds(x=0, y=0, width=10, height=10), Bounds(x=5, y=5, width=10, height=10))


class TestResize(unittest.TestCase):
    def test_resize_dimensions(self):
        img = _solid(64, 64)
        out = resize_image(img, 128, 32, ResizeMethod.NEAREST)
        self.assertEqual(out.size, (128, 32))

    def test_scale(self):
        img = _solid(100, 50)
        out = scale_image(img, 2.0)
        self.assertEqual(out.size, (200, 100))

    def test_rejects_nonpositive(self):
        img = _solid(10, 10)
        with self.assertRaises(ValueError):
            resize_image(img, 0, 10)


class TestMask(unittest.TestCase):
    def test_blank_mask(self):
        m = maskmod.create_blank_mask(10, 10, fill=255)
        self.assertEqual(m.mode, "L")
        self.assertEqual(np.array(m).max(), 255)

    def test_invert(self):
        m = maskmod.create_blank_mask(4, 4, fill=0)
        inv = maskmod.invert_mask(m)
        self.assertEqual(np.array(inv).min(), 255)

    def test_expand_grows_white_region(self):
        m = Image.new("L", (20, 20), 0)
        for y in range(9, 11):
            for x in range(9, 11):
                m.putpixel((x, y), 255)
        expanded = maskmod.expand_mask(m, 3)
        self.assertGreater(np.count_nonzero(np.array(expanded)), np.count_nonzero(np.array(m)))

    def test_contract_shrinks_white_region(self):
        m = Image.new("L", (20, 20), 255)
        contracted = maskmod.contract_mask(m, 3)
        self.assertLessEqual(np.count_nonzero(np.array(contracted) > 0), np.count_nonzero(np.array(m) > 0))

    def test_apply_mask_selects_overlay_where_white(self):
        base = _solid(10, 10, (0, 0, 0))
        overlay = _solid(10, 10, (255, 255, 255))
        m = Image.new("L", (10, 10), 0)
        for x in range(5):
            for y in range(10):
                m.putpixel((x, y), 255)
        out = maskmod.apply_mask(base, overlay, m)
        self.assertEqual(out.getpixel((0, 0))[:3], (255, 255, 255))
        self.assertEqual(out.getpixel((9, 0))[:3], (0, 0, 0))


class TestBlend(unittest.TestCase):
    def test_feathered_composite_replaces_region(self):
        base = _solid(100, 100, (0, 0, 0))
        patch = _solid(20, 20, (255, 255, 255))
        region = Bounds(x=40, y=40, width=20, height=20)
        out = feathered_composite(base, patch, region, feather_radius=0)
        self.assertEqual(out.getpixel((50, 50))[:3], (255, 255, 255))
        self.assertEqual(out.getpixel((0, 0))[:3], (0, 0, 0))

    def test_feather_softens_edges(self):
        base = _solid(100, 100, (0, 0, 0))
        patch = _solid(20, 20, (255, 255, 255))
        region = Bounds(x=40, y=40, width=20, height=20)
        out = feathered_composite(base, patch, region, feather_radius=6)
        edge_pixel = out.getpixel((40, 50))[:3]
        # With feathering the very edge shouldn't be pure 255 or pure 0.
        self.assertTrue(0 < edge_pixel[0] < 255)

    def test_rejects_wrong_patch_size(self):
        base = _solid(50, 50)
        patch = _solid(10, 10)
        with self.assertRaises(ValueError):
            feathered_composite(base, patch, Bounds(x=0, y=0, width=20, height=20))

    def test_rejects_out_of_bounds_region(self):
        base = _solid(50, 50)
        patch = _solid(20, 20)
        with self.assertRaises(ValueError):
            feathered_composite(base, patch, Bounds(x=40, y=40, width=20, height=20))


class TestDifference(unittest.TestCase):
    def test_absolute_identical_is_black(self):
        a = _solid(30, 30, (100, 100, 100))
        b = _solid(30, 30, (100, 100, 100))
        diff = difference.compute_difference(a, b, difference.DifferenceMode.ABSOLUTE)
        self.assertEqual(np.array(diff).max(), 0)

    def test_absolute_different_is_nonzero(self):
        a = _solid(30, 30, (0, 0, 0))
        b = _solid(30, 30, (255, 255, 255))
        diff = difference.compute_difference(a, b, difference.DifferenceMode.ABSOLUTE)
        self.assertGreater(np.array(diff).max(), 0)

    def test_structural_and_edge_modes_run(self):
        a = _solid(40, 40, (10, 10, 10))
        b = _solid(40, 40, (200, 200, 200))
        for mode in (difference.DifferenceMode.STRUCTURAL, difference.DifferenceMode.EDGE):
            out = difference.compute_difference(a, b, mode)
            self.assertEqual(out.size, (40, 40))


class TestColorAlpha(unittest.TestCase):
    def test_inspect_color_rgba(self):
        img = Image.new("RGBA", (10, 10), (1, 2, 3, 128))
        info = color.inspect_color(img)
        self.assertEqual(info.mode, "RGBA")
        self.assertTrue(info.has_alpha)
        self.assertEqual(info.bit_depth, 8)

    def test_preserve_original_alpha(self):
        original = Image.new("RGBA", (10, 10), (0, 0, 0, 77))
        reconstructed = Image.new("RGB", (10, 10), (200, 200, 200))
        out = color.apply_alpha_mode(original, reconstructed, color.AlphaMode.PRESERVE_ORIGINAL)
        self.assertEqual(out.mode, "RGBA")
        self.assertEqual(out.getpixel((0, 0))[3], 77)

    def test_ignore_drops_alpha(self):
        original = Image.new("RGBA", (10, 10), (0, 0, 0, 10))
        reconstructed = Image.new("RGBA", (10, 10), (1, 1, 1, 10))
        out = color.apply_alpha_mode(original, reconstructed, color.AlphaMode.IGNORE)
        self.assertEqual(out.mode, "RGB")

    def test_composite_multiplies_alpha(self):
        original = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
        reconstructed = Image.new("RGBA", (4, 4), (9, 9, 9, 128))
        out = color.apply_alpha_mode(original, reconstructed, color.AlphaMode.COMPOSITE)
        # 255/255 * 128/255 * 255 ~= 128
        self.assertAlmostEqual(out.getpixel((0, 0))[3], 128, delta=2)

    def test_no_silent_premultiply_of_rgb(self):
        # RGB channel values must be untouched by alpha mode changes --
        # only the alpha channel should ever change here.
        reconstructed = Image.new("RGBA", (4, 4), (50, 60, 70, 10))
        original = Image.new("RGBA", (4, 4), (0, 0, 0, 250))
        out = color.apply_alpha_mode(original, reconstructed, color.AlphaMode.PRESERVE_ORIGINAL)
        self.assertEqual(out.getpixel((0, 0))[:3], (50, 60, 70))


class TestValidation(unittest.TestCase):
    def test_input_validation_missing_file(self):
        result = validation.validate_input_image("/does/not/exist.png")
        self.assertFalse(result.ok)

    def test_input_validation_valid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ok.png"
            _solid(10, 10).save(path)
            result = validation.validate_input_image(path)
            self.assertTrue(result.ok, result.errors)

    def test_output_validation_zero_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.png"
            path.write_bytes(b"")
            result = validation.validate_output_image(path)
            self.assertFalse(result.ok)

    def test_output_validation_dimension_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.png"
            _solid(10, 10).save(path)
            result = validation.validate_output_image(path, expected_width=20, expected_height=20)
            self.assertFalse(result.ok)
            self.assertTrue(any("Width" in e for e in result.errors))


class TestLoaderAndThumbnail(unittest.TestCase):
    def test_load_and_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            _solid(64, 32, mode="RGBA").save(path)
            img = load_image(path)
            self.assertEqual(img.size, (64, 32))
            info = get_image_info(path)
            self.assertEqual((info.width, info.height), (64, 32))
            self.assertTrue(info.has_alpha)

    def test_thumbnail_bounded(self):
        img = _solid(1000, 500)
        thumb = generate_thumbnail(img, max_size=100)
        self.assertLessEqual(max(thumb.size), 100)


class TestHashing(unittest.TestCase):
    def test_hash_file_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.bin"
            path.write_bytes(b"hello world")
            self.assertEqual(hash_file(path), hash_file(path))

    def test_hash_image_pixels_changes_with_content(self):
        h1 = hash_image_pixels(_solid(10, 10, (1, 1, 1)))
        h2 = hash_image_pixels(_solid(10, 10, (2, 2, 2)))
        self.assertNotEqual(h1, h2)

    def test_hash_bytes(self):
        self.assertEqual(hash_bytes(b"abc"), hash_bytes(b"abc"))
        self.assertNotEqual(hash_bytes(b"abc"), hash_bytes(b"abd"))


if __name__ == "__main__":
    unittest.main()
