"""Phase 8 tests: command builder, real subprocess management (via a
tiny fake CLI script -- no real Flux.2/Real-ESRGAN needed), progress
parsers, and the full CustomCLIEngine integration including raw log
capture and cooperative cancellation."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.events.bus import EventBus
from core.events.types import EventType
from core.models.engine_models import EngineCapabilities, ErrorCategory, ReconstructionRequest
from engines.custom_cli.command_builder import ConfigurationError, build_command, values_for_reconstruction
from engines.custom_cli.config import CLIEngineConfig
from engines.custom_cli.engine import CustomCLIEngine
from engines.custom_cli.process_runner import run_process
from pipeline.progress import GenericProgressParser, RealESRGANProgressParser

_FAKE_CLI_SOURCE = '''
import argparse, sys, time
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("--input")
parser.add_argument("--output")
parser.add_argument("--prompt", default="")
parser.add_argument("--steps", type=int, default=3)
parser.add_argument("--fail", action="store_true")
parser.add_argument("--hang", action="store_true")
args = parser.parse_args()

if args.hang:
    while True:
        time.sleep(0.05)

for i in range(1, args.steps + 1):
    print(f"Step {i}/{args.steps}")
    sys.stdout.flush()
    time.sleep(0.01)

if args.fail:
    print("simulated failure", file=sys.stderr)
    sys.exit(1)

Image.new("RGB", (4, 4), (1, 2, 3)).save(args.output)
'''


def _write_fake_cli(tmp_dir: Path) -> Path:
    script = tmp_dir / "fake_cli.py"
    script.write_text(_FAKE_CLI_SOURCE, encoding="utf-8")
    return script


class TestCommandBuilder(unittest.TestCase):
    def test_required_and_optional_groups(self):
        config = CLIEngineConfig(name="t", executable="tool", strength_argument=["--strength", "{strength}"])
        request = ReconstructionRequest(job_id="j", part_id="p", input_path="in.png", output_path="out.png",
                                         prompt="hi", strength=0.5)
        values = values_for_reconstruction(request, model_path=None)
        command = build_command(config, values)
        self.assertIn("--input", command)
        self.assertIn("in.png", command)
        self.assertIn("--strength", command)
        self.assertIn("0.5", command)
        # seed wasn't given -> its (default) group must be entirely absent
        self.assertNotIn("--seed", command)

    def test_missing_required_raises(self):
        config = CLIEngineConfig(name="t", executable="tool")
        with self.assertRaises(ConfigurationError):
            build_command(config, {"input": None, "output": "out.png"})

    def test_literal_fixed_arguments_pass_through(self):
        config = CLIEngineConfig(name="t", executable="tool", fixed_arguments=["/path/to/script.py"])
        command = build_command(config, {"input": "in.png", "output": "out.png"})
        self.assertEqual(command[:2], ["tool", "/path/to/script.py"])


class TestProcessRunner(unittest.TestCase):
    def test_captures_stdout_and_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = _write_fake_cli(Path(tmp))
            output = Path(tmp) / "out.png"
            lines = []
            result = run_process(
                [sys.executable, str(script), "--output", str(output), "--steps", "2"],
                on_stdout_line=lines.append,
            )
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(output.exists())
            self.assertIn("Step 1/2", lines)
            self.assertIn("Step 2/2", lines)

    def test_nonzero_exit_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = _write_fake_cli(Path(tmp))
            output = Path(tmp) / "out.png"
            result = run_process([sys.executable, str(script), "--output", str(output), "--fail"])
            self.assertEqual(result.exit_code, 1)
            self.assertIn("simulated failure", result.stderr)

    def test_cancellation_terminates_hung_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = _write_fake_cli(Path(tmp))
            output = Path(tmp) / "out.png"
            cancel_event = threading.Event()

            def _cancel_soon():
                time.sleep(0.2)
                cancel_event.set()

            threading.Thread(target=_cancel_soon).start()
            start = time.time()
            result = run_process(
                [sys.executable, str(script), "--output", str(output), "--hang"],
                cancel_event=cancel_event, termination_grace_seconds=2,
            )
            elapsed = time.time() - start
            self.assertTrue(result.cancelled)
            self.assertLess(elapsed, 5, "cancellation must not wait for the full grace period on a killable process")

    def test_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = _write_fake_cli(Path(tmp))
            output = Path(tmp) / "out.png"
            result = run_process(
                [sys.executable, str(script), "--output", str(output), "--hang"],
                timeout_seconds=0.2, termination_grace_seconds=2,
            )
            self.assertTrue(result.timed_out)


class TestProgressParsers(unittest.TestCase):
    def test_generic_step_pattern(self):
        parser = GenericProgressParser()
        update = parser.parse_line("Step 5/20")
        self.assertEqual((update.step, update.total_steps), (5, 20))
        self.assertAlmostEqual(update.progress, 0.25)

    def test_generic_no_match_returns_none(self):
        parser = GenericProgressParser()
        self.assertIsNone(parser.parse_line("Loading model..."))

    def test_realesrgan_percentage(self):
        parser = RealESRGANProgressParser()
        update = parser.parse_line("Tile 3/10  42.50%")
        self.assertAlmostEqual(update.progress, 0.425)


class TestCustomCLIEngine(unittest.TestCase):
    def test_validate_missing_executable(self):
        engine = CustomCLIEngine(CLIEngineConfig(name="t", executable="/no/such/tool-xyz"))
        result = engine.validate()
        self.assertFalse(result.is_valid)

    def test_validate_existing_executable(self):
        engine = CustomCLIEngine(CLIEngineConfig(name="t", executable=sys.executable))
        result = engine.validate()
        self.assertTrue(result.is_valid)

    def test_config_aligned_with_declared_capabilities(self):
        # Regression test: an engine that declares supports_mask=False
        # must NEVER include --mask in the built command, even if a
        # request happens to carry a mask_path -- capabilities and the
        # actual command builder must not be able to disagree (spec §20).
        config = CLIEngineConfig(name="t", executable=sys.executable)  # default templates include --mask
        engine = CustomCLIEngine(config, capabilities=EngineCapabilities(supports_mask=False))
        self.assertIsNone(engine.config.mask_argument)

    def test_config_keeps_argument_when_capability_is_true(self):
        config = CLIEngineConfig(name="t", executable=sys.executable)
        engine = CustomCLIEngine(config, capabilities=EngineCapabilities(supports_mask=True))
        self.assertIsNotNone(engine.config.mask_argument)

    def test_missing_executable_returns_failed_result_not_a_crash(self):
        # Regression test: subprocess.Popen raises FileNotFoundError
        # directly (not a nonzero exit code) when the executable truly
        # doesn't exist -- reconstruct() must catch that and return a
        # normal failed ReconstructionResult, never propagate the raw
        # OSError out of the engine.
        engine = CustomCLIEngine(CLIEngineConfig(name="ghost", executable="/no/such/tool-xyz-does-not-exist"))
        request = ReconstructionRequest(job_id="j", part_id="p", input_path="in.png", output_path="out.png")
        result = engine.reconstruct(request)  # must not raise
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.category, ErrorCategory.SUBPROCESS_ERROR)
        self.assertIn("/no/such/tool-xyz-does-not-exist", result.error.message)

    def test_missing_executable_upscale_also_returns_failed_result(self):
        from core.models.engine_models import UpscaleRequest
        engine = CustomCLIEngine(CLIEngineConfig(name="ghost", executable="/no/such/tool-xyz-does-not-exist"))
        request = UpscaleRequest(job_id="j", part_id="p", input_path="in.png", output_path="out.png")
        result = engine.upscale(request)  # must not raise
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_successful_reconstruct_writes_logs_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            script = _write_fake_cli(tmp_dir)
            output = tmp_dir / "out.png"
            logs_dir = tmp_dir / "job_logs"
            bus = EventBus()
            progress_events = []
            bus.subscribe(EventType.JOB_PROGRESS, progress_events.append)

            config = CLIEngineConfig(
                name="fake", executable=sys.executable, fixed_arguments=[str(script)],
                strength_argument=None, seed_argument=None, guidance_argument=None, mask_argument=None,
            )
            engine = CustomCLIEngine(config, capabilities=EngineCapabilities(supports_prompt=True),
                                      event_bus=bus, logs_dir=str(logs_dir))
            request = ReconstructionRequest(
                job_id="job_1", part_id="part_1",
                input_path=str(tmp_dir / "in.png"), output_path=str(output), prompt="restore",
            )
            (tmp_dir / "in.png").write_bytes(b"placeholder")

            result = engine.reconstruct(request)

            self.assertTrue(result.success, result.error.message if result.error else None)
            self.assertTrue(output.exists())
            self.assertGreater(len(progress_events), 0)

            job_dir = logs_dir / "job_1"
            self.assertTrue((job_dir / "stdout.log").exists())
            self.assertTrue((job_dir / "stderr.log").exists())
            self.assertTrue((job_dir / "command.txt").exists())
            self.assertTrue((job_dir / "environment.json").exists())
            result_json = json.loads((job_dir / "result.json").read_text())
            self.assertEqual(result_json["exit_code"], 0)

            command_text = (job_dir / "command.txt").read_text()
            self.assertIn(str(script), command_text)
            self.assertIn("restore", command_text)

    def test_failed_reconstruct_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            script = _write_fake_cli(tmp_dir)
            config = CLIEngineConfig(
                name="fake", executable=sys.executable,
                fixed_arguments=[str(script), "--fail"],
                strength_argument=None, seed_argument=None, guidance_argument=None, mask_argument=None,
            )
            engine = CustomCLIEngine(config)
            request = ReconstructionRequest(
                job_id="job_2", part_id="part_2",
                input_path=str(tmp_dir / "in.png"), output_path=str(tmp_dir / "out.png"),
            )
            (tmp_dir / "in.png").write_bytes(b"x")
            result = engine.reconstruct(request)
            self.assertFalse(result.success)
            self.assertIsNotNone(result.error)
            self.assertIn("simulated failure", result.stderr)

    def test_cancel_running_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            script = _write_fake_cli(tmp_dir)
            config = CLIEngineConfig(
                name="fake", executable=sys.executable,
                fixed_arguments=[str(script), "--hang"],
                strength_argument=None, seed_argument=None, guidance_argument=None, mask_argument=None,
                prompt_argument=None, negative_prompt_argument=None,
            )
            engine = CustomCLIEngine(config)
            request = ReconstructionRequest(
                job_id="job_3", part_id="part_3",
                input_path=str(tmp_dir / "in.png"), output_path=str(tmp_dir / "out.png"),
            )
            (tmp_dir / "in.png").write_bytes(b"x")

            def _cancel_soon():
                time.sleep(0.2)
                engine.cancel("job_3")

            threading.Thread(target=_cancel_soon).start()
            result = engine.reconstruct(request)
            self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
