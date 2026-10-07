# Remaster Blaster

**Complete toolkit for upscaling, remastering, fixing and rebuilding GTA San Andreas texture packs.**

This is a collection of specialized tools I built while creating one of the most complete texture packs for GTA SA.  
Every tool gives you **full control** over the result — nothing is fully automatic black-box.

---

## Project Structure

```
RemasterBlaster/
├── README.md                          ← You are here
├── autorun.py                         ← Simple launcher (optional)
├── tools/
│   ├── gta_texture_ai_upscaler/       ← Main AI upscaler (Flux-based)
│   ├── uv_reconstruction_studio/      ← Advanced UV part reconstruction
│   ├── txd_mirror_builder/            ← Rebuild TXD files from PNG pack
│   ├── qa_tool/                       ← Manual + auto QA for thousands of textures
│   ├── san_resize_tool/               ← Fix illegal dimensions / aspect ratio
│   ├── texture_dimension_fixer/       ← Restore original power-of-two dimensions
│   ├── lod_maker/                     ← Generate LOD textures from high-res
│   ├── img_prefix_fixer/              ← Fix missing clean names for IMG/TXD
│   ├── img_texture_checker/           ← Check which textures exist inside IMG
│   ├── scan_missing/                  ← Find missing textures by keywords
│   ├── find_missing_png/              ← Scan PNG sizes / missing files
│   ├── remove_duplicated/             ← Remove already-fixed files from folders
│   ├── copy_skipped/                  ← Copy SKIPPED/FAILED jobs to "need to fix"
│   ├── filename_keywords/             ← Analyze common prefixes & suffixes
│   ├── fast_renamer/                  ← Fast batch renamer
│   └── fast_upscaler/                 ← Lightweight non-diffusion upscaler
```

---

## Recommended Workflow (GTA SA Texture Remaster)

1. **Extract** original textures from `gta3.img`, `gta_int.img`, `player.img`, etc.
2. **Upscale** with `gta_texture_ai_upscaler` (Flux) or `fast_upscaler`.
3. **Manual fix** bad results (ChatGPT / Photoshop / etc.) → put them in a "fixed with AI" folder.
4. Use **remove_duplicated** to clean the upscaled folder from files you already fixed manually.
5. Use **copy_skipped** to collect failed/skipped jobs into a "need to fix" folder.
6. **Fix dimensions** with `san_resize_tool` or `texture_dimension_fixer`.
7. Generate missing **LODs** with `lod_maker`.
8. Fix naming issues with `img_prefix_fixer` and `filename_keywords`.
9. **QA** everything with `qa_tool`.
10. Rebuild final TXDs with `txd_mirror_builder`.
11. (Advanced) Use **UV Reconstruction Studio** for complex UV layouts that need part-by-part reconstruction.

---

## Tools Overview

### 1. GTA Texture AI Upscaler (`tools/gta_texture_ai_upscaler/`)
Main production upscaler based on Flux (img2img).  
Features fine-grained filename categories (~90 categories), color lock, fidelity system, safety guards (temperature, RAM, VRAM), resume support, and path mirroring.

**How to run:**
```bash
cd tools/gta_texture_ai_upscaler
# Edit config if needed
start.bat          # Windows
# or
python main.py
```

See its own `README.md` for full details.

---

### 2. UV Reconstruction Studio (`tools/uv_reconstruction_studio/`)
Advanced tool for splitting UV layouts into parts, reconstructing/enhancing each part with AI engines (Flux / Real-ESRGAN / mock), and reassembling.

Has both GUI (PyQt6) and full CLI + 176 automated tests.

```bash
cd tools/uv_reconstruction_studio
pip install -r requirements.txt
python -m ui.app          # GUI
python main.py --help     # CLI
```

---

### 3. TXD Mirror Builder (`tools/txd_mirror_builder/`)
Rebuilds TXD files inside GTA SA IMG archives by replacing textures with your upscaled PNGs while preserving format (DXT1/3/5, XRGB32, ARGB8888), mipmaps rules, and exact structure.

- `other_img/` → for gta3.img, gta_int.img, cutscene, etc.
- `player_img/` → specialized version for player.img (faces, hair, clothes)

