"""
Part Inspector (spec §26): editable fields for the selected part.
Emits `changed` whenever the user edits a field; MainWindow decides
whether to apply to just this part or (spec §26/§89) to a broader
selection -- this widget only ever edits the one Part it was given.

Controls are enabled/disabled per the active engine's declared
EngineCapabilities (spec §4, §102) via `apply_capabilities()`, so an
engine that doesn't support e.g. guidance never shows a live control
for it.

UNTESTED IN THIS ENVIRONMENT (see ui/canvas.py's note).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QPlainTextEdit,
    QSpinBox, QVBoxLayout, QWidget,
)

from core.models.engine_models import EngineCapabilities
from core.models.part import Part


class PartInspector(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._part: Optional[Part] = None
        self._loading = False  # guards against feedback loops while populating fields

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("Part Inspector"))

        form = QFormLayout()
        outer.addLayout(form)

        self.id_label = QLabel("-")
        form.addRow("Part ID", self.id_label)

        self.material_combo = QComboBox()
        self.material_combo.setEditable(True)
        form.addRow("Material", self.material_combo)

        self.engine_combo = QComboBox()
        form.addRow("Engine", self.engine_combo)

        self.strength_spin = QDoubleSpinBox()
        self.strength_spin.setRange(0.0, 1.0)
        self.strength_spin.setSingleStep(0.05)
        form.addRow("Strength", self.strength_spin)

        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(0, 500)
        self.steps_spin.setSpecialValueText("(engine default)")
        form.addRow("Steps", self.steps_spin)

        self.guidance_spin = QDoubleSpinBox()
        self.guidance_spin.setRange(0.0, 50.0)
        self.guidance_spin.setSingleStep(0.5)
        form.addRow("Guidance", self.guidance_spin)

        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2_147_483_647)
        self.seed_spin.setSpecialValueText("(random)")
        form.addRow("Seed", self.seed_spin)

        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(0, 4096)
        form.addRow("Padding", self.padding_spin)

        self.feather_spin = QSpinBox()
        self.feather_spin.setRange(0, 256)
        form.addRow("Feather", self.feather_spin)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("Part-specific prompt fragment...")
        form.addRow("Prompt", self.prompt_edit)

        self.negative_prompt_edit = QPlainTextEdit()
        form.addRow("Negative prompt", self.negative_prompt_edit)

        self.status_label = QLabel("-")
        form.addRow("Status", self.status_label)

        for widget in (self.material_combo, self.engine_combo):
            widget.currentTextChanged.connect(self._on_field_changed)
        for widget in (self.strength_spin, self.steps_spin, self.guidance_spin,
                       self.seed_spin, self.padding_spin, self.feather_spin):
            widget.valueChanged.connect(self._on_field_changed)
        for widget in (self.prompt_edit, self.negative_prompt_edit):
            widget.textChanged.connect(self._on_field_changed)

        self.setEnabled(False)

    def set_engine_names(self, names: list) -> None:
        current = self.engine_combo.currentText()
        self.engine_combo.blockSignals(True)
        self.engine_combo.clear()
        self.engine_combo.addItems(names)
        if current in names:
            self.engine_combo.setCurrentText(current)
        self.engine_combo.blockSignals(False)

    def set_material_names(self, names: list) -> None:
        current = self.material_combo.currentText()
        self.material_combo.blockSignals(True)
        self.material_combo.clear()
        self.material_combo.addItems(names)
        self.material_combo.setCurrentText(current)
        self.material_combo.blockSignals(False)

    def load_part(self, part: Optional[Part]) -> None:
        self._part = part
        self._loading = True
        try:
            self.setEnabled(part is not None)
            if part is None:
                return
            self.id_label.setText(part.id)
            self.material_combo.setCurrentText(part.material or "")
            self.engine_combo.setCurrentText(part.engine or "")
            self.strength_spin.setValue(part.strength)
            self.steps_spin.setValue(part.steps or 0)
            self.guidance_spin.setValue(part.guidance or 0.0)
            self.seed_spin.setValue(part.seed or 0)
            self.padding_spin.setValue(part.padding)
            self.feather_spin.setValue(part.feather)
            self.prompt_edit.setPlainText(part.prompt)
            self.negative_prompt_edit.setPlainText(part.negative_prompt)
            self.status_label.setText(part.status.value)
        finally:
            self._loading = False

    def apply_capabilities(self, capabilities: EngineCapabilities) -> None:
        """spec §4/§102: disable controls this engine doesn't support."""
        self.prompt_edit.setEnabled(capabilities.supports_prompt)
        self.negative_prompt_edit.setEnabled(capabilities.supports_negative_prompt)
        self.strength_spin.setEnabled(capabilities.supports_strength)
        self.seed_spin.setEnabled(capabilities.supports_seed)
        self.steps_spin.setEnabled(capabilities.supports_steps)
        self.guidance_spin.setEnabled(capabilities.supports_guidance)

    def _on_field_changed(self, *_args) -> None:
        if self._loading or self._part is None:
            return
        part = self._part
        part.material = self.material_combo.currentText() or None
        part.engine = self.engine_combo.currentText() or None
        part.strength = self.strength_spin.value()
        part.steps = self.steps_spin.value() or None
        part.guidance = self.guidance_spin.value() or None
        part.seed = self.seed_spin.value() or None
        part.padding = self.padding_spin.value()
        part.feather = self.feather_spin.value()
        part.prompt = self.prompt_edit.toPlainText()
        part.negative_prompt = self.negative_prompt_edit.toPlainText()
        self.changed.emit()
