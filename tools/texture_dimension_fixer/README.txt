GTA SA Texture Dimension Fixer
===============================

This package was made specifically to undo the damage recorded in 123.txt.

IMPORTANT RULE:
If ONE dimension is already a power of two, the texture was NOT supposed to be
resized. Examples:
    256x512   -> restore 256x512
    512x760   -> restore 512x760
    1024x506  -> restore 1024x506
    1536x1024 -> restore 1536x1024

If NEITHER dimension is a power of two:
    the fixer uniformly scales the current texture so one dimension becomes a
    power of two. Uniform scaling preserves the aspect ratio.

The fixer does NOT:
- crop
- rotate
- stretch width/height independently
- rearrange UVs

NO-BACKUP WARNING:
Because the old files were overwritten, this tool cannot recover the exact original
pixels. It can recover the recorded original dimensions for files that already had
a legal dimension, and it can restore the correct aspect ratio as closely/exactly as
possible for files that originally had no legal dimension.

Before running it on the real folder, run:
    python fix_gta_sa_textures.py "YOUR_FOLDER" --log "123.txt" --dry-run

Then run without --dry-run.

The script automatically creates:
    _dimension_fixer_backup

inside the texture folder before modifying each file.
