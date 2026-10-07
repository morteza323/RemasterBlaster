"""
Main batch engine – Phase 3+4 Production (v5.0)
- Full safety guards (temp / RAM / VRAM / CPU / mandatory rest)
- Exact filename preservation
- Input→Output path mirror (different root)
- Resume, ETA, consecutive failure protection
- Graceful Ctrl+C: show progress, save state, exit cleanly (no terminate prompt)
"""

from __future__ import annotations

import signal
import threading
import sys
import time
from pathlib import Path
from typing import List, Optional

from ..backend.base import IInferenceBackend
from ..core.config import AppConfig, load_master_prompt
from ..core.database import JobDatabase
from ..core.hardware import SafetyGuard
from ..core.logger import get_logger
from ..utils.image_analysis import analyze_texture
from ..utils.material_hint import infer_material, is_uv_critical_filename, has_large_flat_regions

logger = get_logger("pipeline")

# Global flag set by signal handler so the loop can exit cleanly
_INTERRUPT_REQUESTED = False


def _on_sigint(signum, frame):
    """Set flag only — do NOT call sys.exit here (avoids Windows batch Terminate Y/N)."""
    global _INTERRUPT_REQUESTED
    _INTERRUPT_REQUESTED = True
    # Print immediately so user sees something
    print("\n\n[!] Ctrl+C detected — finishing current step safely, then showing progress...", flush=True)


def map_output_path(
    source: Path,
    input_root: Path,
    output_root: Path,
    preserve_filename: bool = True,
    force_png: bool = False,
) -> Path:
    """
    Mirror relative path from input_root to output_root.
    Keep exact original filename (stem + extension) unless force_png.
    """
    try:
        rel = source.relative_to(input_root)
    except ValueError:
        rel = Path(source.name)

    out = output_root / rel
    if force_png:
        out = out.with_suffix(".png")
    elif preserve_filename:
        out = out.with_name(source.name)
    return out


