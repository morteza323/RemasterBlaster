"""Phase 16 tests: system info collection, engine diagnostics, and
diagnostic report export (including secret redaction)."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from diagnostics.engines import run_engine_diagnostics
from diagnostics.report import export_diagnostic_report
from diagnostics.system import collect_system_info
from engines.custom_cli.config import CLIEngineConfig
from engines.mock.mock_engine import MockEngine
from engines.registry import EngineRegistry


class TestSystemInfo(unittest.TestCase):
    def test_collects_without_raising(self):
        info = collect_system_info()
        self.assertIn("PIL", info.dependency_versions)
        self.assertIn("numpy", info.dependency_versions)
        self.assertNotEqual(info.dependency_versions["PIL"], "not installed")
        self.assertIsInstance(info.gpu_names, list)  # empty is fine -- no GPU assumed


class TestEngineDiagnostics(unittest.TestCase):
    def test_reports_mock_engine_ready(self):
        registry = EngineRegistry()
        registry.register("mock", lambda **cfg: MockEngine())
        results = run_engine_diagnostics(registry)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "mock")
        self.assertTrue(results[0].is_valid)

    def test_reports_missing_executable_as_invalid(self):
        from engines.custom_cli.engine import CustomCLIEngine
        registry = EngineRegistry()
        registry.register("ghost", lambda **cfg: CustomCLIEngine(CLIEngineConfig(name="ghost", executable="/no/such/tool")))
        results = run_engine_diagnostics(registry)
        self.assertFalse(results[0].is_valid)
        self.assertTrue(len(results[0].errors) > 0)


class TestDiagnosticReport(unittest.TestCase):
    def test_export_contains_expected_files_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            logs_dir = tmp_dir / "logs"
            (logs_dir / "engine").mkdir(parents=True)
            (logs_dir / "jobs").mkdir(parents=True)
            (logs_dir / "application.log").write_text("app log content")
            (logs_dir / "engine" / "mock.log").write_text("engine log content")
            (logs_dir / "jobs" / "job_1.log").write_text("job log content")

            cli_logs_dir = tmp_dir / "cli_logs"
            failed_job_dir = cli_logs_dir / "job_failed"
            failed_job_dir.mkdir(parents=True)
            (failed_job_dir / "result.json").write_text(json.dumps({"exit_code": 1}))
            (failed_job_dir / "command.txt").write_text("tool --input x --output y")
            ok_job_dir = cli_logs_dir / "job_ok"
            ok_job_dir.mkdir(parents=True)
            (ok_job_dir / "result.json").write_text(json.dumps({"exit_code": 0}))

            registry = EngineRegistry()
            registry.register("mock", lambda **cfg: MockEngine())

            engine_configs = {
                "flux2": CLIEngineConfig(name="flux2", executable="flux-cli",
                                          environment={"SECRET_TOKEN": "sk-super-secret"})
            }

            zip_path = tmp_dir / "report.zip"
            export_diagnostic_report(
                zip_path, logs_dir, registry,
                engine_configs=engine_configs, cli_logs_dir=cli_logs_dir,
            )

            self.assertTrue(zip_path.exists())
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                self.assertIn("system_info.json", names)
                self.assertIn("engine_diagnostics.json", names)
                self.assertIn("application.log", names)
                self.assertIn("engine_logs/mock.log", names)
                self.assertIn("recent_job_logs/job_1.log", names)
                self.assertIn("configuration_summary.json", names)
                self.assertIn("failed_commands.json", names)

                config_summary = json.loads(zf.read("configuration_summary.json"))
                self.assertEqual(config_summary["flux2"]["environment"]["SECRET_TOKEN"], "***REDACTED***")

                failed_commands = json.loads(zf.read("failed_commands.json"))
                job_ids = [f["job_id"] for f in failed_commands]
                self.assertIn("job_failed", job_ids)
                self.assertNotIn("job_ok", job_ids)


if __name__ == "__main__":
    unittest.main()
