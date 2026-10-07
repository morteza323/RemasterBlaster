# Remaster Blaster

**The most complete, controllable, and production-ready toolkit ever built for remastering GTA San Andreas textures.**

This is not another "drag and drop Real-ESRGAN" script.  
This is a full production pipeline that was forged in the fire of creating one of the most ambitious GTA SA texture packs ever attempted — where every single texture had to look right, feel authentic, and work perfectly inside the game.

If you've ever tried to remaster GTA San Andreas textures seriously, you already know the pain:

- Real-ESRGAN and similar tools destroy color, invent details that never existed, and completely ignore the unique constraints of GTA SA.
- AI upscalers turn blood textures into red paint, asphalt into wet plastic, and checker patterns into colorful nonsense.
- LODs get ignored or generated incorrectly.
- Texture names in IMG archives don't match the clean names you need.
- Dimensions become illegal and crash the game or look stretched.
- You end up with thousands of files and no way to systematically find what's missing, what's broken, or what still needs manual work.
- Rebuilding TXDs while preserving DXT compression, alpha, and mipmaps is a nightmare.

**Remaster Blaster was built to solve every single one of these problems.**

---

## Why This Is Completely Different From Normal Upscalers

Most people think "AI texture upscaling" means running Real-ESRGAN or a similar model on a folder and hoping for the best. That approach fails hard on GTA San Andreas for several fundamental reasons:

### 1. GTA SA Has Extremely Specific Constraints
Textures must often be power-of-two. Many must preserve exact aspect ratios. Alpha channels are critical. Certain textures (especially player skins, faces, beards, clothing) use uncompressed formats that most tools butcher. LODs must stay low-resolution and correctly matched. One wrong texture can break an entire area of the map or make a character look grotesque.

### 2. Generic AI Upscalers Hallucinate
Real-ESRGAN and similar models are trained on general photography and art. They have no concept of:
- What a 2004 Rockstar texture is supposed to look like
- That `bloodra` and `bloodrb` are not supposed to become bloody messes
- That checker patterns must stay strictly black and white
- That asphalt, concrete, dirt, and grass have very specific visual languages in this game
- That many textures are deliberately low-detail and stylized

### 3. You Need Full Control, Not a Black Box
When you're making a serious texture pack, you cannot accept "the AI decided this looks better." You need to decide. You need to be able to fix individual files, re-process only the failures, compare original vs upscaled side-by-side at scale, generate proper LODs, fix naming mismatches, and rebuild the actual game archives correctly.

**Remaster Blaster gives you that control.**

Every major step of the pipeline is exposed, configurable, and designed so that a human remains in charge.

---

## What This Toolkit Actually Contains

This is not a single tool. It is a complete ecosystem of specialized tools that work together:

### Core AI Upscaler (`gta_texture_ai_upscaler`)
A production-grade Flux-based img2img pipeline specifically tuned for GTA SA textures.

Key differences from normal upscalers:
- Fine-grained filename category system (~90 categories)
- Strong color locking (grayscale stays grayscale, two-tone stays two-tone)
- Explicit bans on common AI failures (blood splatters, invented stains, random recoloring)
- Fidelity system that anchors the result to the original low-frequency structure
- Safety system that monitors GPU/CPU temperature, RAM, and VRAM and pauses when needed
- Resume support and job database so long runs can be interrupted and continued
- Path mirroring so your folder structure is preserved

This is not "run AI and pray." This is a controlled generation system.

### UV Reconstruction Studio
For complex textures where simple upscaling is not enough. Split UV layouts into parts, reconstruct or enhance individual parts with different engines (Flux, Real-ESRGAN, or mock), then reassemble with full version history and diagnostics. Includes both a serious GUI and a complete CLI, plus extensive automated tests.

### TXD Mirror Builder
One of the most important tools in the entire suite. It rebuilds actual GTA SA TXD files inside IMG archives while:
- Preserving the original format (DXT1/DXT3/DXT5/XRGB32/ARGB8888)
- Using very strict matching rules to prevent wrong texture swaps
- Handling special cases in `player.img` (faces, hair, beards, clothing)
- Leaving unmatched textures completely untouched

Most people who try to replace textures in GTA SA end up with broken archives. This tool was built specifically to avoid that.

### Professional QA Tool
A fast, keyboard-driven reviewer designed for thousands of textures. Manual mode with efficient shortcuts + automatic mode. Session resume. Move or copy bad results. Built for the reality of reviewing 15,000–25,000 files.

### Dimension & Legality Tools
- **SAN Resize Tool**: Detects and fixes illegal dimensions and bad aspect ratios while preserving visual quality as much as possible.
- **Texture Dimension Fixer**: Restores original dimensions from logs when previous tools damaged legal sizes.

### LOD Maker
Automatically finds high-resolution sources and generates proper lower-resolution LOD versions with correct power-of-two sizing. LODs are one of the most commonly neglected parts of texture packs.

