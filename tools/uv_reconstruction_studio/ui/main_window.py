"""
MainWindow (spec §65-69): assembles every panel into the layout the
spec describes --

    MENU / TOOLBAR
    PROJECT+QUEUE+PART LIST | UV CANVAS | INSPECTOR/VERSIONS/PREVIEW/MASK (tabs)
    LOG / CONSOLE
    STATUS BAR

and wires them to ProjectManager (Phase 2), the UV/part generator
(Phase 5), QueueManager (Phase 6), the engine registry (Phases 7-10),
PresetManager (Phase 13), and reassembly/export (Phase 15) -- every
non-GUI subsystem this application has, in one place.

UNTESTED IN THIS ENVIRONMENT: PyQt6 is not installed and there is no
display available in this sandbox (see project README's "GUI" section
for what that means and how to verify it). Every other subsystem this
file wires together has a passing automated test suite; this file and
the rest of ui/ do not, and should be exercised by hand in a real
PyQt6 desktop environment before being relied on.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog, QInputDialog, QMainWindow, QMessageBox, QSplitter, QStatusBar, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from app.version import APP_NAME, APP_VERSION
from core.events.bus import Event, EventBus
from core.events.types import EventType
from core.models.engine_models import ReconstructionRequest
from core.models.guide import Guide
from core.models.job import Job, JobState
from core.models.part import Part, PartStatus
from core.models.project import Project, SourceTexture
from image.loader import load_image
from logging_system.manager import LoggingManager
from pipeline.queue_manager import QueueManager
from project_system.manager import ProjectManager
from prompts.presets import PresetManager
from reassembly.assembler import ReassemblyError, reassemble
from reassembly.export import export_texture
from ui.canvas import UVCanvasWidget
from ui.event_bridge import QtEventBridge
from ui.panels.log_console import LogConsolePanel, QtLogHandler
from ui.panels.mask_editor import MaskEditorPanel
from ui.panels.part_inspector import PartInspector
from ui.panels.preview_panel import PreviewPanel
from ui.panels.project_panel import ProjectPanel
from ui.panels.queue_panel import QueuePanel
from ui.panels.version_history import VersionHistoryPanel
from uv.parts_generator import PartGenerationError, generate_parts


def _default_request_builder(working_dir: Path):
    def _build(part: Part, job: Job) -> ReconstructionRequest:
        input_path = working_dir / "parts" / part.id / "source" / "crop.png"
        version_index = len(part.history) + 1
        output_path = working_dir / "parts" / part.id / "output" / f"{job.engine}_v{version_index:03d}.png"
        return ReconstructionRequest(
            job_id=job.id, part_id=part.id,
            input_path=str(input_path), output_path=str(output_path),
            prompt=part.prompt, negative_prompt=part.negative_prompt,
            strength=part.strength, steps=part.steps, guidance=part.guidance,
            seed=part.seed, mask_path=part.mask_path,
        )
    return _build


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1400, 900)

        # -- core, non-GUI subsystems --------------------------------------
        self.event_bus = EventBus()
        self.project_manager = ProjectManager()
        self.preset_manager = PresetManager()
        self.logging_manager = LoggingManager()
        self.logging_manager.setup_root()

        # spec §16 (GUI/CLI parity): the exact same registry-building
        # code the CLI uses, so a profile saved via --configure-engine
        # or this window's Settings dialog behaves identically either way.
        from cli.commands import build_full_registry
        self.registry = build_full_registry(event_bus=self.event_bus)

        self.queue: Optional[QueueManager] = None
        self.source_image = None  # PIL.Image, loaded with the project

        # -- Qt/event plumbing -----------------------------------------------
        self.event_bridge = QtEventBridge(self.event_bus)
        self.event_bridge.event_received.connect(self._on_event)

        self.log_handler = QtLogHandler()
        self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger("uvrs").addHandler(self.log_handler)

        self._build_ui()
        self.log_handler.emitter.record_emitted.connect(self.log_console.append_log_record)

        self.statusBar().showMessage("Ready.")

    # -- construction --------------------------------------------------------

    def _reload_engine_registry(self) -> None:
        """Rebuilds the engine registry from saved settings -- called
        after opening/creating a project (so project-scoped profiles,
        spec §88, take priority over global ones) and after the
        Settings dialog closes (so edits apply immediately)."""
        from cli.commands import build_full_registry
        working_dir = self.project_manager.working_dir
        self.registry = build_full_registry(event_bus=self.event_bus, working_dir=working_dir)
        self.part_inspector.set_engine_names(self.registry.list_engines())

    def _build_ui(self) -> None:
        self._build_menu_and_toolbar()

        self.canvas = UVCanvasWidget()
        self.canvas.coordinates_changed.connect(self._on_coordinates_changed)
        self.canvas.guide_moved.connect(self._on_guide_moved)

        self.project_panel = ProjectPanel()
        self.project_panel.part_selected.connect(self._on_part_selected)
        self.project_panel.generate_queue_requested.connect(self._on_generate_queue)

        self.queue_panel = QueuePanel()
        self.queue_panel.start_all_requested.connect(self._on_start_all)
        self.queue_panel.pause_requested.connect(self._on_pause)
        self.queue_panel.resume_requested.connect(self._on_resume)
        self.queue_panel.retry_failed_requested.connect(self._on_retry_failed)
        self.queue_panel.cancel_selected_requested.connect(self._on_cancel_selected)
        self.queue_panel.skip_selected_requested.connect(self._on_skip_selected)

        left_column = QWidget()
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.project_panel, stretch=2)
        left_layout.addWidget(self.queue_panel, stretch=1)

        self.part_inspector = PartInspector()
        self.part_inspector.changed.connect(self._on_part_edited)
        self.part_inspector.set_engine_names(self.registry.list_engines())
        self.part_inspector.set_material_names(self.preset_manager.list_materials())

        self.version_history = VersionHistoryPanel()
        self.version_history.compare_requested.connect(self._on_compare_versions)
        self.version_history.use_as_final_requested.connect(lambda _vid: self.project_panel.refresh())
        self.version_history.use_original_requested.connect(self.project_panel.refresh)

        self.preview_panel = PreviewPanel()
        self.mask_editor = MaskEditorPanel()

        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self.part_inspector, "Inspector")
        self.right_tabs.addTab(self.version_history, "Versions")
        self.right_tabs.addTab(self.preview_panel, "Preview")
        self._mask_tab_index = self.right_tabs.addTab(self.mask_editor, "Mask")

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(left_column)
        main_splitter.addWidget(self.canvas)
        main_splitter.addWidget(self.right_tabs)
        main_splitter.setSizes([280, 800, 320])

        self.log_console = LogConsolePanel()

        vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        vertical_splitter.addWidget(main_splitter)
        vertical_splitter.addWidget(self.log_console)
        vertical_splitter.setSizes([700, 200])

        self.setCentralWidget(vertical_splitter)
        self.setStatusBar(QStatusBar())

    def _build_menu_and_toolbar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        file_menu.addAction("New Project...", self._on_new_project)
        file_menu.addAction("Open Project...", self._on_open_project)
        file_menu.addAction("Save", self._on_save_project)
        file_menu.addAction("Save As...", self._on_save_project_as)
        file_menu.addSeparator()
        file_menu.addAction("Export Final Texture...", self._on_export_final)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        queue_menu = menu_bar.addMenu("&Queue")
        queue_menu.addAction("Generate Queue", self._on_generate_queue)
        queue_menu.addAction("Start All", self._on_start_all)
        queue_menu.addAction("Pause", self._on_pause)
        queue_menu.addAction("Resume", self._on_resume)
        queue_menu.addAction("Retry Failed", self._on_retry_failed)

        settings_menu = menu_bar.addMenu("&Settings")
        settings_menu.addAction("Engine Settings...", self._on_open_settings)

        help_menu = menu_bar.addMenu("&Help")
        help_menu.addAction("About", self._on_about)

        toolbar = QToolBar("Main")
        toolbar.addAction("Open", self._on_open_project)
        toolbar.addAction("Save", self._on_save_project)
        toolbar.addSeparator()
        toolbar.addAction("Generate Queue", self._on_generate_queue)
        toolbar.addAction("Start All", self._on_start_all)
        toolbar.addSeparator()
        toolbar.addAction("Fit", lambda: self.canvas.fit_to_screen())
        toolbar.addAction("100%", lambda: self.canvas.view_100_percent())
        self.addToolBar(toolbar)

    # -- project lifecycle -----------------------------------------------

    def _on_new_project(self) -> None:
        source_path, _filter = QFileDialog.getOpenFileName(self, "Select Source Texture", "", "Images (*.png *.jpg *.jpeg)")
        if not source_path:
            return
        working_dir_str = QFileDialog.getExistingDirectory(self, "Choose an empty folder for the new project")
        if not working_dir_str:
            return
        name, ok = QInputDialog.getText(self, "Project Name", "Name:")
        if not ok:
            name = "Untitled Project"

        image = load_image(source_path)
        project = Project(name=name, source=SourceTexture(
            filename=Path(source_path).name, width=image.width, height=image.height, format=image.format or "PNG",
        ))
        working_dir = Path(working_dir_str)
        self.project_manager.create(project, working_dir)

        texture_dest = working_dir / "texture" / Path(source_path).name
        texture_dest.parent.mkdir(parents=True, exist_ok=True)
        texture_dest.write_bytes(Path(source_path).read_bytes())

        self.source_image = image
        self._refresh_from_project()
        self.canvas.load_image(str(texture_dest))
        self.canvas.set_guides(project.guides)
        self.statusBar().showMessage(f"Created project '{name}'.")

    def _on_open_project(self) -> None:
        path_str = QFileDialog.getExistingDirectory(self, "Open Project Folder")
        if not path_str:
            path_str, _filter = QFileDialog.getOpenFileName(self, "Open Project File", "", "UV Reconstruction Studio (*.uvrs)")
            if not path_str:
                return
        path = Path(path_str)
        try:
            project = self.project_manager.load(path, self.project_manager.working_dir or path)
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.critical(self, "Open Project", f"Could not open project:\n{exc}")
            return

        texture_path = self.project_manager.working_dir / "texture" / project.source.filename
        self.source_image = load_image(texture_path) if texture_path.exists() else None
        self._refresh_from_project()
        if texture_path.exists():
            self.canvas.load_image(str(texture_path))
            self.canvas.set_guides(project.guides)
        self.statusBar().showMessage(f"Opened '{project.name}'.")

    def _on_save_project(self) -> None:
        if self.project_manager.save_path is None:
            self._on_save_project_as()
            return
        self.project_manager.save()
        self.statusBar().showMessage("Project saved.")

    def _on_save_project_as(self) -> None:
        if self.project_manager.working_dir is None:
            return
        path_str, _filter = QFileDialog.getSaveFileName(self, "Save Project As", "", "UV Reconstruction Studio (*.uvrs)")
        if not path_str:
            return
        self.project_manager.save_as(Path(path_str))
        self.statusBar().showMessage(f"Saved to {path_str}.")

    def _on_export_final(self) -> None:
        project = self.project_manager.project
        if project is None or self.source_image is None:
            return
        try:
            final_image = reassemble(self.source_image, project.parts, event_bus=self.event_bus)
        except ReassemblyError as exc:
            QMessageBox.warning(self, "Export", f"Not ready to export yet:\n{exc}")
            return
        output_str, _filter = QFileDialog.getSaveFileName(self, "Export Final Texture", "final.png", "PNG (*.png)")
        if output_str:
            export_texture(final_image, output_str)
            self.statusBar().showMessage(f"Exported to {output_str}.")

    def _on_about(self) -> None:
        QMessageBox.information(self, "About", f"{APP_NAME} v{APP_VERSION}")

    def _on_open_settings(self) -> None:
        from project_system.settings import global_settings_manager, project_settings_manager
        from ui.panels.settings_dialog import EngineSettingsDialog

        if self.project_manager.working_dir is not None:
            manager = project_settings_manager(self.project_manager.working_dir)
        else:
            manager = global_settings_manager()

        dialog = EngineSettingsDialog(manager, parent=self)
        dialog.exec()
        self._reload_engine_registry()  # pick up whatever was just saved, immediately

    # -- queue / parts -----------------------------------------------------

    def _refresh_from_project(self) -> None:
        project = self.project_manager.project
        if project is None:
            return
        self._reload_engine_registry()
        self.project_panel.set_parts(project.parts)
        self.queue = QueueManager(self.registry, _default_request_builder(self.project_manager.working_dir),
                                   event_bus=self.event_bus)
        for part in project.parts:
            if part.status == PartStatus.PENDING:
                self.queue.add(part, part.engine or "mock", project_id=project.id)
        self._refresh_queue_panel()

    def _refresh_queue_panel(self) -> None:
        if self.queue is None:
            return
        project = self.project_manager.project
        names = {p.id: p.name for p in project.parts} if project else {}
        self.queue_panel.set_jobs(self.queue.get_jobs(), names)

    def _on_generate_queue(self) -> None:
        project = self.project_manager.project
        if project is None or self.source_image is None:
            QMessageBox.warning(self, "Generate Queue", "Open or create a project with guides first.")
            return
        try:
            new_parts = generate_parts(project, self.source_image, self.project_manager.working_dir)
        except PartGenerationError as exc:
            QMessageBox.warning(self, "Generate Queue", str(exc))
            return
        for part in new_parts:
            self.queue.add(part, part.engine or "mock", project_id=project.id)
        self.project_panel.set_parts(project.parts)
        self._refresh_queue_panel()
        self.statusBar().showMessage(f"Generated {len(new_parts)} parts.")

    def _on_start_all(self) -> None:
        if self.queue is not None:
            self.queue.start_all()

    def _on_pause(self) -> None:
        if self.queue is not None:
            self.queue.pause()

    def _on_resume(self) -> None:
        if self.queue is not None:
            self.queue.resume()

    def _on_retry_failed(self) -> None:
        if self.queue is not None:
            self.queue.retry_failed()

    def _on_cancel_selected(self, job_ids: list) -> None:
        if self.queue is not None:
            self.queue.cancel_selected(job_ids)

    def _on_skip_selected(self, job_ids: list) -> None:
        if self.queue is None:
            return
        for job_id in job_ids:
            self.queue.skip(job_id)
        self._refresh_queue_panel()

    # -- selection / inspector -------------------------------------------

    def _current_part(self) -> Optional[Part]:
        project = self.project_manager.project
        part_id = self.project_panel.selected_part_id()
        if project is None or part_id is None:
            return None
        return project.get_part(part_id)

    def _on_part_selected(self, part_id: str) -> None:
        project = self.project_manager.project
        if project is None:
            return
        part = project.get_part(part_id)
        if part is None:
            return
        self.part_inspector.load_part(part)
        self.version_history.load_part(part)
        self.canvas.highlight_region(part.bounds)

        engine = self.registry.list_engines() and (part.engine or self.registry.get_default())
        if engine and engine in self.registry.list_engines():
            capabilities = self.registry.create(engine).get_capabilities()
            self.part_inspector.apply_capabilities(capabilities)
            # spec §20: never leave masking available-but-ignored --
            # disable the whole tab and say why, rather than silently
            # discarding a mask the active engine can't use.
            self.right_tabs.setTabEnabled(self._mask_tab_index, capabilities.supports_mask)
            self.right_tabs.setTabToolTip(
                self._mask_tab_index,
                "" if capabilities.supports_mask
                else f"'{engine}' does not support masking (see its declared capabilities)."
            )

        crop_path = self.project_manager.working_dir / "parts" / part.id / "source" / "crop.png"
        if crop_path.exists():
            crop_image = load_image(crop_path)
            self.mask_editor.load(crop_image)
            version = part.selected_version()
            if version is not None and Path(version.output_path).exists():
                self.preview_panel.set_images(crop_image, load_image(version.output_path))

    def _on_part_edited(self) -> None:
        self.project_panel.refresh()

    def _on_guide_moved(self, guide_id: str, new_position: int) -> None:
        project = self.project_manager.project
        if project is None:
            return
        for guide in project.guides:
            if guide.id == guide_id:
                guide.position = new_position
                break

    def _on_compare_versions(self, version_id_a: str, version_id_b: str) -> None:
        part = self._current_part()
        if part is None:
            return
        version_a = next((v for v in part.history if v.id == version_id_a), None)
        version_b = next((v for v in part.history if v.id == version_id_b), None)
        if version_a and version_b and Path(version_a.output_path).exists() and Path(version_b.output_path).exists():
            self.preview_panel.set_images(load_image(version_a.output_path), load_image(version_b.output_path))

    def _on_coordinates_changed(self, x: int, y: int) -> None:
        self.statusBar().showMessage(f"X: {x}   Y: {y}")

    # -- live events (spec §6-7) -------------------------------------------

    def _on_event(self, event: Event) -> None:
        self.log_console.append_event(event)

        if event.type in (EventType.JOB_STARTED, EventType.JOB_PROGRESS, EventType.JOB_COMPLETED,
                          EventType.JOB_FAILED, EventType.JOB_CANCELLED):
            self._refresh_queue_panel()

        if event.type in (EventType.JOB_COMPLETED, EventType.JOB_FAILED):
            self.project_panel.refresh()
            current = self._current_part()
            if current is not None and current.id == event.part_id:
                self.version_history.load_part(current)

        if event.type == EventType.JOB_FAILED:
            self._show_job_failure(event)

        if event.type == EventType.JOB_PROGRESS:
            progress = event.data.get("progress")
            if progress is not None:
                self.statusBar().showMessage(f"Processing {event.part_id}: {progress * 100:.0f}%")

    def _show_job_failure(self, event: Event) -> None:
        """spec §28: a structured, actionable failure report -- same
        formatter the CLI uses (spec §16) -- not a bare 'Process
        failed' or a raw traceback."""
        from core.error_formatting import format_job_failure

        job = next((j for j in (self.queue.get_jobs() if self.queue else []) if j.id == event.job_id), None)
        if job is None:
            return
        project = self.project_manager.project
        part = project.get_part(job.part_id) if project else None
        report = format_job_failure(job, part, engine_display_name=job.engine)
        self.log_console.append_log_record("ERROR", report)
        QMessageBox.warning(self, "Reconstruction Failed", report)
