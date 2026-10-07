"""
Log Viewer (spec §94): filter/search/clear/copy/save, fed by both the
EventBus (live job/engine events) and Python's `logging` module (the
hierarchical LoggingManager's records).

QtLogHandler deliberately does NOT subclass QObject itself (mixing
logging.Handler with QObject multiple-inheritance is a known source of
metaclass headaches with PyQt6's sip bindings) -- it owns a small
QObject "emitter" instead and forwards through that, using the same
safe cross-thread signal pattern as ui/event_bridge.py.

UNTESTED IN THIS ENVIRONMENT (see ui/canvas.py's note).
"""

from __future__ import annotations

import logging
from typing import List, Tuple

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from core.events.bus import Event
from core.events.types import EventType

_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
_ERROR_EVENT_TYPES = (EventType.JOB_ERROR, EventType.JOB_FAILED, EventType.ENGINE_ERROR)


class _LogSignalEmitter(QObject):
    record_emitted = pyqtSignal(str, str)  # level name, formatted message


class QtLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.emitter = _LogSignalEmitter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self.emitter.record_emitted.emit(record.levelname, message)


class LogConsolePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_lines: List[Tuple[str, str]] = []  # (level, text)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search...")
        self.search_edit.textChanged.connect(self._apply_filter)
        controls.addWidget(self.search_edit)

        self._level_checks = {}
        for level in _LEVELS:
            checkbox = QCheckBox(level)
            checkbox.setChecked(level != "DEBUG")
            checkbox.stateChanged.connect(self._apply_filter)
            self._level_checks[level] = checkbox
            controls.addWidget(checkbox)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self._clear)
        controls.addWidget(self.clear_button)

        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy)
        controls.addWidget(self.copy_button)

        self.save_button = QPushButton("Save...")
        self.save_button.clicked.connect(self._save)
        controls.addWidget(self.save_button)

        layout.addLayout(controls)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(20000)  # bounded (spec §93: throttle high-frequency logs)
        layout.addWidget(self.text_edit)

    # -- sources --------------------------------------------------------

    def append_log_record(self, level: str, message: str) -> None:
        self._all_lines.append((level, message))
        if self._passes_filter(level, message):
            self.text_edit.appendPlainText(message)

    def append_event(self, event: Event) -> None:
        text = self._format_event(event)
        level = "ERROR" if event.type in _ERROR_EVENT_TYPES else "INFO"
        self._all_lines.append((level, text))
        if self._passes_filter(level, text):
            self.text_edit.appendPlainText(text)

    @staticmethod
    def _format_event(event: Event) -> str:
        bits = [f"[{event.type.value}]"]
        if event.job_id:
            bits.append(f"job={event.job_id}")
        if event.part_id:
            bits.append(f"part={event.part_id}")
        extra = {k: v for k, v in event.data.items() if k not in ("job_id", "part_id", "engine")}
        if extra:
            bits.append(str(extra))
        return " ".join(bits)

    # -- filtering --------------------------------------------------------

    def _passes_filter(self, level: str, text: str) -> bool:
        checkbox = self._level_checks.get(level)
        if checkbox is not None and not checkbox.isChecked():
            return False
        query = self.search_edit.text().strip().lower()
        return not query or query in text.lower()

    def _apply_filter(self, *_args) -> None:
        self.text_edit.clear()
        for level, text in self._all_lines:
            if self._passes_filter(level, text):
                self.text_edit.appendPlainText(text)

    def _clear(self) -> None:
        self._all_lines.clear()
        self.text_edit.clear()

    def _copy(self) -> None:
        self.text_edit.selectAll()
        self.text_edit.copy()
        cursor = self.text_edit.textCursor()
        cursor.clearSelection()
        self.text_edit.setTextCursor(cursor)

    def _save(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "Save Log", "log.txt", "Text Files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text_edit.toPlainText())

    def jump_to_first_error(self) -> None:
        cursor = self.text_edit.document().find("ERROR")
        if not cursor.isNull():
            self.text_edit.setTextCursor(cursor)
