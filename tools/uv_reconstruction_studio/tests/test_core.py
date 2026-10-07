"""
Phase 1 test suite (spec §77-78): job state machine, guide/part/bounds
validation, event bus dispatch, engine registry, and a full mock-engine
reconstruct/cancel/failure cycle. Stdlib unittest only -- no extra
dependencies needed to prove the architecture works.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.events.bus import EventBus
from core.events.types import EventType
from core.models.engine_models import ReconstructionRequest
from core.models.guide import Guide, GuideOrientation
from core.models.job import InvalidJobTransition, Job, JobState
from core.models.part import Bounds, Part
from core.models.project import Project, SourceTexture
from engines.mock.mock_engine import MockEngine
from engines.registry import EngineRegistry


class TestGuide(unittest.TestCase):
    def test_rejects_float_position(self):
        with self.assertRaises(TypeError):
            Guide(orientation=GuideOrientation.HORIZONTAL, position=12.5)

    def test_rejects_negative_position(self):
        with self.assertRaises(ValueError):
            Guide(orientation=GuideOrientation.VERTICAL, position=-1)

    def test_round_trip(self):
        g = Guide(orientation=GuideOrientation.HORIZONTAL, position=512)
        restored = Guide.from_dict(g.to_dict())
        self.assertEqual(g.id, restored.id)
        self.assertEqual(g.position, restored.position)
        self.assertEqual(g.orientation, restored.orientation)


class TestBounds(unittest.TestCase):
    def test_rejects_non_positive_dims(self):
        with self.assertRaises(ValueError):
            Bounds(x=0, y=0, width=0, height=10)

    def test_padded_clamps_at_zero(self):
        b = Bounds(x=10, y=10, width=100, height=100)
        padded = b.padded(64)
        self.assertEqual(padded.x, 0)
        self.assertEqual(padded.y, 0)
        # width grows by however much x actually moved, plus the full
        # padding on the far edge (see Bounds.padded docstring/impl).
        self.assertEqual(padded.width, 100 + 10 + 64)


class TestJobStateMachine(unittest.TestCase):
    def test_happy_path(self):
        job = Job(part_id="p1", project_id="proj1", engine="mock")
        for target in (JobState.VALIDATING, JobState.QUEUED, JobState.PREPARING,
                       JobState.RUNNING, JobState.POST_PROCESSING,
                       JobState.VALIDATING_OUTPUT, JobState.COMPLETED):
            job.transition(target)
        self.assertEqual(job.state, JobState.COMPLETED)
        self.assertEqual(len(job.state_machine.history), 7)

    def test_rejects_illegal_jump(self):
        job = Job(part_id="p1", project_id="proj1", engine="mock")
        with self.assertRaises(InvalidJobTransition):
            job.transition(JobState.COMPLETED)

    def test_terminal_states_have_no_exit(self):
        job = Job(part_id="p1", project_id="proj1", engine="mock")
        job.transition(JobState.VALIDATING)
        job.transition(JobState.QUEUED)
        job.transition(JobState.PREPARING)
        job.transition(JobState.RUNNING)
        job.transition(JobState.CANCELLED)
        with self.assertRaises(InvalidJobTransition):
            job.transition(JobState.RUNNING)

    def test_failed_can_only_retry(self):
        job = Job(part_id="p1", project_id="proj1", engine="mock")
        job.transition(JobState.VALIDATING)
        job.transition(JobState.QUEUED)
        job.transition(JobState.PREPARING)
        job.transition(JobState.RUNNING)
        job.transition(JobState.FAILED)
        self.assertTrue(job.state_machine.can_transition(JobState.RETRYING))
        self.assertFalse(job.state_machine.can_transition(JobState.RUNNING))


class TestEventBus(unittest.TestCase):
    def test_publish_dispatches_to_subscriber(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.JOB_STARTED, received.append)
        bus.publish(EventType.JOB_STARTED, job_id="job_1")
        bus.publish(EventType.JOB_COMPLETED, job_id="job_1")  # should NOT be received
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].type, EventType.JOB_STARTED)
        self.assertEqual(received[0].job_id, "job_1")

    def test_subscribe_all_receives_everything(self):
        bus = EventBus()
        received = []
        bus.subscribe_all(received.append)
        bus.publish(EventType.JOB_STARTED, job_id="job_1")
        bus.publish(EventType.JOB_COMPLETED, job_id="job_1")
        self.assertEqual(len(received), 2)

    def test_thread_safety_smoke(self):
        bus = EventBus()
        count = {"n": 0}
        lock = threading.Lock()

        def handler(event):
            with lock:
                count["n"] += 1

        bus.subscribe_all(handler)

        def publisher():
            for _ in range(50):
                bus.publish(EventType.JOB_PROGRESS, job_id="job_x")

        threads = [threading.Thread(target=publisher) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(count["n"], 200)


class TestEngineRegistry(unittest.TestCase):
    def test_unknown_engine_raises(self):
        registry = EngineRegistry()
        with self.assertRaises(KeyError):
            registry.create("does_not_exist")

    def test_default_is_first_registered(self):
        registry = EngineRegistry()
        registry.register("mock", lambda **cfg: MockEngine(**cfg))
        self.assertEqual(registry.get_default(), "mock")


class TestMockEngine(unittest.TestCase):
    def test_reconstruct_success_writes_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / "in.png"
            input_path.write_bytes(b"fake-image-bytes")
            output_path = tmp_dir / "out.png"

            engine = MockEngine(total_steps=2, step_delay=0.0)
            request = ReconstructionRequest(
                job_id="job_1", part_id="part_1",
                input_path=str(input_path), output_path=str(output_path),
            )
            result = engine.reconstruct(request)

            self.assertTrue(result.success)
            self.assertTrue(output_path.exists())
            self.assertEqual(output_path.read_bytes(), input_path.read_bytes())
            self.assertIsNotNone(result.actual_seed)

    def test_simulated_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            request = ReconstructionRequest(
                job_id="job_2", part_id="part_2",
                input_path=str(tmp_dir / "missing.png"),
                output_path=str(tmp_dir / "out.png"),
                extra={"simulate_failure": True},
            )
            engine = MockEngine(total_steps=2, step_delay=0.0)
            result = engine.reconstruct(request)
            self.assertFalse(result.success)
            self.assertIsNotNone(result.error)

    def test_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            engine = MockEngine(total_steps=20, step_delay=0.05)
            request = ReconstructionRequest(
                job_id="job_3", part_id="part_3",
                input_path=str(tmp_dir / "in.png"),
                output_path=str(tmp_dir / "out.png"),
            )

            def cancel_soon():
                time.sleep(0.1)
                engine.cancel("job_3")

            threading.Thread(target=cancel_soon).start()
            result = engine.reconstruct(request)
            self.assertFalse(result.success)

    def test_events_are_published(self):
        bus = EventBus()
        progress_events = []
        bus.subscribe(EventType.JOB_PROGRESS, progress_events.append)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            engine = MockEngine(event_bus=bus, total_steps=3, step_delay=0.0)
            request = ReconstructionRequest(
                job_id="job_4", part_id="part_4",
                input_path=str(tmp_dir / "in.png"),
                output_path=str(tmp_dir / "out.png"),
            )
            engine.reconstruct(request)

        self.assertEqual(len(progress_events), 3)
        self.assertEqual(progress_events[-1].data["progress"], 1.0)


class TestProjectRoundTrip(unittest.TestCase):
    def test_to_dict_from_dict(self):
        project = Project(name="Demo", source=SourceTexture(filename="a.png", width=100, height=100))
        project.add_guide(Guide(orientation=GuideOrientation.HORIZONTAL, position=50))
        project.add_part(Part(source_texture="a.png", bounds=Bounds(x=0, y=0, width=50, height=50)))

        data = project.to_dict()
        restored = Project.from_dict(data)

        self.assertEqual(restored.id, project.id)
        self.assertEqual(restored.name, project.name)
        self.assertEqual(len(restored.guides), 1)
        self.assertEqual(restored.source.width, 100)


if __name__ == "__main__":
    unittest.main()