### Naming & Matching Utilities
- **IMG Prefix Fixer**: Solves the classic problem where TXDs expect clean names (`tex.png`) but files on disk have prefixes (`something_tex.png`).
- **IMG Texture Checker**: Verifies what actually exists inside IMG archives.
- **Filename Keywords tools**: Analyze common prefixes and suffixes across thousands of files to understand Rockstar's naming patterns and improve categorization.

### Workflow Utilities
- **Remove Duplicated**: After you manually fix some textures, this removes the AI versions from the big folder so only unfinished work remains.
- **Copy Skipped**: Pulls all failed/skipped jobs from the AI upscaler database into a dedicated "need to fix" folder.
- **Scan Missing**: Uses a large curated keyword system focused on GTA SA environments (roads, dirt, grass, concrete, rock, sand, etc.) to find gaps.

---

## Recommended Production Workflow

This is the workflow that was actually used:

1. Extract original textures from the game archives.
2. Run the main AI Upscaler (Flux pipeline).
3. Manually fix the worst results (or use other methods) and put them in a "fixed" folder.
4. Use **Remove Duplicated** to clean the AI output folder.
5. Use **Copy Skipped** to collect remaining failures.
6. Fix dimensions with **SAN Resize Tool** / Dimension Fixer.
7. Generate missing LODs with **LOD Maker**.
8. Fix naming issues with **IMG Prefix Fixer**.
9. Do systematic QA with the QA Tool.
10. Rebuild final TXDs with **TXD Mirror Builder**.
11. (Optional) Use UV Reconstruction Studio for particularly difficult UV layouts.

Every step is optional and controllable. You can stop, inspect, fix, and continue at any point.

---

## Who This Is For

- People making serious GTA SA texture packs
- Modders who are tired of black-box AI results
- Anyone who has tried Real-ESRGAN / similar tools on GTA SA and been disappointed
- Developers who want to understand or extend a real production texture pipeline
- People who care about authenticity and technical correctness, not just "higher resolution"

This toolkit assumes you are willing to be involved in the process. It rewards skill and attention. It does not try to replace the human — it amplifies them.

---

## Technical Philosophy

The design principles behind Remaster Blaster:

- **Domain knowledge over generic AI**. The tools understand GTA SA (RenderWare TXD structure, IMG formats, typical texture sizes, naming conventions, LOD requirements, player texture quirks).
- **Control over automation**. Automation exists to remove drudgery, not to remove judgment.
- **Recoverability**. Long-running processes can be paused, resumed, and inspected. Databases and logs exist for a reason.
- **Strictness where it matters**. Matching rules in the TXD builder are intentionally strict. Wrong texture swaps are worse than missing textures.
- **Practicality**. Many tools started as solutions to real problems encountered during a massive texture project. They are battle-tested.

---

## Project Structure

```
RemasterBlaster/
├── README.md
├── autorun.py
├── requirements.txt
├── LICENSE
└── tools/
    ├── gta_texture_ai_upscaler/       # Main Flux-based AI upscaler
    ├── uv_reconstruction_studio/      # Advanced UV part reconstruction
    ├── txd_mirror_builder/            # Rebuild TXDs correctly
    ├── qa_tool/                       # High-volume texture QA
    ├── san_resize_tool/               # Fix illegal dimensions
    ├── texture_dimension_fixer/       # Restore original dimensions from logs
    ├── lod_maker/                     # Generate proper LODs
    ├── img_prefix_fixer/              # Fix clean vs prefixed names
    ├── img_texture_checker/           # Inspect IMG contents
    ├── scan_missing/                  # Find missing environment textures
    ├── find_missing_png/              # Size / existence checks
    ├── remove_duplicated/             # Clean already-fixed files
    ├── copy_skipped/                  # Collect failed AI jobs
    ├── filename_keywords/             # Analyze naming patterns
    ├── fast_renamer/                  # Batch renaming
    └── fast_upscaler/                 # Lightweight non-diffusion upscaler
```

Each tool has its own README with specific usage instructions.

---

## Requirements

Most tools require:
```bash
pip install pillow
```

Additional packages used by some tools:
- `customtkinter` (QA Tool, SAN Resize Tool)
- `PyQt6` (UV Reconstruction Studio GUI)
- `numpy`, `opencv-python`, `psutil` (UV Studio)

See individual tool folders for exact requirements.

---

## Important Notes

- Many tools still contain hard-coded paths from the original production environment. Open the Python files and update the paths at the top before running.
- Always work on copies of original game files.
- This is a personal production toolkit that has been cleaned and organized for public release. Further polish and improvements are expected in future updates.
- The AI upscaler expects a properly configured Flux backend. Configuration is documented inside its folder.

---

## Final Words

GTA San Andreas is more than 20 years old. Its textures were created under severe technical limitations. Simply making them higher resolution is not the same as remastering them with respect for the original art direction and technical reality of the game.

Remaster Blaster exists because doing this properly is hard — and because the existing tools were not good enough.

If you are serious about GTA SA texture work, this toolkit was built for you.

---

**Made with obsession for GTA San Andreas texture quality.**

Use it. Improve it. Make something that looks like it belongs in the game.
