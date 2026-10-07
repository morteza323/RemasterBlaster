"""
Job state machine (spec §30-31).

Invalid transitions are rejected -- nowhere else in the codebase
should a job's state be assigned as a bare string.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Dict, List, Optional


@unique
class JobState(str, Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    POST_PROCESSING = "POST_PROCESSING"
    VALIDATING_OUTPUT = "VALIDATING_OUTPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"
    PAUSED = "PAUSED"
    RETRYING = "RETRYING"


# Allowed transitions, per spec §31's state diagram plus the
# validate/queue/prepare/post-process/validate-output stages named
# in §30. RETRYING re-enters QUEUED rather than RUNNING directly so a
# retried job goes through the same validation/preparation path as a
# fresh one.
_ALLOWED_TRANSITIONS: Dict[JobState, frozenset] = {
    JobState.CREATED: frozenset({JobState.VALIDATING, JobState.CANCELLED}),
    JobState.VALIDATING: frozenset({JobState.QUEUED, JobState.FAILED, JobState.CANCELLED}),
    JobState.QUEUED: frozenset({JobState.PREPARING, JobState.CANCELLED, JobState.SKIPPED, JobState.PAUSED}),
    JobState.PREPARING: frozenset({JobState.RUNNING, JobState.FAILED, JobState.CANCELLED}),
    JobState.RUNNING: frozenset({
        JobState.POST_PROCESSING, JobState.FAILED, JobState.CANCELLED, JobState.PAUSED,
    }),
    JobState.POST_PROCESSING: frozenset({JobState.VALIDATING_OUTPUT, JobState.FAILED}),
    JobState.VALIDATING_OUTPUT: frozenset({JobState.COMPLETED, JobState.FAILED}),
    JobState.PAUSED: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.FAILED: frozenset({JobState.RETRYING}),
    JobState.CANCELLED: frozenset(),
    JobState.COMPLETED: frozenset(),
    JobState.SKIPPED: frozenset({JobState.QUEUED}),
    JobState.RETRYING: frozenset({JobState.QUEUED, JobState.CANCELLED}),
}


class InvalidJobTransition(Exception):
    def __init__(self, current: JobState, target: JobState):
        super().__init__(f"Cannot transition job from {current.value} to {target.value}")
        self.current = current
        self.target = target


@dataclass
class JobStateMachine:
    state: JobState = JobState.CREATED
    history: List[Dict[str, Any]] = field(default_factory=list)

    def can_transition(self, target: JobState) -> bool:
        return target in _ALLOWED_TRANSITIONS.get(self.state, frozenset())

    def transition(self, target: JobState) -> None:
        if not self.can_transition(target):
            raise InvalidJobTransition(self.state, target)
        self.history.append({
            "from": self.state.value,
            "to": target.value,
            "timestamp": time.time(),
        })
        self.state = target


@dataclass
class Job:
    """A single unit of work: one engine operation against one part."""

    part_id: str
    project_id: str
    engine: str
    id: str = field(default_factory=lambda: f"job_{uuid.uuid4().hex[:8]}")
    state_machine: JobStateMachine = field(default_factory=JobStateMachine)
    command: Optional[List[str]] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    progress: float = 0.0
    step: Optional[int] = None
    total_steps: Optional[int] = None

    @property
    def state(self) -> JobState:
        return self.state_machine.state

    def transition(self, target: JobState) -> None:
        self.state_machine.transition(target)
        if target == JobState.RUNNING and self.started_at is None:
            self.started_at = time.time()
        if target in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
            self.ended_at = time.time()

    def duration_seconds(self) -> Optional[float]:
        if self.started_at is None:
            return None
        end = self.ended_at if self.ended_at is not None else time.time()
        return end - self.started_at
