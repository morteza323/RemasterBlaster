"""
CLI commands (spec §52-55).

This is intentionally the *only* place that prints to stdout for a
user-facing narrative. Everything it prints is driven by EventBus
events -- the same events a future GUI panel would subscribe to
(spec §6: GUI and CLI must always stay in sync) -- so this module is
a reference implementation of "how to render the event stream",
not a parallel source of truth.
"""

from __future__ import annotations

import json as json_module
import platform
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from app.version import APP_NAME, APP_VERSION
from core.events.bus import Event, EventBus
from core.events.types import EventType
from core.error_formatting import format_job_failure
from core.models.engine_models import ReconstructionRequest
from core.models.guide import Guide, GuideOrientation
from core.models.job import Job, JobState
from core.models.part import Bounds, Part, PartStatus, PartVersion
from core.models.project import Project, SourceTexture
from diagnostics.engines import run_engine_diagnostics
from diagnostics.report import export_diagnostic_report
from diagnostics.system import collect_system_info
from engines.mock.mock_engine import MockEngine
from engines.registry import EngineRegistry
from image.loader import load_image
from image.validation import validate_input_image
from logging_system.manager import LoggingManager
from pipeline.queue_manager import QueueManager
from project_system.manager import ProjectManager
from project_system.settings import global_settings_manager, project_settings_manager
from project_system.storage import storage_for_path
from reassembly.assembler import ReassemblyError, reassemble
from reassembly.export import export_texture
from uv.parts_generator import PartGenerationError, generate_parts


def _banner() -> str:
    title = f"{APP_NAME}  v{APP_VERSION}"
    width = max(len(title) + 4, 50)
    top = "╔" + "═" * width + "╗"
    bottom = "╚" + "═" * width + "╝"
    middle = "║" + title.center(width) + "║"
    return "\n".join([top, middle, bottom])


def _cli_event_logger(event: Event) -> None:
    """Prints every event to the terminal in real time (spec §6, §53)."""
    ts = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
    ms = int((event.timestamp % 1) * 1000)
    tag_bits = [f"[{ts}.{ms:03d}]"]
    if event.job_id:
        tag_bits.append(f"[JOB {event.job_id}]")
    if event.part_id:
        tag_bits.append(f"[PART {event.part_id}]")
    if event.engine:
        tag_bits.append(f"[ENGINE {event.engine}]")
    prefix = " ".join(tag_bits)

    if event.type == EventType.JOB_PROGRESS:
        step = event.data.get("step")
        total = event.data.get("total_steps")
        progress = event.data.get("progress", 0.0)
        print(f"{prefix} Step {step}/{total}  Progress: {progress * 100:.0f}%")
    elif event.type == EventType.ENGINE_STDOUT:
        print(f"{prefix} {event.data.get('line', '')}")
    elif event.type == EventType.ENGINE_STDERR:
        print(f"{prefix} STDERR: {event.data.get('line', '')}", file=sys.stderr)
    else:
        print(f"{prefix} {event.type.value}")


def _json_event_logger(event: Event) -> None:
    """spec §55: machine-readable JSON Lines event stream for future
    external integrations. One compact JSON object per line."""
    payload = {"event": event.type.value, "timestamp": event.timestamp, **event.data}
    print(json_module.dumps(payload, default=str))


def run_diagnostics(registry: EngineRegistry) -> int:
    """spec §49-51: a diagnostic pass that never assumes a GPU/CUDA
    backend exists (spec §101) -- it only reports what it can detect."""
    print(_banner())
    print()
    print("DIAGNOSTICS")
    print("-" * 40)

    system_info = collect_system_info()
    print(f"Python        : {platform.python_version()}")
    print(f"Platform      : {system_info.platform}")
    if system_info.cpu_count is not None:
        print(f"CPU cores     : {system_info.cpu_count}")
    if system_info.total_ram_mb is not None:
        print(f"RAM           : {system_info.available_ram_mb:.0f} MB free / {system_info.total_ram_mb:.0f} MB total")
    print(f"GPU           : {', '.join(system_info.gpu_names) if system_info.gpu_names else '(none detected)'}")
    print("Dependencies  : " + ", ".join(f"{k}={v}" for k, v in system_info.dependency_versions.items()))
    print(f"Engines       : {', '.join(registry.list_engines()) or '(none registered)'}")
    print()

    all_ok = True
    for diag in run_engine_diagnostics(registry):
        status = "READY" if diag.is_valid else "FAILED"
        print(f"[{diag.name}] {status}")
        for msg in diag.messages:
            print(f"    ok: {msg}")
        for err in diag.errors:
            print(f"    error: {err}")
        all_ok = all_ok and diag.is_valid
    print()
    print("ENGINE READY" if all_ok else "ONE OR MORE ENGINES FAILED VALIDATION")
    return 0 if all_ok else 1


