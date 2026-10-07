"""
QtEventBridge: the ONE place EventBus events cross from a background
worker thread (QueueManager's ThreadPoolExecutor, spec §32) onto the
Qt GUI thread.

Qt signal/slot connections are automatically queued when the emitting
call happens on a different thread than the receiving QObject lives
on, *provided* that QObject was constructed on the GUI thread and the
app's event loop is running. That's the entire thread-safety story
here -- .emit() itself is safe to call from any thread.

IMPORTANT: construct this only after QApplication exists and only on
the GUI thread (e.g. inside MainWindow.__init__), never from a worker.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from core.events.bus import Event, EventBus


class QtEventBridge(QObject):
    event_received = pyqtSignal(object)  # carries an Event

    def __init__(self, event_bus: EventBus, parent=None):
        super().__init__(parent)
        self._event_bus = event_bus
        event_bus.subscribe_all(self._on_event)

    def _on_event(self, event: Event) -> None:
        # May run on a worker thread -- Qt marshals delivery of
        # event_received onto this object's (GUI) thread automatically.
        self.event_received.emit(event)
