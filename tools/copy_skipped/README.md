# Copy Skipped to Need-to-Fix

**What it does**  
Reads the jobs database (`jobs.db`) of the GTA Texture AI Upscaler and copies all textures that were marked as **SKIPPED** or **FAILED** into a "need to fix" folder.

It automatically ignores LOD textures and files that already exist in your "fixed with AI" folder.

**Why it's useful**  
After a long AI upscale run you usually have hundreds of failed/skipped files. This tool collects them in one place so you can fix them manually or with another method.

## How to use

1. Make sure the AI Upscaler has run at least once (so `jobs.db` exists).
2. Edit the paths at the top of `copy_skipped_to_need_to_fix.py` if needed:
   ```python
   ORIGINAL_DIR   = Path(r"H:\gta sa textures\original")
   NEED_TO_FIX_DIR = Path(r"H:\gta sa textures\need to fix")
   FIXED_DIR      = Path(r"H:\gta sa textures\fucked up fixed with ai")
   ```
3. Run:
   ```bash
   python copy_skipped_to_need_to_fix.py
   ```
   Or pass the database path manually:
   ```bash
   python copy_skipped_to_need_to_fix.py "full\path\to\jobs.db"
   ```

The script will try several common locations for `jobs.db` automatically.