def run_export_diagnostic_report(zip_path: str, registry: EngineRegistry, logs_dir: str = "logs") -> int:
    engine_configs = {}
    for name in registry.list_engines():
        config = getattr(registry.create(name), "config", None)  # MockEngine has none -- fine, skipped
        if config is not None:
            engine_configs[name] = config
    output_path = export_diagnostic_report(zip_path, logs_dir, registry, engine_configs=engine_configs)
    print(f"Diagnostic report written to {output_path}")
    return 0


def _build_demo_project(tmp_dir: Path) -> Project:
    """Builds a tiny synthetic project: a 512x512 'texture' split into
    a 2x2 grid by one horizontal + one vertical guide. This stands in
    for Phase 4/5's real UV canvas + region generator, which operate
    on an actual imported image."""
    source_path = tmp_dir / "source.png"
    source_path.write_bytes(b"MOCK_TEXTURE_BYTES")

    project = Project(
        name="Demo Project",
        source=SourceTexture(filename=source_path.name, width=512, height=512, format="PNG"),
    )
    project.add_guide(Guide(orientation=GuideOrientation.HORIZONTAL, position=256))
    project.add_guide(Guide(orientation=GuideOrientation.VERTICAL, position=256))

    grid = [
        ("part_top_left", Bounds(x=0, y=0, width=256, height=256)),
        ("part_top_right", Bounds(x=256, y=0, width=256, height=256)),
        ("part_bottom_left", Bounds(x=0, y=256, width=256, height=256)),
        ("part_bottom_right", Bounds(x=256, y=256, width=256, height=256)),
    ]
    for name, bounds in grid:
        part = Part(source_texture=source_path.name, bounds=bounds, name=name, engine="mock")
        project.add_part(part)

    project.engines["source_path"] = str(source_path)
    return project


