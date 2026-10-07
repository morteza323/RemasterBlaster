"""Phase 9/10 tests: Flux2Engine and RealESRGANEngine. No real Flux.2
or Real-ESRGAN binary is available in this environment, so:
  - validate() against the *default* profile is expected to correctly
    report "not ready" (the executable genuinely isn't installed here) --
    this proves the diagnostic path spec §49 describes actually works.
  - a small executable stub script stands in for "a real CLI tool" to
    prove command construction + subprocess execution + output
    validation work end-to-end through these adapters.
"""

from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path

from core.models.engine_models import ReconstructionRequest, UpscaleRequest
from engines.flux2.config import Flux2Profile
from engines.flux2.flux2_engine import Flux2Engine
from engines.realesrgan.config import RealESRGANProfile
from engines.realesrgan.realesrgan_engine import RealESRGANEngine

_STUB_CLI_SOURCE = '''#!/usr/bin/env python3
import argparse
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("--input", "-i")
parser.add_argument("--output", "-o")
args, _unknown = parser.parse_known_args()

Image.new("RGB", (4, 4), (5, 6, 7)).save(args.output)
'''


def _write_executable_stub(tmp_dir: Path) -> Path:
    script = tmp_dir / "fake_tool.py"
    script.write_text(_STUB_CLI_SOURCE, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


class TestFlux2Engine(unittest.TestCase):
    def test_default_profile_reports_not_ready_without_real_cli(self):
        engine = Flux2Engine()
        self.assertFalse(engine.validate().is_valid)

    def test_declared_capabilities(self):
        caps = Flux2Engine().get_capabilities()
        self.assertTrue(caps.supports_prompt)
        self.assertTrue(caps.supports_strength)
        self.assertTrue(caps.supports_seed)
        self.assertFalse(caps.supports_mask)
        self.assertFalse(caps.supports_upscale)

    def test_end_to_end_against_stub_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            script = _write_executable_stub(tmp_dir)
            engine = Flux2Engine(profile=Flux2Profile(executable=str(script)))
            self.assertTrue(engine.validate().is_valid)

            (tmp_dir / "in.png").write_bytes(b"x")
            request = ReconstructionRequest(
                job_id="j1", part_id="p1",
                input_path=str(tmp_dir / "in.png"), output_path=str(tmp_dir / "out.png"),
                prompt="restore metal", strength=0.6, seed=42,
            )
            result = engine.reconstruct(request)
            self.assertTrue(result.success, result.error.message if result.error else None)
            self.assertIn("--model", result.command)
            self.assertIn("Flux.2 Klein 4B", result.command)
            self.assertTrue(Path(request.output_path).exists())

    def test_model_path_included_when_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_path = str(Path(tmp) / "model.bin")
            engine = Flux2Engine(profile=Flux2Profile(executable=sys.executable, model_path=model_path))
            self.assertIn("--model-path", engine.config.fixed_arguments)
            self.assertIn(model_path, engine.config.fixed_arguments)


class TestRealESRGANEngine(unittest.TestCase):
    def test_default_profile_not_ready_without_real_binary(self):
        self.assertFalse(RealESRGANEngine().validate().is_valid)

    def test_capabilities_are_upscale_only(self):
        caps = RealESRGANEngine().get_capabilities()
        self.assertTrue(caps.supports_upscale)
        self.assertFalse(caps.supports_prompt)
        self.assertFalse(caps.supports_strength)
        self.assertFalse(caps.supports_seed)

    def test_reconstruct_fails_cleanly_rather_than_silently_noop(self):
        request = ReconstructionRequest(job_id="j", part_id="p", input_path="in.png", output_path="out.png")
        result = RealESRGANEngine().reconstruct(request)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_tile_size_and_model_path_included_when_set(self):
        engine = RealESRGANEngine(profile=RealESRGANProfile(
            executable=sys.executable, tile_size=256, model_path="/opt/esrgan/models",
        ))
        self.assertIn("-t", engine.config.fixed_arguments)
        self.assertIn("256", engine.config.fixed_arguments)
        self.assertIn("-m", engine.config.fixed_arguments)
        self.assertIn("/opt/esrgan/models", engine.config.fixed_arguments)

    def test_tile_size_omitted_by_default(self):
        engine = RealESRGANEngine(profile=RealESRGANProfile(executable=sys.executable))
        self.assertNotIn("-t", engine.config.fixed_arguments)
        self.assertNotIn("-m", engine.config.fixed_arguments)

    def test_end_to_end_upscale_against_stub_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            script = _write_executable_stub(tmp_dir)
            engine = RealESRGANEngine(profile=RealESRGANProfile(executable=str(script)))
            self.assertTrue(engine.validate().is_valid)

            (tmp_dir / "in.png").write_bytes(b"x")
            request = UpscaleRequest(
                job_id="j2", part_id="p2",
                input_path=str(tmp_dir / "in.png"), output_path=str(tmp_dir / "out.png"), scale=4.0,
            )
            result = engine.upscale(request)
            self.assertTrue(result.success, result.error.message if result.error else None)
            self.assertIn("-s", result.command)
            self.assertIn("4.0", result.command)
            self.assertTrue(Path(request.output_path).exists())


if __name__ == "__main__":
    unittest.main()
