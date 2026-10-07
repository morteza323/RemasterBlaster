# Texture Dimension Fixer

Restores original dimensions of textures using a log file (`123.txt`) that recorded previous (incorrect) resizes.

**Important rule implemented:**
- If **one** dimension is already a power of two → restore the original recorded size.
- If **neither** dimension is power of two → uniformly scale so one side becomes power of two (preserves aspect ratio).

Never crops, rotates, or stretches independently.

## How to use

```bash
# Dry run first (recommended)
python fix_gta_sa_textures.py "YOUR_FOLDER" --log "123.txt" --dry-run

# Real run
python fix_gta_sa_textures.py "YOUR_FOLDER" --log "123.txt"
```

Creates automatic backup folder `_dimension_fixer_backup` before modifying files.
