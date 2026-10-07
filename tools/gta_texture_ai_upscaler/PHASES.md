# GTA Texture AI Upscaler – Phases

## Phase 0 – Skeleton ✅
## Phase 1 – Core Infrastructure ✅
## Phase 2 – Real Inference Backend ✅

## Phase 3 – Production Batch & Safety ✅ (this release)

- SafetyGuard: GPU temp, CPU temp, RAM %, free RAM, VRAM free, CPU util
- Pause when GPU ≥ 72°C or CPU ≥ 75°C; resume at 62°C / 65°C
- Temperature re-check every 60 seconds while paused
- Pause timeout (never stuck forever – max 90–120 min then careful resume)
- Mandatory rest: every 3 hours → 15 minutes pause
- Soft cooldown every 30 jobs
- Consecutive failure pause
- ETA + session summary
- Progress logging with live sensor line

## Phase 4 – Polish & Path Rules ✅ (this release)

- Exact filename preservation (pizza3c.png → pizza3c.png)
- Path mirror: D:/gta/asset/texture/x.png → H:/gta/asset/texture/x.png
- CLI `--input` / `--output` roots
- `--test-inference` single file
- First-run oriented help text
- Version 1.0.0-phase3+4

## Phase 5 – Future
- Merge into los-santos-ai as library/service