Very strict matching to avoid wrong texture swaps.

---

### 4. QA Tool (`tools/qa_tool/`)
Desktop app (CustomTkinter) for reviewing thousands of texture pairs (original vs upscaled).

- Manual review with keyboard shortcuts
- Auto mode
- Move/copy bad results to "fucked" folder
- Session resume

```bash
cd tools/qa_tool
python qa_texture_tool.py
python qa_texture_tool.py -auto
```

---

### 5. SAN Resize Tool (`tools/san_resize_tool/`)
Simple but life-saving tool.  
Scans a folder and finds textures with illegal dimensions or wrong aspect ratio for GTA SA, then resizes them to nearest legal power-of-two size (2048, 1024, 512, ...) while preserving aspect ratio as much as possible. Supports backup and dry-run.

```bash
cd tools/san_resize_tool
python san_resize_tool.py
```

---

### 6. Texture Dimension Fixer (`tools/texture_dimension_fixer/`)
Restores original dimensions recorded in a log file (`123.txt`).  
Useful when previous tools incorrectly resized textures that already had one power-of-two side.

---

### 7. LOD Maker (`tools/lod_maker/`)
Generates LOD textures by downscaling high-resolution upscaled versions.  
Finds matching non-LOD sources and creates proper lower-resolution LOD files with power-of-two sides.

```bash
cd tools/lod_maker
python lod_maker.py
```

---

### 8. IMG Prefix Fixer (`tools/img_prefix_fixer/`)
Reads texture names from TXDs inside IMG archives.  
When a clean name (`tex.png`) is missing but a prefixed version exists (`something_tex.png`), it creates a clean copy. Essential for correct placement inside IMG.

---

### 9. IMG Texture Checker (`tools/img_texture_checker/`)
Checks which textures actually exist inside IMG archives and helps verify completeness of your pack.

---

### 10. Scan Missing (`tools/scan_missing/`)
Scans folders and finds missing textures using a large curated list of GTA SA-related keywords (roads, dirt, grass, concrete, etc.). Very useful for finding gaps in environment textures.

---

### 11. Find Missing PNG (`tools/find_missing_png/`)
Utility to scan PNG sizes and detect missing or wrong-sized files.

---

### 12. Remove Duplicated (`tools/remove_duplicated/`)
Compares a "fixed with AI / manual" folder against an "upscaled" folder and deletes the Flux/AI versions that you have already fixed manually. Leaves only the ones that still need work.

---

### 13. Copy Skipped (`tools/copy_skipped/`)
Reads the jobs database of the AI Upscaler and copies all SKIPPED / FAILED textures (excluding LODs) into a "need to fix" folder so you can work on them.

---

### 14. Filename Keywords (`tools/filename_keywords/`)
Two tools:
- `filename_keywords_first.py` → find common prefixes / first words
- `filename_keywords_last.py` → find common suffixes / endings

Extremely useful for understanding naming patterns and building better category systems for the AI upscaler.

---

### 15. Fast Renamer (`tools/fast_renamer/`)
Simple and fast batch renamer.

---

### 16. Fast Upscaler (without diffusion) (`tools/fast_upscaler/`)
Lightweight upscaler that does **not** use diffusion models. Faster alternative when you don't need AI generation quality.

---

## Requirements (Global)

Most tools need:
```bash
pip install pillow
```

Some tools need extra packages:
- `customtkinter` → QA Tool, SAN Resize Tool
- `PyQt6` → UV Reconstruction Studio (GUI)
- `numpy`, `opencv-python`, `psutil` → UV Studio

See each tool's own `README.md` or `requirements.txt`.

---

## Notes for GitHub Users

- Many tools still contain **hard-coded Windows paths** (from my personal workflow).  
  Open the `.py` files and change the paths at the top before running.
- Some tools are GUI-first, some are CLI-first. Most support both.
- This is a **personal production toolkit** that was cleaned and organized for public release.  
  Expect further improvements and cleaner versions in future commits.

---

## License

Do whatever you want with the tools.  
If you use them in a public texture pack, a small credit is appreciated but not required.

---

**Made with obsession for GTA SA texture quality.**
