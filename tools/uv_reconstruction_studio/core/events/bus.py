"""
EventBus: the single channel GUI, CLI, and logging all subscribe to.

Spec §7. Nothing in this codebase should print a user-facing status
message directly -- it should publish an Event and let subscribers
(a future Qt panel, the CLI logger, the file logger) render it their
own way. This keeps GUI and CLI guaranteed to agree (spec §6).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.events.types import EventType

EventHandler = Callable[["Event"], None]


@dataclass
class Event:
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Common correlation fields -- convenience accessors into `data`.
    @property
    def project_id(self) -> Optional[str]:
        return self.data.get("project_id")

    @property
    def job_id(self) -> Optional[str]:
        return self.data.get("job_id")

    @property
    def part_id(self) -> Optional[str]:
        return self.data.get("part_id")

    @property
    def engine(self) -> Optional[str]:
        return self.data.get("engine")


class EventBus:
    """Thread-safe publish/subscribe hub.

    Jobs run on worker threads (spec §32: the GUI thread must never
    block), so subscription and dispatch must be safe to call from
    any thread. Handlers are invoked synchronously on the publisher's
    thread -- a future Qt subscriber is expected to marshal onto the
    GUI thread itself (e.g. via a Qt signal) rather than block here.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: Dict[EventType, List[EventHandler]] = {}
        self._global_subscribers: List[EventHandler] = []
        self._history: List[Event] = []
        self._history_cap = 5000

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to every event type (used by the CLI logger)."""
        with self._lock:
            self._global_subscribers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        with self._lock:
            handlers = self._subscribers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def publish(self, event_type: EventType, **data: Any) -> Event:
        event = Event(type=event_type, data=data)
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._history_cap:
                self._history.pop(0)
            handlers = list(self._subscribers.get(event_type, []))
            global_handlers = list(self._global_subscribers)

        # Dispatch outside the lock so a slow/broken handler can't
        # deadlock other publishers.
        for handler in handlers + global_handlers:
            handler(event)
        return event

    def history(self, event_type: Optional[EventType] = None) -> List[Event]:
        with self._lock:
            if event_type is None:
                return list(self._history)
            return [e for e in self._history if e.type == event_type]
