# LOD Maker

Automatically generates **LOD textures** for GTA SA from your high-resolution upscaled textures.

**Logic:**
1. Indexes every PNG in Original and Upscaled folders.
2. Detects LOD files (name contains `lod` as token/substring).
3. For each missing or low-res LOD:
   - Finds the best matching high-res source (exact core name, `*_core`, or contains core).
   - Downscales it (default 1/4) and snaps sides to power-of-two (32–512).
   - Saves under the original LOD filename.

## How to use

**GUI:**
```bash
python lod_maker.py
```

**CLI:**
```bash
python lod_maker.py "H:\original_textures" "H:\upscaled" "H:\upscaled_lods"
```

Requires: `pip install Pillow`
