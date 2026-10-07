"""
Settings dialog (spec §24): the GUI-side equivalent of
`--configure-engine` -- lets a user point Flux.2 / Real-ESRGAN at real
executables and models without touching Python source. Reads/writes
through project_system.settings.EngineSettingsManager, so profiles
saved here are picked up by the CLI too (spec §16: GUI/CLI parity).

UNTESTED IN THIS ENVIRONMENT (see ui/canvas.py's note).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from engines.flux2.config import Flux2Profile
from engines.realesrgan.config import RealESRGANProfile
from project_system.settings import EngineSettingsManager


class _ProfileEditor(QWidget):
    """One tab: pick a saved profile by name, edit its fields, save/
    delete/set-default. Generic over Flux2Profile / RealESRGANProfile
    via each field's declared type."""

    def __init__(self, engine_type: str, profile_cls, manager: EngineSettingsManager, parent=None):
        super().__init__(parent)
        self.engine_type = engine_type
        self.profile_cls = profile_cls
        self.manager = manager
        self._field_widgets: dict = {}

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.currentTextChanged.connect(self._load_selected)
        row.addWidget(self.profile_combo)
        self.new_button = QPushButton("New Profile...")
        self.new_button.clicked.connect(self._new_profile)
        row.addWidget(self.new_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete_profile)
        row.addWidget(self.delete_button)
        layout.addLayout(row)

        self.form = QFormLayout()
        layout.addLayout(self.form)
        self._build_fields()

        button_row = QHBoxLayout()
        self.set_default_button = QPushButton("Set as Default")
        self.set_default_button.clicked.connect(self._set_default)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._save)
        button_row.addStretch(1)
        button_row.addWidget(self.set_default_button)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self._refresh_profile_list()

    def _build_fields(self) -> None:
        import dataclasses
        for f in dataclasses.fields(self.profile_cls):
            declared = str(f.type)
            label = f.name.replace("_", " ").title()
            if "float" in declared:
                widget = QDoubleSpinBox()
                widget.setRange(0.0, 100000.0)
                widget.setSpecialValueText("(unset)")
            elif "int" in declared and "str" not in declared:
                widget = QSpinBox()
                widget.setRange(0, 100000)
                widget.setSpecialValueText("(unset)")
            elif "executable" in f.name or "path" in f.name:
                widget = self._path_picker_widget()
            else:
                widget = QLineEdit()
            self._field_widgets[f.name] = widget
            self.form.addRow(label, widget if not isinstance(widget, QWidget) or True else widget)

    def _path_picker_widget(self) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        line_edit = QLineEdit()
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda: self._browse_into(line_edit))
        row.addWidget(line_edit)
        row.addWidget(browse)
        container.line_edit = line_edit  # type: ignore[attr-defined]
        return container

    def _browse_into(self, line_edit: QLineEdit) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Select File")
        if path:
            line_edit.setText(path)

    def _refresh_profile_list(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        names = list(self.manager.list_profiles(self.engine_type))
        self.profile_combo.addItems(names)
        default_name = self.manager.get_default_profile_name(self.engine_type)
        if default_name in names:
            self.profile_combo.setCurrentText(default_name)
        self.profile_combo.blockSignals(False)
        self._load_selected(self.profile_combo.currentText())

    def _current_profile_name(self) -> Optional[str]:
        text = self.profile_combo.currentText()
        return text or None

    def _load_selected(self, name: str) -> None:
        if not name:
            self._populate_fields(self.profile_cls())
            return
        data = self.manager.get_profile(self.engine_type, name)
        profile = self.profile_cls.from_dict(data) if data else self.profile_cls()
        self._populate_fields(profile)

    def _populate_fields(self, profile) -> None:
        for name, widget in self._field_widgets.items():
            value = getattr(profile, name, None)
            if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                widget.setValue(value if value is not None else 0)
            elif isinstance(widget, QLineEdit):
                widget.setText("" if value is None else str(value))
            elif hasattr(widget, "line_edit"):
                widget.line_edit.setText("" if value is None else str(value))

    def _collect_profile(self):
        profile = self.profile_cls()
        for name, widget in self._field_widgets.items():
            if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                value = widget.value()
                setattr(profile, name, value if value else None)
            elif isinstance(widget, QLineEdit):
                text = widget.text().strip()
                setattr(profile, name, text or None)
            elif hasattr(widget, "line_edit"):
                text = widget.line_edit.text().strip()
                setattr(profile, name, text or None)
        return profile

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if ok and name:
            self.manager.save_profile(self.engine_type, name, self.profile_cls().to_dict())
            self._refresh_profile_list()
            self.profile_combo.setCurrentText(name)

    def _delete_profile(self) -> None:
        name = self._current_profile_name()
        if not name:
            return
        confirm = QMessageBox.question(self, "Delete Profile", f"Delete profile '{name}'?")
        if confirm == QMessageBox.StandardButton.Yes:
            self.manager.delete_profile(self.engine_type, name)
            self._refresh_profile_list()

    def _save(self) -> None:
        name = self._current_profile_name()
        if not name:
            QMessageBox.warning(self, "Save Profile", "Create a profile first (New Profile...).")
            return
        profile = self._collect_profile()
        self.manager.save_profile(self.engine_type, name, profile.to_dict())
        QMessageBox.information(self, "Save Profile", f"Saved '{name}'.")

    def _set_default(self) -> None:
        name = self._current_profile_name()
        if name:
            self._save()
            self.manager.set_default(self.engine_type, name)
            QMessageBox.information(self, "Default Profile", f"'{name}' is now the default {self.engine_type} profile.")


class EngineSettingsDialog(QDialog):
    def __init__(self, manager: EngineSettingsManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Engine Settings  ({manager.path})")
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Profiles stored at: {manager.path}"))

        tabs = QTabWidget()
        tabs.addTab(_ProfileEditor("flux2", Flux2Profile, manager), "Flux.2")
        tabs.addTab(_ProfileEditor("realesrgan", RealESRGANProfile, manager), "Real-ESRGAN")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)
