# GTA Texture AI Upscaler v5.4

Flux img2img remaster with **~90 fine-grained filename categories**.

## v5.4 (color fix)
- **CRITICAL FIX**: raw filename tokens are NO LONGER injected into the prompt.
  Names like `bloodra` / `bloodrb` were being read as “blood” → red splatters & wrong recolors.
- Only the safe category label is used (vehicle body, checker pattern, asphalt, …).
- Stronger color lock: grayscale / B&W / two-tone stay exactly that color.
- Explicit bans: no blood, no splatters, no invented stains, no recolor.
- Checker patterns get their own rule so they stay black-and-white.
- RealESRGAN still off by default (speed + small files ~0.5–1 MB).
- Roads / roofs / CJ house / skins: program can process them; re-do at 4K manually if you want higher quality.

## Start
```cmd
start.bat
```
First run will re-encode the master prompt (one-time). Then continues from where you left off.

## V5.4.1 fidelity system
- Keeps the generation output at a maximum 512px long side.
- Uses Flux 4B at 6 steps (unchanged).
- Removes the old global contrast/saturation post-sauce that could wash or recolor textures.
- Adds source-anchored multi-scale fusion: original low-frequency color/layout is retained while Flux high-frequency detail is imported for realism.
- Preserves original alpha with nearest-neighbor scaling.
- Base strength is 0.25; UV-critical assets are capped lower for safety.