def run_demo(simulate_failure_on: str = "part_bottom_right") -> int:
    """Exercises the full Phase 1 stack end to end: project + guides +
    parts -> job state machine -> mock engine -> events -> hierarchical
    logs -> retry of a failed part. No real AI model involved."""
    print(_banner())
    print()

    logging_manager = LoggingManager()
    logging_manager.setup_root()
    queue_logger = logging_manager.get_component_logger("queue")

    bus = EventBus()
    bus.subscribe_all(_cli_event_logger)

    registry = EngineRegistry()
    registry.register("mock", lambda **cfg: MockEngine(event_bus=bus, total_steps=4, step_delay=0.0))
    engine = registry.create("mock")

    with tempfile.TemporaryDirectory(prefix="uvrs_demo_") as tmp:
        tmp_dir = Path(tmp)
        project = _build_demo_project(tmp_dir)
        source_path = tmp_dir / project.source.filename

        print(f"Project: {project.name}")
        print(f"Texture: {project.source.filename}")
        print(f"Resolution: {project.source.width}x{project.source.height}")
        print(f"Queue: {len(project.parts)} parts")
        print()

        results = []
        for idx, part in enumerate(project.parts, start=1):
            job = Job(part_id=part.id, project_id=project.id, engine=engine.name)
            part_logger = logging_manager.get_part_logger(part.id)
            job_logger = logging_manager.get_job_logger(job.id)

            print(f"[{idx:02d}/{len(project.parts)}] {part.name}")
            queue_logger.info("Starting job", extra={
                "project_id": project.id, "job_id": job.id, "part_id": part.id, "engine": engine.name,
            })

            job.transition(JobState.VALIDATING)
            job.transition(JobState.QUEUED)
            job.transition(JobState.PREPARING)
            part.status = PartStatus.PROCESSING
            job.transition(JobState.RUNNING)
            bus.publish(EventType.JOB_STARTED, job_id=job.id, part_id=part.id, engine=engine.name)

            output_path = tmp_dir / "parts" / part.id / "output" / "mock_v001.png"
            request = ReconstructionRequest(
                job_id=job.id,
                part_id=part.id,
                input_path=str(source_path),
                output_path=str(output_path),
                prompt=part.prompt,
                strength=part.strength,
                extra={"simulate_failure": part.name == simulate_failure_on},
            )
            result = engine.reconstruct(request)

            if result.success:
                job.transition(JobState.POST_PROCESSING)
                job.transition(JobState.VALIDATING_OUTPUT)
                job.transition(JobState.COMPLETED)
                version = PartVersion(
                    engine=engine.name,
                    output_path=result.output_path,
                    seed=result.actual_seed,
                    command=result.command,
                    duration_seconds=result.duration_seconds,
                )
                part.add_version(version)
                part.status = PartStatus.COMPLETED
                job_logger.info("Job completed", extra={
                    "project_id": project.id, "job_id": job.id, "part_id": part.id, "engine": engine.name,
                })
                part_logger.info("Version added", extra={
                    "project_id": project.id, "job_id": job.id, "part_id": part.id, "engine": engine.name,
                })
                print(f"    Completed in {result.duration_seconds:.2f}s\n")
            else:
                job.transition(JobState.FAILED)
                part.status = PartStatus.FAILED
                job_logger.error(f"Job failed: {result.error.message if result.error else 'unknown'}", extra={
                    "project_id": project.id, "job_id": job.id, "part_id": part.id, "engine": engine.name,
                })
                print(f"    FAILED: {result.error.message if result.error else 'unknown error'}\n")

            results.append((part, job, result))

        # Retry any failed part once (spec §72: retry with same settings).
        for part, job, result in results:
            if job.state != JobState.FAILED:
                continue
            print(f"Retrying {part.name} (retry same settings, without simulated failure)...")
            job.transition(JobState.RETRYING)
            job.transition(JobState.QUEUED)
            job.transition(JobState.PREPARING)
            job.transition(JobState.RUNNING)
            output_path = tmp_dir / "parts" / part.id / "output" / "mock_v002.png"
            request = ReconstructionRequest(
                job_id=job.id, part_id=part.id,
                input_path=str(source_path), output_path=str(output_path),
            )
            result = engine.reconstruct(request)
            if result.success:
                job.transition(JobState.POST_PROCESSING)
                job.transition(JobState.VALIDATING_OUTPUT)
                job.transition(JobState.COMPLETED)
                part.add_version(PartVersion(engine=engine.name, output_path=result.output_path,
                                              seed=result.actual_seed, duration_seconds=result.duration_seconds))
                part.status = PartStatus.COMPLETED
                print(f"    Retry succeeded in {result.duration_seconds:.2f}s\n")

        print("QUEUE SUMMARY")
        print("-" * 40)
        for part, job, _ in results:
            print(f"{part.name:<18} {job.engine:<6} versions={len(part.history):<2} status={part.status.value}")

        all_completed = all(p.status == PartStatus.COMPLETED for p, _, _ in results)
        print()
        print("Demo complete. Logs written under ./logs/")
        return 0 if all_completed else 1


# ---------------------------------------------------------------------------
# Full project workflow commands (spec §52, phase-plan Phase 17)
# ---------------------------------------------------------------------------

def build_full_registry(event_bus: Optional[EventBus] = None, working_dir: Optional[Path] = None) -> EngineRegistry:
    """Registers every known engine type -- mock always works; flux2
    and realesrgan are registered too (using any saved profile), so
    --diagnostics / --validate-engine / --export-diagnostic-report
    report their REAL status (e.g. "executable not found") instead of
    pretending those engines don't exist just because they aren't the
    one engine name the user happened to pass with --engine."""
    bus = event_bus or EventBus()
    registry = EngineRegistry()
    registry.register("mock", lambda **cfg: MockEngine(event_bus=bus, total_steps=4, step_delay=0.0), set_default=True)

    from engines.flux2.config import Flux2Profile
    from engines.flux2.flux2_engine import Flux2Engine
    flux2_dict = _load_engine_profile_dict("flux2", working_dir)
    flux2_profile = Flux2Profile.from_dict(flux2_dict) if flux2_dict else None
    registry.register("flux2", lambda **cfg: Flux2Engine(profile=flux2_profile, event_bus=bus))

    from engines.realesrgan.config import RealESRGANProfile
    from engines.realesrgan.realesrgan_engine import RealESRGANEngine
    esrgan_dict = _load_engine_profile_dict("realesrgan", working_dir)
    esrgan_profile = RealESRGANProfile.from_dict(esrgan_dict) if esrgan_dict else None
    registry.register("realesrgan", lambda **cfg: RealESRGANEngine(profile=esrgan_profile, event_bus=bus))

    return registry


