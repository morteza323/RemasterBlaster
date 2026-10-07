#!/usr/bin/env python3
"""
GTA Texture AI Upscaler v6.0 – photoreal + RealESRGAN x4 + anti-green + resume-safe Ctrl+C

Examples:
  python main.py --test-hardware
  python main.py --validate
  python main.py --encode-prompt

  python main.py --scan --input "D:/gta" --output "H:/gta"
  python main.py --stats
  python main.py --start --input "D:/gta" --output "H:/gta" --dry-run --max-jobs 3
  python main.py --start --input "D:/gta" --output "H:/gta"
  python main.py --test-inference --input "D:/gta/asset/texture/pizza3c.png" --output "H:/gta/asset/texture/pizza3c.png"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.core.logger import setup_logger, get_logger
from src.core.hardware import detect_hardware, print_hardware_report, SafetyGuard
from src.backend.sdcpp_backend import SDCppBackend
from src.pipeline.engine import UpscaleEngine, map_output_path
from src.utils.validation import run_full_validation
from src.core.database import JobDatabase


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="GTA Texture AI Upscaler v6.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--config", type=str, default=None)
    p.add_argument("--test-hardware", action="store_true")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--encode-prompt", action="store_true")
    p.add_argument("--scan", action="store_true")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--start", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-jobs", type=int, default=None)
    p.add_argument("--input", type=str, default=None,
                   help="Input root folder OR single file for --test-inference")
    p.add_argument("--output", type=str, default=None,
                   help="Output root folder OR single file path for --test-inference")
    p.add_argument("--test-inference", action="store_true",
                   help="Process a single image (requires --input and --output file paths)")
    p.add_argument("--list-skipped", action="store_true",
                   help="Show textures skipped as empty/flat")
    p.add_argument("--gui", action="store_true",
                   help="Launch optional GUI (settings + side-by-side preview). CLI pipeline unchanged.")
    p.add_argument("--export-skipped", type=str, default=None,
                   help="Write skipped source paths to a text file")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    cfg = load_config(args.config, project_root=PROJECT_ROOT)
    setup_logger(cfg)
    logger = get_logger("main")

    logger.info(f"{cfg.name} v{cfg.version}")
    logger.info(f"Project root: {cfg.project_root}")

    for d in [cfg.data_dir, cfg.log_dir, "data/embeddings", "data/jobs"]:
        cfg.resolve_path(d).mkdir(parents=True, exist_ok=True)

    backend = SDCppBackend(cfg)
    engine = UpscaleEngine(cfg, backend)

    if args.test_hardware:
        info = detect_hardware()
        print_hardware_report(info)
        print("\nSafety limits from config:")
        h = cfg.hardware
        print(f"  GPU pause/resume : {h.gpu_pause_temp_c}°C / {h.gpu_resume_temp_c}°C")
        print(f"  CPU pause/resume : {h.cpu_pause_temp_c}°C / {h.cpu_resume_temp_c}°C")
        print(f"  Max RAM percent  : {h.max_ram_percent}%")
        print(f"  Min free RAM     : {h.min_free_ram_mb} MB")
        print(f"  Min free VRAM    : {h.min_free_vram_mb} MB")
        print(f"  Rest every       : {h.rest_every_hours} h for {h.rest_duration_minutes} min")
        return 0

    if args.validate:
        result = run_full_validation(cfg)
        if result.ok:
            print("\nValidation PASSED.")
            return 0
        print("\nValidation FAILED. Fix errors before --start.")
        return 1

    if args.encode_prompt:
        engine.encode_master_prompt()
        return 0

    if args.scan:
        n = engine.scan_input_folder(input_dir=args.input, output_dir=args.output)
        print(f"Added {n} new job(s).")
        engine.print_stats()
        return 0

    if args.stats:
        engine.print_stats()
        return 0

    if args.test_inference:
        if not args.input or not args.output:
            print("ERROR: --test-inference needs --input FILE and --output FILE")
            return 1
        src = Path(args.input)
        dst = Path(args.output)
        if not src.is_file():
            print(f"ERROR: input file not found: {src}")
            return 1
        try:
            backend.initialize()
            cache = cfg.resolve_path(cfg.prompt.embedding_cache)
            if not cache.exists():
                engine.encode_master_prompt()
            seed = JobDatabase.make_seed(str(src))
            print(f"Test inference: {src} → {dst} (seed={seed})")
            backend.process_image(src, dst, seed=seed)
            backend.shutdown()
            print("SUCCESS")
            return 0
        except Exception as e:
            logger.exception(str(e))
            print(f"FAILED: {e}")
            return 1

    if args.list_skipped:
        rows = engine.db.list_skipped()
        print(f"Skipped count: {len(rows)}")
        for r in rows[:50]:
            print(f"  [{r['id']}] {r['relative_path']} | {r['last_error']}")
        if len(rows) > 50:
            print(f"  ... and {len(rows)-50} more")
        return 0

    if args.export_skipped:
        n = engine.db.export_skipped_paths(Path(args.export_skipped))
        print(f"Exported {n} skipped paths → {args.export_skipped}")
        return 0

    if args.gui:
        from src.ui.gui_app import launch_gui
        logger.info("Launching GUI (console logging remains active)...")
        launch_gui(cfg, engine, backend)
        return 0

    if args.start:
        logger.info("Starting batch engine (production mode)...")
        try:
            engine.run(
                dry_run=args.dry_run,
                max_jobs=args.max_jobs,
                input_dir=args.input,
                output_dir=args.output,
            )
        except RuntimeError as e:
            logger.error(str(e))
            print(f"\nERROR: {e}")
            return 1
        return 0

    parser.print_help()
    print("\n=== Quick start ===")
    print('  python main.py --scan --input "D:/gta" --output "H:/gta"')
    print('  python main.py --start --input "D:/gta" --output "H:/gta" --dry-run --max-jobs 5')
    print('  python main.py --start --input "D:/gta" --output "H:/gta"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
