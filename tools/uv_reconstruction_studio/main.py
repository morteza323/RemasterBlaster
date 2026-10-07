#!/usr/bin/env python3
"""
UV Reconstruction Studio -- entry point.

The PyQt6 GUI (ui/app.py) is the primary way this application is meant
to be used; this CLI (spec §52-55) covers the same operations --
create/open a project, generate parts, process the queue, reconstruct
a single part, reassemble and export -- for scripting, automation, and
headless use, and doubles as a way to exercise the whole non-GUI stack
without a display.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cli.commands import (
    build_full_registry,
    configure_engine_command,
    create_project_command,
    list_engine_profiles_command,
    open_project_command,
    process_project_command,
    reassemble_command,
    reconstruct_part_command,
    run_demo,
    run_diagnostics,
    run_export_diagnostic_report,
    use_original_command,
    use_version_command,
)
from cli.parser import build_parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    working_dir = Path(args.project) if getattr(args, "project", None) else None
    registry = build_full_registry(working_dir=working_dir)

    if args.validate_engine:
        if args.validate_engine not in registry.list_engines():
            print(f"Unknown engine '{args.validate_engine}'. "
                  f"Available: {', '.join(registry.list_engines())}", file=sys.stderr)
            return 2
        engine = registry.create(args.validate_engine)
        result = engine.validate()
        print(f"{args.validate_engine}: {'VALID' if result.is_valid else 'INVALID'}")
        for m in result.messages:
            print(f"  ok: {m}")
        for e in result.errors:
            print(f"  error: {e}")
        return 0 if result.is_valid else 1

    if args.configure_engine:
        return configure_engine_command(
            args.configure_engine, args.profile_name, args.set, args.scope, args.project, args.set_default
        )

    if args.list_engine_profiles:
        return list_engine_profiles_command(args.list_engine_profiles, args.scope, args.project)

    if args.export_diagnostic_report:
        return run_export_diagnostic_report(args.export_diagnostic_report, registry)

    if args.diagnostics:
        return run_diagnostics(registry)

    if args.demo:
        return run_demo()

    if args.create_project:
        if not args.source:
            print("--create-project requires --source IMAGE_PATH", file=sys.stderr)
            return 2
        return create_project_command(
            args.create_project, args.source, args.name, args.guide, args.generate_parts, args.padding
        )

    if args.project:
        if args.reassemble:
            if not args.output:
                print("--reassemble requires --output PATH", file=sys.stderr)
                return 2
            return reassemble_command(args.project, args.output)
        if args.part and args.reconstruct:
            return reconstruct_part_command(args.project, args.part, args.engine, args.json, args.quiet)
        if args.part and args.use_original:
            return use_original_command(args.project, args.part)
        if args.part and args.use_version:
            return use_version_command(args.project, args.part, args.use_version)
        if args.process:
            return process_project_command(args.project, args.engine, args.json, args.quiet)
        return open_project_command(args.project)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
