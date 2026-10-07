# UV Reconstruction Studio

*AI-assisted reconstruction, enhancement, upscaling, and reassembly of
game texture UV layouts.*

**Pure Python (3.10+). No C/C++ anywhere.** The desktop GUI uses
PyQt6; everything else is the standard library plus Pillow, NumPy,
OpenCV, and psutil.

## Status: 176/176 automated tests passing

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests -v
# Ran 176 tests ... OK
```

Everything except the desktop GUI has been run and verified, not just
written -- see "What's genuinely verified vs. not" near the bottom for
the honest, itemized breakdown.

## Installation

```bash
# Python 3.10+
pip install -r requirements.txt        # Pillow, numpy, opencv, psutil (required)
pip install PyQt6                      # only needed for the GUI
```

Real-ESRGAN and Flux.2 are external tools, not Python packages -- see
"Configuring Flux.2" / "Configuring Real-ESRGAN" below.

## Running the GUI

```bash
python3 -m ui.app
```

If PyQt6 isn't installed, or no display is available (headless/SSH
without X11 forwarding), this now prints a clear message telling you
what's missing instead of a raw traceback.

## Running the CLI

```bash
# No real texture needed -- generates a small synthetic one:
python3 scripts/create_sample_project.py /tmp/demo

python3 main.py --project /tmp/demo/sample_project              # inspect
python3 main.py --project /tmp/demo/sample_project --process    # run the queue
python3 main.py --project /tmp/demo/sample_project --part <ID> --reconstruct   # one part
python3 main.py --project /tmp/demo/sample_project --part <ID> --use-original  # revert to source
python3 main.py --project /tmp/demo/sample_project --part <ID> --use-version <VID>
python3 main.py --project /tmp/demo/sample_project --reassemble --output final.png

python3 main.py --diagnostics
python3 main.py --validate-engine flux2
python3 main.py --export-diagnostic-report report.zip
```

`--process` and `--part ... --reconstruct` accept `--engine mock`
(default, no setup needed), `--engine flux2`, or `--engine realesrgan`
(both need to be configured first -- next section). Add `--json` for
a machine-readable event stream, or `--quiet` to suppress it.

## Configuring Flux.2

The default profile is **Flux.2 Klein 4B, quantization 4Q_K_M**
(unquantized/other Flux variants work too -- it's all configuration,
nothing is hard-coded to one model). Configure it without touching
any Python:

```bash
python3 main.py --configure-engine flux2 --profile-name "My Flux" --set-default \
  --set executable="/path/to/flux-cli" \
  --set model_path="/path/to/flux2-klein-4b-q4km" \
  --set gpu=0 \
  --set vram_mode=high \
  --set timeout_seconds=300

python3 main.py --list-engine-profiles flux2
python3 main.py --validate-engine flux2   # confirms the executable/model actually exist
```

Fields: `executable`, `model_path`, `model` (display name), `quantization`,
`gpu`, `vram_mode`, `extra_arguments` (comma-separated, appended
verbatim to the command), `timeout_seconds`. On Windows, quote paths
with spaces normally, e.g. `--set executable="C:\Program Files\flux\flux-cli.exe"`.

Add `--scope project --project <path>` to save a profile scoped to
one project instead of globally (`~/.uvrs/engines.json`) -- project
settings take priority when both exist. The GUI has the same thing
under **Settings > Engine Settings...**, reading/writing the identical
JSON files.

**Important, honest caveat**: no real Flux.2 CLI binary was available
in the environment this was built and tested in, so the exact
command-line flags (`--model`, `--quantization`, `--prompt`, etc.) in
`engines/flux2/flux2_engine.py` are reasonable, documented assumptions
-- not verified against a real release. `--validate-engine flux2` will
correctly tell you if your configured executable/model paths don't
exist; if the executable *does* exist but rejects the arguments,
check `logs/engine/flux2.log` and the per-job `command.txt` (`--export-diagnostic-report`
bundles these), and adjust `CLIEngineConfig`'s argument templates in
that file to match your actual CLI's real flags -- nothing else in
the application needs to change (that's the whole point of the engine
abstraction, spec §1).

## Configuring Real-ESRGAN

```bash
python3 main.py --configure-engine realesrgan --profile-name "My ESRGAN" --set-default \
  --set executable="/path/to/realesrgan-ncnn-vulkan" \
  --set model_name=realesrgan-x4plus \
  --set model_path="/path/to/models" \
  --set tile_size=256 \
  --set gpu_id=0
```

Fields: `executable`, `model_name` (`-n`), `model_path` (`-m`, the
directory with the `.param`/`.bin` files), `tile_size` (`-t`, lower
values use less VRAM), `gpu_id` (`-g`), `extra_arguments`,
`timeout_seconds`. Same caveat as Flux.2: these flags follow the
widely-published `realesrgan-ncnn-vulkan` CLI convention but weren't
verified against a real binary here. Real-ESRGAN is upscale-only --
its declared capabilities have `supports_prompt`/`supports_strength`/
etc. all `False`, so its part-inspector controls are disabled and
`reconstruct()` fails cleanly with an explanation rather than silently
doing nothing.

## Basic workflow

```
Load Texture -> add guides -> Generate Queue -> configure parts
   -> Process -> review (Preview/Versions) -> retry / Use Original / Use Version
   -> Reassemble -> Export
