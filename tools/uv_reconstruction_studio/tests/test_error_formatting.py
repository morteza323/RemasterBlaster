"""Tests for the rich, actionable failure-report formatter (spec §28)."""

from __future__ import annotations

import unittest

from core.models.engine_models import ErrorCategory
from core.models.job import Job, JobState
from core.models.part import Bounds, Part
from core.error_formatting import format_job_failure, format_engine_error_summary


def _failed_job(category=None, message="boom") -> Job:
    job = Job(part_id="part_1", project_id="proj_1", engine="flux2")
    for state in (JobState.VALIDATING, JobState.QUEUED, JobState.PREPARING, JobState.RUNNING):
        job.transition(state)
    job.transition(JobState.FAILED)
    job.error_category = category.value if category else None
    job.error_message = message
    return job


class TestFormatJobFailure(unittest.TestCase):
    def test_includes_engine_part_and_message(self):
        job = _failed_job(ErrorCategory.SUBPROCESS_ERROR, "Could not launch 'flux-cli': No such file")
        part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10), name="Part_07")
        report = format_job_failure(job, part, engine_display_name="Flux.2 Klein 4B")

        self.assertIn("Flux.2 Klein 4B", report)
        self.assertIn("Part_07", report)
        self.assertIn("Could not launch", report)
        self.assertIn("Check:", report)

    def test_checklist_is_category_specific(self):
        subprocess_report = format_job_failure(_failed_job(ErrorCategory.SUBPROCESS_ERROR))
        model_report = format_job_failure(_failed_job(ErrorCategory.MODEL_ERROR))
        self.assertNotEqual(subprocess_report, model_report)
        self.assertIn("executable", subprocess_report.lower())
        self.assertIn("model", model_report.lower())

    def test_unknown_category_falls_back_to_default_checklist(self):
        job = _failed_job(category=None, message="mystery failure")
        report = format_job_failure(job)
        self.assertIn("diagnostics", report.lower())

    def test_works_without_a_part_object(self):
        job = _failed_job(ErrorCategory.INPUT_ERROR)
        report = format_job_failure(job, part=None)
        self.assertIn(job.part_id, report)  # falls back to the raw part id

    def test_never_raises_on_minimal_job(self):
        job = Job(part_id="p", project_id="proj", engine="mock")
        # No error_category/message set at all.
        report = format_job_failure(job)
        self.assertIsInstance(report, str)
        self.assertIn("Check:", report)


class TestFormatEngineErrorSummary(unittest.TestCase):
    def test_one_liner_includes_command(self):
        summary = format_engine_error_summary("flux2", "part_1", "exit code 1", command=["flux-cli", "--input", "a.png"])
        self.assertIn("flux2", summary)
        self.assertIn("part_1", summary)
        self.assertIn("flux-cli", summary)

    def test_one_liner_without_command(self):
        summary = format_engine_error_summary("mock", "part_2", "simulated failure")
        self.assertIn("mock", summary)
        self.assertNotIn("command:", summary)


if __name__ == "__main__":
    unittest.main()