class UpscaleEngine:
    def __init__(self, cfg: AppConfig, backend: IInferenceBackend):
        self.cfg = cfg
        self.backend = backend
        self.db = JobDatabase(cfg)
        self.safety = SafetyGuard(cfg)
        self._jobs_since_cooldown = 0
        self._times: List[float] = []
        self._consecutive_fails = 0
        self._input_root: Optional[Path] = None
        self._output_root: Optional[Path] = None
        # Optional GUI hooks (no-op if unset) — never required for CLI
        self.on_job_start = None   # callable(source: Path, output: Path, label: str)
        self.on_job_done = None    # callable(source: Path, output: Path, ok: bool, ms: int)

    def set_roots(self, input_dir: Optional[str] = None, output_dir: Optional[str] = None) -> None:
        self._input_root = Path(input_dir) if input_dir else self.cfg.resolve_path(self.cfg.input_dir)
        self._output_root = Path(output_dir) if output_dir else self.cfg.resolve_path(self.cfg.output_dir)
        self._input_root = self._input_root.resolve()
        self._output_root = self._output_root.resolve()
        logger.info(f"Input root : {self._input_root}")
        logger.info(f"Output root: {self._output_root}")

    def scan_input_folder(
        self,
        extensions: Optional[List[str]] = None,
        input_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> int:
        if extensions is None:
            extensions = [".png", ".jpg", ".jpeg", ".bmp", ".tga", ".dds", ".webp"]
        extensions = [e.lower() for e in extensions]

        self.set_roots(input_dir, output_dir)
        assert self._input_root and self._output_root

        if not self._input_root.exists():
            logger.warning(f"Input folder does not exist: {self._input_root}")
            return 0

        preserve = self.cfg.inference.preserve_filename
        force_png = self.cfg.inference.force_png_extension

        added = 0
        for path in self._input_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in extensions:
                continue
            out_path = map_output_path(
                path, self._input_root, self._output_root, preserve, force_png
            )
            rel = str(path.relative_to(self._input_root))
            if self.db.add_job(path, rel, out_path):
                added += 1

        logger.info(f"Scan complete. New jobs added: {added}")
        return added

    def encode_master_prompt(self) -> None:
        prompt = load_master_prompt(self.cfg)
        cache = self.cfg.resolve_path(self.cfg.prompt.embedding_cache)
        logger.info("Caching master prompt (one-time)...")
        self.backend.encode_prompt_once(prompt, cache)
        logger.info("Master prompt ready.")

    def _eta_str(self, remaining: int) -> str:
        if not self._times or remaining <= 0:
            return "N/A"
        avg = sum(self._times) / len(self._times)
        sec = avg * remaining
        if sec < 60:
            return f"{sec:.0f}s"
        if sec < 3600:
            return f"{sec/60:.1f} min"
        return f"{sec/3600:.1f} h"

    def _print_interrupt_progress(self) -> None:
        """Show clear progress so user knows resume will work."""
        stats = self.db.stats()
        print("\n" + "=" * 56)
        print("  PAUSED — progress saved. You can close this window.")
        print("=" * 56)
        for k, v in stats.items():
            print(f"  {k:12}: {v}")
        if self._times:
            avg = sum(self._times) / len(self._times)
            print(f"  avg time   : {avg:.1f}s / texture")
        print("-" * 56)
        print("  Next time just run again:")
        print('    python main.py --start')
        print("  or double-click start.bat")
        print("  It will continue from where it left off.")
        print("=" * 56)
        print("\nPress any key to exit...")
        try:
            # Windows-friendly pause without "Terminate batch job"
            if sys.platform == "win32":
                import msvcrt
                msvcrt.getch()
            else:
                input()
        except Exception:
            pass

    def run(
        self,
        dry_run: bool = False,
        max_jobs: Optional[int] = None,
        input_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> None:
        global _INTERRUPT_REQUESTED
        _INTERRUPT_REQUESTED = False

        # Install SIGINT handler only on main thread (GUI runs engine in a worker thread)
        old_handler = None
        try:
            if threading.current_thread() is threading.main_thread():
                old_handler = signal.signal(signal.SIGINT, _on_sigint)
        except (ValueError, RuntimeError):
            old_handler = None

        if input_dir or output_dir or self._input_root is None:
            self.set_roots(input_dir, output_dir)

        self.db.recover_running_jobs()

        # Sync any files user added to fixed-with-ai (e.g. ChatGPT/manual upscales)
        # so they are marked SKIPPED and never re-processed by Flux.
        fixed_dir = Path(getattr(self.cfg, "handmade_dir", None) or r"H:\gta sa textures\fucked up fixed with ai")
        try:
            logger.info(f"Scanning fixed-with-ai folder: {fixed_dir}")
            n_sync = self.db.sync_fixed_folder_to_skipped(fixed_dir)
            logger.info(f"fixed-with-ai sync done → newly SKIPPED: {n_sync}")
        except Exception as e:
            logger.warning(f"fixed-with-ai sync failed (continuing): {e}")

        stats = self.db.stats()
        logger.info(f"Job stats before start: {stats}")
        logger.info(f"Safety: {self.safety.status_line()}")

        if not dry_run:
            self.backend.initialize()
            cache = self.cfg.resolve_path(self.cfg.prompt.embedding_cache)
            master = load_master_prompt(self.cfg)
            cache_stale = True
            if cache.exists():
                try:
                    import json, hashlib
                    cached = json.loads(cache.read_text(encoding="utf-8"))
                    cache_stale = cached.get("prompt_sha256") != hashlib.sha256(
                        master.encode("utf-8")
                    ).hexdigest()
                except Exception:
                    cache_stale = True
            if cache_stale:
                logger.info("Prompt cache missing/stale – refreshing it now...")
                self.encode_master_prompt()

        processed = 0
        session_t0 = time.time()

        try:
            while True:
                if _INTERRUPT_REQUESTED:
                    break

                if max_jobs is not None and processed >= max_jobs:
                    logger.info(f"Reached max_jobs={max_jobs}, stopping.")
                    break

                # ===== SAFETY GATE =====
                try:
                    self.safety.ensure_safe_to_run()
                except Exception as e:
                    logger.error(f"Safety guard error (continuing carefully): {e}")

                if _INTERRUPT_REQUESTED:
                    break

                job = self.db.get_next_pending()
                if job is None:
                    logger.info("No more PENDING jobs.")
                    break

                job_id = job["id"]
                source = Path(job["source_path"])
                output = Path(job["output_path"])
                seed = job["seed"]
                stats = self.db.stats()
                remaining = stats.get("PENDING", 0)
                done = stats.get("COMPLETED", 0)
                total = stats.get("total", 0)

                logger.info(
                    f"── Job #{job_id} [{done+1}/{total}] | {job['relative_path']} | "
                    f"seed={seed} | ETA {self._eta_str(max(0, remaining-1))} | "
                    f"{self.safety.status_line()}"
                )
                logger.info(f"   OUT → {output}")

                if dry_run:
                    logger.info("  [DRY-RUN] would process this texture")
                    self.db.mark_running(job_id)
                    self.db.mark_completed(job_id, 0)
                    processed += 1
                    self._consecutive_fails = 0
                    continue

                # Skip LOD textures (count as skipped)
                if "lod" in source.name.lower():
                    logger.info(f"  SKIP → LOD texture")
                    self.db.mark_skipped(job_id, "LOD texture")
                    processed += 1
                    continue

                # Skip if already present in fixed-with-ai folder
                # Match by exact name OR same stem (any extension), case-insensitive.
                fixed_dir = Path(getattr(self.cfg, "handmade_dir", None) or r"H:\gta sa textures\fucked up fixed with ai")
                already_fixed = False
                if fixed_dir.is_dir():
                    if (fixed_dir / source.name).exists():
                        already_fixed = True
                    else:
                        stem_l = source.stem.lower()
                        try:
                            for p in fixed_dir.iterdir():
                                if p.is_file() and p.stem.lower() == stem_l:
                                    already_fixed = True
                                    break
                        except OSError:
                            pass
                if already_fixed:
                    logger.info(f"  SKIP → already in fixed-with-ai folder")
                    self.db.mark_skipped(job_id, "already in fixed-with-ai folder")
                    processed += 1
                    continue

                # Skip empty / flat textures
                if source.is_file():
                    decision = analyze_texture(source)
                    if decision.should_skip:
                        logger.info(
                            f"  SKIP → {decision.reason} | black={decision.black_ratio:.2f} var={decision.variance:.1f}"
                        )
                        self.db.mark_skipped(job_id, decision.reason)
                        processed += 1
                        continue

                self.db.mark_running(job_id)
                t0 = time.perf_counter()
                try:
                    # V5.2 REALISM MODE:
                    # The previous engine silently capped many ordinary textures at 0.27-0.34
                    # (flat regions, alpha, doors, signs, etc.). That meant changing the GUI
                    # strength to 1.0 often had almost no effect. For normal world textures,
                    # respect the user's requested strength. Only true UV-critical character
                    # atlases keep a hard safety cap because their pixel placement is gameplay-critical.
                    strength = float(self.cfg.inference.strength)
                    try:
                        lab, _ = infer_material(source.name, image_path=source, allow_filename_hints=getattr(self.cfg.inference, "filename_material_hints", False))
                    except Exception:
                        lab = ""
                    if is_uv_critical_filename(source.name):
                        strength = min(strength, 0.30)
                        logger.info(f"  UV-critical [generic 2D UV layout safety] → strength={strength} (hard layout safety cap; no material classification)")
                    else:
                        logger.info(f"  REALISM [image-first; filename semantics disabled] → strength={strength} (no legacy cap)")

                    if callable(self.on_job_start):
                        try:
                            self.on_job_start(source, output, lab or "")
                        except Exception:
                            pass
                    self.backend.process_image(source, output, seed=seed, strength=strength)
                    if _INTERRUPT_REQUESTED:
                        # Job finished successfully before we noticed interrupt
                        elapsed_ms = int((time.perf_counter() - t0) * 1000)
                        self.db.mark_completed(job_id, elapsed_ms)
                        self._times.append(elapsed_ms / 1000.0)
                        break
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                    self.db.mark_completed(job_id, elapsed_ms)
                    self._times.append(elapsed_ms / 1000.0)
                    if len(self._times) > 50:
                        self._times = self._times[-50:]
                    self._consecutive_fails = 0
                    logger.info(f"  Completed in {elapsed_ms/1000:.1f}s → {output.name}")
                    if callable(self.on_job_done):
                        try:
                            self.on_job_done(source, output, True, elapsed_ms)
                        except Exception:
                            pass
                except Exception as e:
                    if _INTERRUPT_REQUESTED:
                        # Put current job back to PENDING
                        self.db.recover_running_jobs()
                        logger.warning("Interrupted during job — restored to PENDING for resume.")
                        break
                    logger.exception(f"  Failed: {e}")
                    self.db.mark_failed(job_id, str(e), self.cfg.batch.max_retries)
                    self._consecutive_fails += 1
                    if self._consecutive_fails >= self.cfg.batch.consecutive_fail_pause:
                        pause_s = self.cfg.batch.consecutive_fail_pause_seconds
                        logger.warning(
                            f"{self._consecutive_fails} consecutive failures — "
                            f"cooling pause {pause_s}s"
                        )
                        # Sleep in small chunks so Ctrl+C is responsive
                        for _ in range(pause_s):
                            if _INTERRUPT_REQUESTED:
                                break
                            time.sleep(1)
                        self._consecutive_fails = 0

                processed += 1
                self._jobs_since_cooldown += 1

                if self._jobs_since_cooldown >= self.cfg.batch.jobs_before_cooldown:
                    logger.info(f"Soft cooldown after {self._jobs_since_cooldown} jobs...")
                    for _ in range(self.cfg.batch.cooldown_seconds):
                        if _INTERRUPT_REQUESTED:
                            break
                        time.sleep(1)
                    self._jobs_since_cooldown = 0

        finally:
            # Always restore default handler and shut down backend
            try:
                if old_handler is not None:
                    signal.signal(signal.SIGINT, old_handler)
            except Exception:
                pass
            if not dry_run:
                try:
                    self.backend.shutdown()
                except Exception:
                    pass
            # Ensure any RUNNING jobs are put back to PENDING
            self.db.recover_running_jobs()

        if _INTERRUPT_REQUESTED:
            self._print_interrupt_progress()
            return

        final = self.db.stats()
        elapsed_h = (time.time() - session_t0) / 3600.0
        logger.info(f"Finished. Final stats: {final}")
        logger.info(f"Session wall time: {elapsed_h:.2f} hours")
        self._print_session_summary(final, elapsed_h)

    def _print_session_summary(self, stats: dict, hours: float) -> None:
        print("\n" + "=" * 50)
        print("SESSION SUMMARY")
        print("=" * 50)
        for k, v in stats.items():
            print(f"  {k:12}: {v}")
        if self._times:
            avg = sum(self._times) / len(self._times)
            print(f"  avg time   : {avg:.1f}s / texture")
        print(f"  wall time  : {hours:.2f} h")
        print("=" * 50 + "\n")

    def print_stats(self) -> None:
        stats = self.db.stats()
        print("─" * 40)
        print("JOB STATISTICS")
        for k, v in stats.items():
            print(f"  {k:12}: {v}")
        print("─" * 40)