def _load_engine_profile_dict(engine_type: str, working_dir: Optional[Path]) -> Optional[dict]:
    """Project-level saved profile takes priority over the global one;
    returns None if neither is configured (caller then uses the
    engine's built-in dataclass defaults)."""
    if working_dir is not None:
        project_dict = project_settings_manager(working_dir).get_default_profile(engine_type)
        if project_dict is not None:
            return project_dict
    return global_settings_manager().get_default_profile(engine_type)


def _build_registry_with_engine(engine_name: str, event_bus: EventBus, working_dir: Optional[Path] = None) -> EngineRegistry:
    """CLI processing always has "mock" available (zero-setup); other
    engine names are only usable once the caller's environment actually
    has them configured -- this keeps `--engine flux2` a clear, honest
    error rather than a silent fallback to mock. If a profile has been
    saved via --configure-engine (or the GUI Settings dialog), it's
    used automatically -- no code edits required (spec §24)."""
    registry = EngineRegistry()
    registry.register("mock", lambda **cfg: MockEngine(event_bus=event_bus, total_steps=4, step_delay=0.0),
                       set_default=True)
    if engine_name not in registry.list_engines():
        if engine_name == "flux2":
            from engines.flux2.config import Flux2Profile
            from engines.flux2.flux2_engine import Flux2Engine
            profile_dict = _load_engine_profile_dict("flux2", working_dir)
            profile = Flux2Profile.from_dict(profile_dict) if profile_dict else None
            registry.register("flux2", lambda **cfg: Flux2Engine(profile=profile, event_bus=event_bus))
        elif engine_name == "realesrgan":
            from engines.realesrgan.config import RealESRGANProfile
            from engines.realesrgan.realesrgan_engine import RealESRGANEngine
            profile_dict = _load_engine_profile_dict("realesrgan", working_dir)
            profile = RealESRGANProfile.from_dict(profile_dict) if profile_dict else None
            registry.register("realesrgan", lambda **cfg: RealESRGANEngine(profile=profile, event_bus=event_bus))
    return registry


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


def create_project_command(directory: str, source: str, name: str, guide_specs: list,
                            generate: bool, padding: int) -> int:
    source_path = Path(source)
    check = validate_input_image(source_path)
    if not check.ok:
        print(f"Invalid source image: {'; '.join(check.errors)}", file=sys.stderr)
        return 2

    image = load_image(source_path)
    project = Project(name=name, source=SourceTexture(
        filename=source_path.name, width=image.width, height=image.height, format=image.format or "PNG",
    ))

    for spec in guide_specs:
        try:
            orientation_str, position_str = spec.split(":", 1)
            orientation = GuideOrientation(orientation_str.strip().lower())
            project.add_guide(Guide(orientation=orientation, position=int(position_str)))
        except (ValueError, KeyError) as exc:
            print(f"Bad --guide value {spec!r}: {exc}", file=sys.stderr)
            return 2

    working_dir = Path(directory)
    manager = ProjectManager()
    manager.create(project, working_dir)
    import shutil as _shutil
    _shutil.copy(source_path, working_dir / "texture" / source_path.name)

    if generate:
        try:
            new_parts = generate_parts(project, image, working_dir, default_padding=padding)
        except PartGenerationError as exc:
            print(f"Part generation failed: {exc}", file=sys.stderr)
            return 2
        print(f"Generated {len(new_parts)} parts.")

    manager.save_as(working_dir)
    print(f"Project created at {working_dir} ({len(project.guides)} guides, {len(project.parts)} parts)")
    return 0


