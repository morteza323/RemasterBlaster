"""
Project / Part list panel (spec §65's left column: PROJECT / QUEUE /
PART LIST). Lists every Part in the current project with its status;
selecting one emits part_selected so the canvas can highlight it and
the inspector can load it.

UNTESTED IN THIS ENVIRONMENT (see ui/canvas.py's note).
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from core.models.part import Part, PartStatus

_STATUS_LABELS = {
    PartStatus.PENDING: "pending",
    PartStatus.QUEUED: "queued",
    PartStatus.PROCESSING: "processing",
    PartStatus.COMPLETED: "completed",
    PartStatus.FAILED: "failed",
    PartStatus.SKIPPED: "skipped",
    PartStatus.USING_ORIGINAL: "using original",
}


class ProjectPanel(QWidget):
    part_selected = pyqtSignal(str)            # part id
    generate_queue_requested = pyqtSignal()
    part_context_action = pyqtSignal(str, str)  # part id, action name

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts: List[Part] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QHBoxLayout()
        header.addWidget(QLabel("Parts"))
        self.generate_button = QPushButton("Generate Queue")
        self.generate_button.clicked.connect(self.generate_queue_requested.emit)
        header.addWidget(self.generate_button)
        layout.addLayout(header)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget)

    def set_parts(self, parts: List[Part]) -> None:
        self._parts = parts
        self.list_widget.clear()
        for part in parts:
            label = f"{part.name}   [{_STATUS_LABELS.get(part.status, part.status.value)}]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, part.id)
            self.list_widget.addItem(item)

    def refresh(self) -> None:
        """Call after part statuses change (e.g. on JOB_COMPLETED) to
        update labels without losing the current selection."""
        selected_id = self.selected_part_id()
        self.set_parts(self._parts)
        if selected_id is not None:
            self.select_part(selected_id)

    def selected_part_id(self) -> Optional[str]:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def select_part(self, part_id: str) -> None:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == part_id:
                self.list_widget.setCurrentItem(item)
                return

    def _on_selection_changed(self, current: QListWidgetItem, _previous: QListWidgetItem) -> None:
        if current is not None:
            self.part_selected.emit(current.data(Qt.ItemDataRole.UserRole))
