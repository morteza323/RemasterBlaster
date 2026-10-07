# IMG Prefix Fixer

**Problem it solves**  
Many textures inside GTA SA IMG archives have clean names in the TXD (e.g. `CJ_bottle3`), but the actual PNG files on disk have prefixes (`player_props_CJ_bottle3.png`, `dyn_objects_tex.png`, etc.).

When you rebuild the TXD, the clean name is required. This tool creates the missing clean-named copies.

**How it works**
1. Reads every TXD inside the selected IMG (supports VER2 / GTA SA and VER1 / III-VC).
2. For every texture name that has **no** exact `tex.png` but has one or more `*_tex.png`:
   - Copies one of the prefixed files into the output folder as `tex.png`.
3. Never modifies the source files.

## How to use

```bash
python img_prefix_fixer.py
```

GUI will open. Select:
- IMG file
- Folder that contains your PNGs
- Output folder for the clean-named copies

Then click Start.