def open_project_command(path: str) -> int:
    manager = ProjectManager()
    try:
        project = manager.load(Path(path), Path(path))
    except (FileNotFoundError, ValueError) as exc:
        print(f"Could not open project: {exc}", file=sys.stderr)
        return 2

    print(f"Project: {project.name}")
    print(f"Texture: {project.source.filename} ({project.source.width}x{project.source.height})")
    print(f"Guides:  {len(project.guides)}")
    print(f"Parts:   {len(project.parts)}")
    for part in project.parts:
        version = part.selected_version()
        version_info = f"{len(part.history)} version(s)" if part.history else "no versions"
        print(f"  {part.id}  {part.name:<16} status={part.status.value:<12} {version_info}")
    return 0


def process_project_command(path: str, engine_name: str, json_mode: bool, quiet: bool) -> int:
    working_dir = Path(path)
    manager = ProjectManager()
    try:
        project = manager.load(working_dir, working_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Could not open project: {exc}", file=sys.stderr)
        return 2

    bus = EventBus()
    if not quiet:
        bus.subscribe_all(_json_event_logger if json_mode else _cli_event_logger)

    registry = _build_registry_with_engine(engine_name, bus, working_dir)
    queue = QueueManager(registry, _default_request_builder(working_dir), event_bus=bus)

    pending = [p for p in project.parts if p.status == PartStatus.PENDING]
    for part in pending:
        queue.add(part, part.engine or engine_name, project_id=project.id)

    queue.start_all()
    queue.wait_idle()
    jobs = queue.get_jobs()
    queue.shutdown()

    manager.save()

    completed = sum(1 for p in project.parts if p.status == PartStatus.COMPLETED)
    failed_jobs = [j for j in jobs if j.state == JobState.FAILED]
    print(f"Processed {len(pending)} part(s): {completed} completed, {len(failed_jobs)} failed.")
    for job in failed_jobs:
        print()
        print(format_job_failure(job, project.get_part(job.part_id), engine_display_name=job.engine))
    return 0 if not failed_jobs else 1


def reconstruct_part_command(path: str, part_id: str, engine_name: str, json_mode: bool, quiet: bool) -> int:
    working_dir = Path(path)
    manager = ProjectManager()
    try:
        project = manager.load(working_dir, working_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Could not open project: {exc}", file=sys.stderr)
        return 2

    part = project.get_part(part_id)
    if part is None:
        print(f"No such part: {part_id}", file=sys.stderr)
        return 2

    bus = EventBus()
    if not quiet:
        bus.subscribe_all(_json_event_logger if json_mode else _cli_event_logger)

    registry = _build_registry_with_engine(engine_name, bus, working_dir)
    queue = QueueManager(registry, _default_request_builder(working_dir), event_bus=bus)
    job = queue.add(part, part.engine or engine_name, project_id=project.id)
    queue.start_all()
    queue.wait_idle()
    queue.shutdown()

    manager.save()

    print(f"Part {part_id}: {job.state.value}")
    if job.state == JobState.FAILED:
        print()
        print(format_job_failure(job, part, engine_display_name=job.engine))
    return 0 if job.state == JobState.COMPLETED else 1


def reassemble_command(path: str, output: str) -> int:
    working_dir = Path(path)
    manager = ProjectManager()
    try:
        project = manager.load(working_dir, working_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Could not open project: {exc}", file=sys.stderr)
        return 2

    source_path = working_dir / "texture" / project.source.filename
    source_image = load_image(source_path)

    try:
        final_image = reassemble(source_image, project.parts)
    except ReassemblyError as exc:
        print(f"Reassembly failed: {exc}", file=sys.stderr)
        return 2

    output_path = export_texture(final_image, output)
    print(f"Final texture exported to {output_path}")
    return 0


# ---------------------------------------------------------------------------
# Engine profile configuration (spec §24: configure engines without editing
# Python source)
# ---------------------------------------------------------------------------

def _profile_class(engine_type: str):
    if engine_type == "flux2":
        from engines.flux2.config import Flux2Profile
        return Flux2Profile
    if engine_type == "realesrgan":
        from engines.realesrgan.config import RealESRGANProfile
        return RealESRGANProfile
    raise ValueError(f"Unknown engine type: {engine_type!r} (expected 'flux2' or 'realesrgan')")


def _coerce_for_field(profile, key: str, raw_value: str) -> object:
    """Coerces based on the dataclass field's DECLARED type, not the
    current value -- a value that happens to be None right now (e.g.
    an unset Optional[float] like timeout_seconds) still needs to be
    parsed as a float, not left as the literal string."""
    import dataclasses
    declared = ""
    for f in dataclasses.fields(profile):
        if f.name == key:
            declared = str(f.type)
            break
    if "bool" in declared:
        return raw_value.strip().lower() in ("1", "true", "yes", "on")
    if "List" in declared or "list" in declared:
        return [v.strip() for v in raw_value.split(",") if v.strip()]
    if "float" in declared:
        try:
            return float(raw_value)
        except ValueError:
            return raw_value
    if "int" in declared:
        try:
            return int(raw_value)
        except ValueError:
            return raw_value
    return raw_value  # str / Optional[str] -- store the raw string as-is


def configure_engine_command(engine_type: str, profile_name: str, set_values: list,
                              scope: str, project_path: Optional[str], set_default: bool) -> int:
    try:
        profile_cls = _profile_class(engine_type)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if scope == "project":
        if not project_path:
            print("--scope project requires --project PATH", file=sys.stderr)
            return 2
        manager = project_settings_manager(Path(project_path))
    else:
        manager = global_settings_manager()

    existing = manager.get_profile(engine_type, profile_name)
    profile = profile_cls.from_dict(existing) if existing else profile_cls()

    for item in set_values:
        if "=" not in item:
            print(f"Bad --set value {item!r}; expected key=value", file=sys.stderr)
            return 2
        key, _, raw_value = item.partition("=")
        key = key.strip()
        if not hasattr(profile, key):
            print(f"Unknown field {key!r} for {engine_type} profile. "
                  f"Valid fields: {', '.join(profile.to_dict().keys())}", file=sys.stderr)
            return 2
        setattr(profile, key, _coerce_for_field(profile, key, raw_value))

    manager.save_profile(engine_type, profile_name, profile.to_dict(), set_default=set_default)
    print(f"Saved {engine_type} profile '{profile_name}' ({scope}, "
          f"stored at {manager.path}).")
    for key, value in profile.to_dict().items():
        print(f"  {key} = {value}")
    return 0


def list_engine_profiles_command(engine_type: str, scope: str, project_path: Optional[str]) -> int:
    if scope == "project":
        if not project_path:
            print("--scope project requires --project PATH", file=sys.stderr)
            return 2
        manager = project_settings_manager(Path(project_path))
    else:
        manager = global_settings_manager()

    profiles = manager.list_profiles(engine_type)
    default_name = manager.get_default_profile_name(engine_type)
    if not profiles:
        print(f"No saved {engine_type} profiles ({scope}, {manager.path}).")
        return 0
    for name, data in profiles.items():
        marker = "  [default]" if name == default_name else ""
        print(f"{name}{marker}")
        for key, value in data.items():
            print(f"  {key} = {value}")
    return 0


# ---------------------------------------------------------------------------
# Original / version selection (spec §18-19: "Use Original", "Use Version")
# ---------------------------------------------------------------------------

def use_original_command(path: str, part_id: str) -> int:
    working_dir = Path(path)
    manager = ProjectManager()
    try:
        project = manager.load(working_dir, working_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Could not open project: {exc}", file=sys.stderr)
        return 2

    part = project.get_part(part_id)
    if part is None:
        print(f"No such part: {part_id}", file=sys.stderr)
        return 2

    part.status = PartStatus.USING_ORIGINAL
    manager.save()
    print(f"Part {part_id} will use the original (unreconstructed) texture region on reassembly.")
    return 0


def use_version_command(path: str, part_id: str, version_id: str) -> int:
    working_dir = Path(path)
    manager = ProjectManager()
    try:
        project = manager.load(working_dir, working_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Could not open project: {exc}", file=sys.stderr)
        return 2

    part = project.get_part(part_id)
    if part is None:
        print(f"No such part: {part_id}", file=sys.stderr)
        return 2

    if not any(v.id == version_id for v in part.history):
        available = ", ".join(v.id for v in part.history) or "(none)"
        print(f"No such version '{version_id}' on part {part_id}. Available: {available}", file=sys.stderr)
        return 2

    part.selected_version_id = version_id
    # Selecting a version must clear a prior "use original"/"skipped"
    # status -- reassembly checks part.status, not just whether a
    # version id happens to be set, so this status flip is required
    # for the newly selected version to actually get used.
    part.status = PartStatus.COMPLETED
    manager.save()
    print(f"Part {part_id} will use version {version_id} on reassembly.")
    return 0
