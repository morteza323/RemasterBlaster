"""
QueueManager (spec §29, §32): the first-class queue subsystem.

Runs jobs on a bounded worker pool (spec §62's "Max concurrent jobs",
default conservative per app.constants) so the calling thread --
a future GUI's event loop -- never blocks (spec §32, §34). Every state
change and lifecycle event is published on the shared EventBus, so a
CLI logger and a future GUI panel render the identical stream (spec §6).

QueueManager does NOT know about file paths, prompts, or images --
callers supply a `request_builder(part, job) -> ReconstructionRequest`
callback. That keeps this module reusable regardless of how Phase 2's
project storage or Phase 5's part generation laid out the disk.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from typing import Callable, Dict, List, Optional

from app.constants import DEFAULT_MAX_CONCURRENT_JOBS
from core.events.bus import EventBus
from core.events.types import EventType
from core.models.engine_models import ReconstructionRequest
from core.models.job import InvalidJobTransition, Job, JobState
from core.models.part import Part, PartStatus, PartVersion
from engines.base.engine import ReconstructionEngine
from engines.registry import EngineRegistry

RequestBuilder = Callable[[Part, Job], ReconstructionRequest]


class QueueManager:
    def __init__(
        self,
        engine_registry: EngineRegistry,
        request_builder: RequestBuilder,
        event_bus: Optional[EventBus] = None,
        max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.engine_registry = engine_registry
        self.request_builder = request_builder
        self.event_bus = event_bus or EventBus()
        self.max_concurrent_jobs = max(1, max_concurrent_jobs)
        self.logger = logger

        self._jobs: List[Job] = []
        self._parts_by_id: Dict[str, Part] = {}
        self._engines: Dict[str, ReconstructionEngine] = {}
        self._futures: Dict[str, "concurrent.futures.Future"] = {}
        self._lock = threading.RLock()
        self._paused = False
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_concurrent_jobs, thread_name_prefix="uvrs-worker"
        )

    # -- engine caching ----------------------------------------------------
    # Engines are cached per name so cancel() calls land on the SAME
    # instance that's actually running the job -- EngineRegistry.create()
    # would otherwise hand back a fresh, unrelated instance every time.

    def _get_engine(self, name: str) -> ReconstructionEngine:
        with self._lock:
            if name not in self._engines:
                self._engines[name] = self.engine_registry.create(name)
            return self._engines[name]

    def _emit(self, event_type: EventType, **data) -> None:
        self.event_bus.publish(event_type, **data)

    def _get_job(self, job_id: str) -> Job:
        with self._lock:
            for job in self._jobs:
                if job.id == job_id:
                    return job
        raise KeyError(f"No job with id {job_id}")

    # -- building the queue --------------------------------------------------

    def add(self, part: Part, engine: str, project_id: str = "") -> Job:
        job = Job(part_id=part.id, project_id=project_id or part.id, engine=engine)
        with self._lock:
            self._parts_by_id[part.id] = part
            self._jobs.append(job)
        self._emit(EventType.JOB_CREATED, job_id=job.id, part_id=part.id, engine=engine)
        return job

    def get_jobs(self) -> List[Job]:
        with self._lock:
            return list(self._jobs)

    # -- submission ------------------------------------------------------

    def _submit(self, job: Job) -> None:
        if job.state == JobState.CREATED:
            job.transition(JobState.VALIDATING)
            job.transition(JobState.QUEUED)
        elif job.state == JobState.RETRYING:
            job.transition(JobState.QUEUED)
        elif job.state != JobState.QUEUED:
            raise InvalidJobTransition(job.state, JobState.QUEUED)

        future = self._executor.submit(self._run_job_safe, job)
        with self._lock:
            self._futures[job.id] = future

    def start_all(self) -> None:
        """Submits every CREATED job. No-ops while paused (spec §29's
        'pause' control) -- explicit process_*/retry_* calls below
        bypass pause, since those are direct per-item user actions."""
        with self._lock:
            if self._paused:
                return
            creatable = [j for j in self._jobs if j.state == JobState.CREATED]
        for job in creatable:
            self._submit(job)

    def process_pending(self) -> None:
        with self._lock:
            creatable = [j for j in self._jobs if j.state == JobState.CREATED]
        for job in creatable:
            self._submit(job)

    def process_selected(self, job_ids: List[str]) -> None:
        for job_id in job_ids:
            job = self._get_job(job_id)
            if job.state == JobState.CREATED:
                self._submit(job)

    def process_failed(self) -> None:
        with self._lock:
            failed = [j for j in self._jobs if j.state == JobState.FAILED]
        for job in failed:
            self.retry(job.id)

    # -- pause / resume ----------------------------------------------------

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    # -- cancel --------------------------------------------------------------

    def cancel(self, job_id: str) -> None:
        job = self._get_job(job_id)
        state = job.state
        if state == JobState.RUNNING:
            engine = self._get_engine(job.engine)
            engine.cancel(job_id)  # cooperative -- engine finishes its own reconstruct() call
        elif state in (JobState.CREATED, JobState.VALIDATING, JobState.QUEUED, JobState.PAUSED):
            job.transition(JobState.CANCELLED)
            self._emit(EventType.JOB_CANCELLED, job_id=job.id, part_id=job.part_id)
        # Terminal states (COMPLETED/FAILED/CANCELLED/SKIPPED): no-op.

    def cancel_selected(self, job_ids: List[str]) -> None:
        for job_id in job_ids:
            self.cancel(job_id)

    # -- retry (spec §72) ------------------------------------------------

    def retry(self, job_id: str) -> None:
        job = self._get_job(job_id)
        if job.state != JobState.FAILED:
            raise InvalidJobTransition(job.state, JobState.RETRYING)
        job.transition(JobState.RETRYING)
        self._submit(job)

    def retry_failed(self) -> None:
        self.process_failed()

    def retry_selected(self, job_ids: List[str]) -> None:
        for job_id in job_ids:
            job = self._get_job(job_id)
            if job.state == JobState.FAILED:
                self.retry(job.id)

    # -- skip / reorder / duplicate / remove --------------------------------

    def skip(self, job_id: str) -> None:
        job = self._get_job(job_id)
        if job.state == JobState.CREATED:
            job.transition(JobState.VALIDATING)
            job.transition(JobState.QUEUED)
        job.transition(JobState.SKIPPED)
        part = self._parts_by_id.get(job.part_id)
        if part is not None:
            part.status = PartStatus.SKIPPED

    def reorder(self, job_ids_in_new_order: List[str]) -> None:
        with self._lock:
            by_id = {j.id: j for j in self._jobs}
            if set(job_ids_in_new_order) != set(by_id):
                raise ValueError("reorder() requires exactly the current set of job ids")
            self._jobs = [by_id[jid] for jid in job_ids_in_new_order]

    def duplicate(self, job_id: str) -> Job:
        job = self._get_job(job_id)
        part = self._parts_by_id[job.part_id]
        new_job = Job(part_id=job.part_id, project_id=job.project_id, engine=job.engine)
        with self._lock:
            self._jobs.append(new_job)
        self._emit(EventType.JOB_CREATED, job_id=new_job.id, part_id=part.id, engine=new_job.engine)
        return new_job

    def remove(self, job_id: str) -> None:
        job = self._get_job(job_id)
        if job.state == JobState.RUNNING:
            raise ValueError("Cannot remove a running job -- cancel it first")
        with self._lock:
            self._jobs = [j for j in self._jobs if j.id != job_id]
            self._futures.pop(job_id, None)

    # -- worker body -----------------------------------------------------

    def _run_job_safe(self, job: Job) -> None:
        try:
            self._run_job(job)
        except InvalidJobTransition:
            raise
        except Exception as exc:  # noqa: BLE001 -- last-resort guard, spec §96
            # An engine adapter raising instead of returning a failed
            # Result is a bug in that adapter, but the queue must never
            # die silently or leave the job stuck mid-flight either way.
            if self.logger:
                self.logger.exception("Unhandled exception running job %s", job.id)
            try:
                if job.state not in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                    for target in (JobState.POST_PROCESSING, JobState.FAILED):
                        if job.state_machine.can_transition(target):
                            job.transition(target)
                    if job.state != JobState.FAILED and job.state_machine.can_transition(JobState.FAILED):
                        job.transition(JobState.FAILED)
            except InvalidJobTransition:
                pass
            job.error_message = str(exc)
            self._emit(EventType.JOB_ERROR, job_id=job.id, part_id=job.part_id, message=str(exc))
            if job.state == JobState.FAILED:
                self._emit(EventType.JOB_FAILED, job_id=job.id, part_id=job.part_id, engine=job.engine,
                           message=str(exc), category=job.error_category)
        finally:
            with self._lock:
                self._futures.pop(job.id, None)

    def _run_job(self, job: Job) -> None:
        part = self._parts_by_id[job.part_id]

        job.transition(JobState.PREPARING)
        engine = self._get_engine(job.engine)
        request = self.request_builder(part, job)

        part.status = PartStatus.PROCESSING
        job.transition(JobState.RUNNING)
        self._emit(EventType.JOB_STARTED, job_id=job.id, part_id=part.id, engine=job.engine)

        result = engine.reconstruct(request)

        if result.success:
            job.transition(JobState.POST_PROCESSING)
            job.transition(JobState.VALIDATING_OUTPUT)
            job.transition(JobState.COMPLETED)
            version = PartVersion(
                engine=job.engine,
                output_path=result.output_path or "",
                seed=result.actual_seed,
                command=result.command,
                duration_seconds=result.duration_seconds,
            )
            part.add_version(version)
            part.status = PartStatus.COMPLETED
            # QueueManager -- not the engine adapter -- is the
            # authoritative source of a job's final state (it's the
            # one doing job.transition(...) above), so it must publish
            # that state itself rather than depending on every current
            # and future engine adapter to also remember to (spec §6-7).
            self._emit(EventType.JOB_COMPLETED, job_id=job.id, part_id=part.id, engine=job.engine,
                       output_path=version.output_path)
        else:
            job.transition(JobState.FAILED)
            part.status = PartStatus.FAILED
            if result.error is not None:
                job.error_category = result.error.category.value
                job.error_message = result.error.message
            self._emit(EventType.JOB_FAILED, job_id=job.id, part_id=part.id, engine=job.engine,
                       message=job.error_message, category=job.error_category)

    # -- lifecycle -------------------------------------------------------

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        """Blocks until every currently-tracked job has finished.
        Test/CLI convenience -- a GUI would instead just watch events."""
        with self._lock:
            futures = list(self._futures.values())
        done = concurrent.futures.wait(futures, timeout=timeout)
        return len(done.not_done) == 0

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