```

GUI: File > New Project (pick a texture, a folder), drag guides on
the canvas, Queue > Generate Queue, Queue > Start All, click a part to
inspect/preview/compare versions, File > Export Final Texture.

CLI: `--create-project` (with `--guide h:POS`, repeatable, and
`--generate-parts`) → `--process` → `--reassemble`. See
`scripts/create_sample_project.py` for a runnable example.

## What's genuinely verified vs. not

Per-run, real verification -- not "should work":

**Verified by running it, repeatedly, in this session:**
- The full test suite: 176/176 passing (`python3 -m unittest discover -s tests`)
- The complete CLI workflow end to end: create project → add guides →
  generate parts → process (mock engine) → retry a failed part →
  use-original → use-version → reassemble → export → reopen project
- `--diagnostics` / `--validate-engine` correctly report Flux.2 and
  Real-ESRGAN as present-but-not-configured in this environment (no
  real binaries here) -- proving the diagnostic path itself works
- `--configure-engine` / `--list-engine-profiles` round-tripping real
  profile data, including on-disk atomic writes
- The generic CLI engine (`engines/custom_cli/`) against a real (stub)
  subprocess: real-time output streaming, nonzero exit codes,
  cooperative cancellation, timeouts, and -- specifically fixed this
  session -- a missing/unlaunchable executable returning a proper
  failed result instead of crashing
- Every file in `ui/` compiles cleanly (`python3 -m py_compile`)

**Implemented, NOT independently executed (be honest with yourself
about this before relying on it):**
- The PyQt6 GUI itself (`ui/`) -- no PyQt6 install and no display in
  this environment. Every non-GUI subsystem it calls into is the
  same tested code above; the risk is concentrated in `ui/`'s ~3,000
  lines specifically. **Launch it and exercise it by hand before
  trusting it** -- that's genuinely the next real step, not something
  that can be honestly claimed as verified from here.
- Real Flux.2 reconstruction -- no real executable/model available to
  run. The adapter's plumbing (subprocess management, logging, error
  handling) is the same code tested above against a stub; only the
  exact CLI flag names are unverified.
- Real Real-ESRGAN upscaling -- same caveat.

This session found and fixed several real bugs by actually running
things rather than reading code and assuming: a crash on a missing
executable, `QueueManager` never actually publishing `JOB_FAILED` /
`JOB_COMPLETED` itself (only relying on the engine to), a type-coercion
bug in `--configure-engine`, a mask-capability/command-builder
inconsistency (an engine could declare "no mask support" while still
silently sending `--mask` if one was set), and `main.py --diagnostics`
never registering Flux.2/Real-ESRGAN at all. All have regression tests.

## Troubleshooting

- **"Executable not found" from `--validate-engine`**: the path in
  your saved profile doesn't exist on disk. Re-run `--configure-engine`
  with the correct path, or `--list-engine-profiles` to check what's
  currently saved.
- **GUI won't start**: `pip install PyQt6`. If it still won't start
  and you're on a remote/headless machine, you need a real desktop
  session (or X11/Wayland forwarding) -- the CLI works everywhere.
- **A part fails during `--process`**: the CLI prints a structured
  report (engine, part, category, error, and a numbered checklist) for
  every failure, not just a count. Check `logs/jobs/<job_id>.log` and,
  for CLI-engine-based failures, `logs/engine/<engine>.log` and that
  job's `command.txt`/`stderr.log` for the exact command and its raw
  output.
- **Reassembly fails with "no selected output"**: a part hasn't been
  processed yet, or its only attempt failed. Process it, or
  explicitly `--use-original` that part.

## Architecture principle (unchanged, load-bearing)

```
GUI / CLI -> QueueManager -> EngineRegistry -> ReconstructionEngine (ABC)
                                                      |
                                    MockEngine / CustomCLIEngine / Flux2Engine / RealESRGANEngine
```

Adding or swapping an AI backend means writing one adapter class and
registering it. The UV editor, project system, queue, logging, and
reassembly engine never change. This finalization pass tightened that
guarantee: `CustomCLIEngine` now automatically nulls out any command
argument whose capability flag says "unsupported," so a subclass can
no longer declare a capability false while its config still builds a
command that uses it.

## Project layout

```
main.py                    CLI entry point
scripts/create_sample_project.py   synthetic texture + project, no AI needed
app/                        version + global constants
core/
  events/                    EventBus + EventType
  models/                    Guide, Bounds, Part, Job (+ state machine), Project
  error_formatting.py         spec §28 structured failure reports (CLI + GUI share this)
project_system/             .uvrs / directory storage, autosave, crash recovery, engine settings
image/                      crop/padding, resize, mask, blend, difference, color/alpha, validation
uv/                          region calculation + "Generate Queue"
pipeline/                    QueueManager + ProgressParser
engines/
  base/                       ReconstructionEngine ABC
  mock/                       zero-dependency MockEngine
  custom_cli/                 generic configurable CLI engine + subprocess plumbing
  flux2/, realesrgan/          pre-configured CustomCLIEngine profiles
  registry.py                  EngineRegistry
prompts/                     prompt composition + material/prompt presets
reassembly/                  exact-coordinate compositing + export
diagnostics/                 system/engine diagnostics + report export
logging_system/              hierarchical logging
cli/                          argument parser + all CLI commands
ui/                           PyQt6 GUI (see "What's genuinely verified vs. not")
tests/                       176 tests, stdlib unittest + Pillow/numpy fixtures
```
