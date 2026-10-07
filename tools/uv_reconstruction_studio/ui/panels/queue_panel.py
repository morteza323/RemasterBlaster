"""
Queue panel (spec §29): the job queue table plus its controls (start
all, pause, resume, cancel, retry failed, skip...). This widget only
renders Job state and forwards button clicks -- MainWindow wires the
signals to the actual QueueManager, keeping this panel free of
threading concerns.

UNTESTED IN THIS ENVIRONMENT (see ui/canvas.py's note).
"""

from __future__ import annotations

from typing import List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from core.models.job import Job

_COLUMNS = ["Part", "Engine", "Status", "Progress"]


class QueuePanel(QWidget):
    start_all_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    cancel_selected_requested = pyqtSignal(list)   # job ids
    retry_failed_requested = pyqtSignal()
    retry_selected_requested = pyqtSignal(list)
    skip_selected_requested = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        buttons = QHBoxLayout()
        self.start_all_button = QPushButton("Start All")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.retry_failed_button = QPushButton("Retry Failed")
        self.cancel_button = QPushButton("Cancel Selected")
        self.skip_button = QPushButton("Skip Selected")
        for button in (self.start_all_button, self.pause_button, self.resume_button,
                       self.retry_failed_button, self.cancel_button, self.skip_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.start_all_button.clicked.connect(self.start_all_requested.emit)
        self.pause_button.clicked.connect(self.pause_requested.emit)
        self.resume_button.clicked.connect(self.resume_requested.emit)
        self.retry_failed_button.clicked.connect(self.retry_failed_requested.emit)
        self.cancel_button.clicked.connect(lambda: self.cancel_selected_requested.emit(self.selected_job_ids()))
        self.skip_button.clicked.connect(lambda: self.skip_selected_requested.emit(self.selected_job_ids()))

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self._job_ids: List[str] = []

    def set_jobs(self, jobs: List[Job], part_names: dict) -> None:
        self._job_ids = [job.id for job in jobs]
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            part_label = part_names.get(job.part_id, job.part_id)
            progress_text = f"{job.progress * 100:.0f}%" if job.progress else ""
            for col, text in enumerate([part_label, job.engine, job.state.value, progress_text]):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.ItemDataRole.UserRole, job.id)
                self.table.setItem(row, col, item)

    def selected_job_ids(self) -> List[str]:
        rows = {index.row() for index in self.table.selectedIndexes()}
        ids = []
        for row in rows:
            item = self.table.item(row, 0)
            if item is not None:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids
