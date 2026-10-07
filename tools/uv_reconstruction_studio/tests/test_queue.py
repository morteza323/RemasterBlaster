"""Phase 6 tests: QueueManager against the MockEngine -- concurrency
cap, non-blocking start_all, pause/resume, cancel, retry, skip,
reorder, duplicate, remove, and unhandled-exception containment."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.events.bus import EventBus
from core.events.types import EventType
from core.models.engine_models import ReconstructionRequest
from core.models.job import JobState
from core.models.part import Bounds, Part, PartStatus
from engines.mock.mock_engine import MockEngine
from engines.registry import EngineRegistry
from pipeline.queue_manager import QueueManager


def _make_registry(event_bus=None, total_steps=2, step_delay=0.02):
    registry = EngineRegistry()
    registry.register("mock", lambda **cfg: MockEngine(
        event_bus=event_bus, total_steps=total_steps, step_delay=step_delay))
    return registry


def _request_builder(tmp_dir: Path):
    def _build(part: Part, job) -> ReconstructionRequest:
        input_path = tmp_dir / f"{part.id}_in.png"
        if not input_path.exists():
            input_path.write_bytes(b"fake")
        output_path = tmp_dir / f"{part.id}_{job.id}_out.png"
        extra = {"simulate_failure": bool(getattr(part, "_force_fail", False))}
        return ReconstructionRequest(
            job_id=job.id, part_id=part.id,
            input_path=str(input_path), output_path=str(output_path), extra=extra,
        )
    return _build


class TestQueueBasics(unittest.TestCase):
    def test_add_creates_job_and_publishes_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            created = []
            bus.subscribe(EventType.JOB_CREATED, created.append)
            qm = QueueManager(_make_registry(bus), _request_builder(Path(tmp)), event_bus=bus)
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            self.assertEqual(job.state, JobState.CREATED)
            self.assertEqual(len(created), 1)
            qm.shutdown()

    def test_queue_itself_publishes_job_completed(self):
        # Regression test: JOB_COMPLETED must come from QueueManager
        # (the authoritative source of final job state), not only from
        # whatever the engine adapter happens to also publish.
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            completed_events = []
            bus.subscribe(EventType.JOB_COMPLETED, completed_events.append)
            qm = QueueManager(_make_registry(step_delay=0.01), _request_builder(Path(tmp)), event_bus=bus)
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            qm.start_all()
            qm.wait_idle(timeout=5)
            qm.shutdown()
            self.assertEqual(job.state, JobState.COMPLETED)
            self.assertGreaterEqual(len(completed_events), 1)
            self.assertEqual(completed_events[-1].job_id, job.id)

    def test_queue_itself_publishes_job_failed(self):
        # Regression test: JOB_FAILED is defined in the event
        # vocabulary but was never actually published by anything --
        # QueueManager must publish it on every path that lands a job
        # in the FAILED state.
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            failed_events = []
            bus.subscribe(EventType.JOB_FAILED, failed_events.append)
            qm = QueueManager(_make_registry(step_delay=0.01), _request_builder(Path(tmp)), event_bus=bus)
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            part._force_fail = True
            job = qm.add(part, "mock")
            qm.start_all()
            qm.wait_idle(timeout=5)
            qm.shutdown()
            self.assertEqual(job.state, JobState.FAILED)
            self.assertEqual(len(failed_events), 1)
            self.assertEqual(failed_events[0].job_id, job.id)
            self.assertIsNotNone(failed_events[0].data.get("message"))

    def test_start_all_does_not_block_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(step_delay=0.3), _request_builder(Path(tmp)))
            parts = [Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10)) for _ in range(3)]
            for p in parts:
                qm.add(p, "mock")

            start = time.time()
            qm.start_all()
            elapsed = time.time() - start
            self.assertLess(elapsed, 0.2, "start_all() must return immediately, not block on job completion")

            self.assertTrue(qm.wait_idle(timeout=5))
            qm.shutdown()
            for p in parts:
                self.assertEqual(p.status, PartStatus.COMPLETED)

    def test_concurrency_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(step_delay=0.1), _request_builder(Path(tmp)), max_concurrent_jobs=2)
            parts = [Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10)) for _ in range(6)]
            for p in parts:
                qm.add(p, "mock")

            qm.start_all()
            time.sleep(0.05)  # let workers pick up jobs
            running = [j for j in qm.get_jobs() if j.state == JobState.RUNNING]
            self.assertLessEqual(len(running), 2)

            qm.wait_idle(timeout=5)
            qm.shutdown()
            self.assertTrue(all(p.status == PartStatus.COMPLETED for p in parts))


class TestPauseResume(unittest.TestCase):
    def test_pause_prevents_start_all_from_submitting(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(step_delay=0.02), _request_builder(Path(tmp)))
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            qm.add(part, "mock")
            qm.pause()
            qm.start_all()
            time.sleep(0.05)
            self.assertEqual(qm.get_jobs()[0].state, JobState.CREATED)

            qm.resume()
            qm.start_all()
            qm.wait_idle(timeout=5)
            qm.shutdown()
            self.assertEqual(qm.get_jobs()[0].state, JobState.COMPLETED)

    def test_process_selected_bypasses_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(step_delay=0.02), _request_builder(Path(tmp)))
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            qm.pause()
            qm.process_selected([job.id])
            qm.wait_idle(timeout=5)
            qm.shutdown()
            self.assertEqual(job.state, JobState.COMPLETED)


class TestCancel(unittest.TestCase):
    def test_cancel_queued_job_before_it_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(step_delay=0.02), _request_builder(Path(tmp)))
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            qm.cancel(job.id)
            self.assertEqual(job.state, JobState.CANCELLED)
            qm.shutdown()

    def test_cancel_running_job_is_cooperative(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(total_steps=50, step_delay=0.02), _request_builder(Path(tmp)),
                               max_concurrent_jobs=1)
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            qm.start_all()

            # wait until it's actually running before cancelling
            for _ in range(100):
                if job.state == JobState.RUNNING:
                    break
                time.sleep(0.01)
            self.assertEqual(job.state, JobState.RUNNING)

            qm.cancel(job.id)
            qm.wait_idle(timeout=5)
            qm.shutdown()
            self.assertEqual(job.state, JobState.FAILED)  # MockEngine reports cancellation as a failed result


class TestRetrySkipReorderDuplicateRemove(unittest.TestCase):
    def test_retry_failed_job_succeeds_second_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(step_delay=0.01), _request_builder(Path(tmp)))
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            part._force_fail = True
            job = qm.add(part, "mock")
            qm.start_all()
            qm.wait_idle(timeout=5)
            self.assertEqual(job.state, JobState.FAILED)

            part._force_fail = False
            qm.retry(job.id)
            qm.wait_idle(timeout=5)
            qm.shutdown()
            self.assertEqual(job.state, JobState.COMPLETED)

    def test_retry_non_failed_job_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(), _request_builder(Path(tmp)))
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            with self.assertRaises(Exception):
                qm.retry(job.id)
            qm.shutdown()

    def test_process_failed_retries_all_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(step_delay=0.01), _request_builder(Path(tmp)))
            parts = []
            for _ in range(2):
                p = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
                p._force_fail = True
                parts.append(p)
                qm.add(p, "mock")
            qm.start_all()
            qm.wait_idle(timeout=5)
            self.assertTrue(all(j.state == JobState.FAILED for j in qm.get_jobs()))

            for p in parts:
                p._force_fail = False
            qm.process_failed()
            qm.wait_idle(timeout=5)
            qm.shutdown()
            self.assertTrue(all(j.state == JobState.COMPLETED for j in qm.get_jobs()))

    def test_skip_from_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(), _request_builder(Path(tmp)))
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            qm.skip(job.id)
            self.assertEqual(job.state, JobState.SKIPPED)
            self.assertEqual(part.status, PartStatus.SKIPPED)
            qm.shutdown()

    def test_reorder(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(), _request_builder(Path(tmp)))
            jobs = [qm.add(Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10)), "mock")
                    for _ in range(3)]
            new_order = [jobs[2].id, jobs[0].id, jobs[1].id]
            qm.reorder(new_order)
            self.assertEqual([j.id for j in qm.get_jobs()], new_order)
            qm.shutdown()

    def test_reorder_rejects_partial_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(), _request_builder(Path(tmp)))
            jobs = [qm.add(Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10)), "mock")
                    for _ in range(2)]
            with self.assertRaises(ValueError):
                qm.reorder([jobs[0].id])
            qm.shutdown()

    def test_duplicate_creates_independent_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(step_delay=0.01), _request_builder(Path(tmp)))
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            dup = qm.duplicate(job.id)
            self.assertNotEqual(job.id, dup.id)
            self.assertEqual(len(qm.get_jobs()), 2)
            qm.shutdown()

    def test_remove_pending_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(), _request_builder(Path(tmp)))
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            qm.remove(job.id)
            self.assertEqual(qm.get_jobs(), [])
            qm.shutdown()

    def test_remove_running_job_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            qm = QueueManager(_make_registry(total_steps=50, step_delay=0.02), _request_builder(Path(tmp)))
            part = Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=10, height=10))
            job = qm.add(part, "mock")
            qm.start_all()
            for _ in range(100):
                if job.state == JobState.RUNNING:
                    break
                time.sleep(0.01)
            with self.assertRaises(ValueError):
                qm.remove(job.id)
            qm.cancel(job.id)
            qm.wait_idle(timeout=5)
            qm.shutdown()


if __name__ == "__main__":
    unittest.main()
