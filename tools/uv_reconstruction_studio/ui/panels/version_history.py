"""
Version History panel (spec §40): lists a part's PartVersion history.
Selecting exactly two enables Compare (routes into PreviewPanel);
selecting one enables "Use as Final" (changes the part's
selected_version_id without creating a new version -- spec §40 says
the user can compare any two versions and pick one, not that picking
one re-runs anything).

UNTESTED IN THIS ENVIRONMENT (see ui/canvas.py's note).
"""

from __future__ import annotations

import time
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from core.models.part import Part, PartStatus


class VersionHistoryPanel(QWidget):
    version_selected = pyqtSignal(str)        # version id
    compare_requested = pyqtSignal(str, str)  # version id A, version id B
    use_as_final_requested = pyqtSignal(str)  # version id
    use_original_requested = pyqtSignal()     # spec §19: restore the original, unreconstructed texture region

    def __init__(self, parent=None):
        super().__init__(parent)
        self._part: Optional[Part] = None

        layout = QVBoxLayout(self)
        self.status_label = QLabel("No part selected.")
        layout.addWidget(self.status_label)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget)

        buttons = QHBoxLayout()
        self.compare_button = QPushButton("Compare Selected")
        self.compare_button.setEnabled(False)
        self.compare_button.clicked.connect(self._emit_compare)
        self.use_button = QPushButton("Use as Final")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._emit_use_as_final)
        self.use_original_button = QPushButton("Use Original")
        self.use_original_button.clicked.connect(self._emit_use_original)
        buttons.addWidget(self.compare_button)
        buttons.addWidget(self.use_button)
        buttons.addWidget(self.use_original_button)
        layout.addLayout(buttons)

    def load_part(self, part: Optional[Part]) -> None:
        self._part = part
        self.list_widget.clear()
        if part is None:
            self.status_label.setText("No part selected.")
            return
        if part.status == PartStatus.USING_ORIGINAL:
            self.status_label.setText("Currently using: Original (unreconstructed) texture")
        elif part.selected_version() is not None:
            v = part.selected_version()
            self.status_label.setText(f"Currently using: {v.engine} version from "
                                       f"{time.strftime('%H:%M:%S', time.localtime(v.timestamp))}")
        else:
            self.status_label.setText("No version selected yet.")
        for version in part.history:
            timestamp = time.strftime("%H:%M:%S", time.localtime(version.timestamp))
            label = f"{timestamp}  {version.engine}"
            if version.seed is not None:
                label += f"  seed={version.seed}"
            if version.id == part.selected_version_id:
                label += "  [selected]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, version.id)
            self.list_widget.addItem(item)

    def _selected_version_ids(self) -> List[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.list_widget.selectedItems()]

    def _on_selection_changed(self) -> None:
        ids = self._selected_version_ids()
        self.compare_button.setEnabled(len(ids) == 2)
        self.use_button.setEnabled(len(ids) == 1)
        if len(ids) == 1:
            self.version_selected.emit(ids[0])

    def _emit_compare(self) -> None:
        ids = self._selected_version_ids()
        if len(ids) == 2:
            self.compare_requested.emit(ids[0], ids[1])

    def _emit_use_as_final(self) -> None:
        ids = self._selected_version_ids()
        if len(ids) == 1 and self._part is not None:
            self._part.selected_version_id = ids[0]
            # Bug fix: picking a version must clear a prior "use
            # original"/"skipped" status -- otherwise reassembly keeps
            # skipping this part (it checks status, not whether a
            # version is selected) even though the user just chose one.
            self._part.status = PartStatus.COMPLETED
            self.use_as_final_requested.emit(ids[0])
            self.load_part(self._part)

    def _emit_use_original(self) -> None:
        if self._part is not None:
            self._part.status = PartStatus.USING_ORIGINAL
            self.use_original_requested.emit()
            self.load_part(self._part)
