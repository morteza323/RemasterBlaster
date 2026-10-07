"""
Argument parser (spec §52). Kept separate from cli/commands.py so the
CLI's surface area is readable in one place.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uvrs", description="UV Reconstruction Studio")

    parser.add_argument("--diagnostics", action="store_true", help="Run engine/environment diagnostics")
    parser.add_argument("--demo", action="store_true", help="Run an end-to-end demo project through the mock engine")
    parser.add_argument("--validate-engine", metavar="NAME", help="Validate a single registered engine by name")
    parser.add_argument("--export-diagnostic-report", metavar="ZIP_PATH",
                         help="Write a full diagnostic report ZIP to this path")

    parser.add_argument("--create-project", metavar="DIR", help="Create a new project working directory")
    parser.add_argument("--source", metavar="IMAGE_PATH", help="Source texture for --create-project")
    parser.add_argument("--name", metavar="NAME", default="Untitled Project", help="Project name for --create-project")
    parser.add_argument("--guide", action="append", default=[], metavar="ORIENTATION:POSITION",
                         help="Add a guide, e.g. horizontal:256 (repeatable)")
    parser.add_argument("--generate-parts", action="store_true",
                         help="With --create-project: immediately generate parts from the given guides")
    parser.add_argument("--padding", type=int, default=0, help="Context padding (px) for --generate-parts")

    parser.add_argument("--project", metavar="PATH", help="Open an existing project (directory or .uvrs)")
    parser.add_argument("--process", action="store_true", help="With --project: process every pending part")
    parser.add_argument("--part", metavar="PART_ID", help="With --project: target a single part")
    parser.add_argument("--reconstruct", action="store_true", help="With --project --part: reconstruct that one part")
    parser.add_argument("--engine", metavar="NAME", default="mock", help="Engine to use for processing (default: mock)")

    parser.add_argument("--reassemble", action="store_true", help="With --project: reassemble the final texture")
    parser.add_argument("--output", metavar="PATH", help="Output path for --reassemble")

    parser.add_argument("--use-original", action="store_true",
                         help="With --project --part: use the original texture region instead of any AI version")
    parser.add_argument("--use-version", metavar="VERSION_ID",
                         help="With --project --part: select a specific existing version as the final one")

    parser.add_argument("--json", action="store_true", help="Machine-readable JSON event stream (spec §55)")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-event output; print only the final summary")

    parser.add_argument("--configure-engine", metavar="TYPE", choices=["flux2", "realesrgan"],
                         help="Create/update a saved engine profile (e.g. executable/model paths) without editing code")
    parser.add_argument("--list-engine-profiles", metavar="TYPE", choices=["flux2", "realesrgan"],
                         help="List saved profiles for an engine type")
    parser.add_argument("--profile-name", metavar="NAME", default="Default",
                         help="Profile name for --configure-engine / --list-engine-profiles (default: 'Default')")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                         help="With --configure-engine: set a profile field, e.g. --set executable=/path/to/flux-cli "
                              "(repeatable). See the profile's dataclass fields, e.g. executable, model_path, "
                              "quantization, gpu, vram_mode, model_name, tile_size, timeout_seconds")
    parser.add_argument("--scope", choices=["global", "project"], default="global",
                         help="Where to save/read the profile: 'global' (~/.uvrs/engines.json, default) "
                              "or 'project' (requires --project)")
    parser.add_argument("--set-default", action="store_true",
                         help="With --configure-engine: make this profile the default for its engine type")

    return parser
