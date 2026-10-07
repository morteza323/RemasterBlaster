# GTA SA TXD Mirror Builder

Rebuilds TXD files inside GTA San Andreas IMG archives by injecting your upscaled PNG textures while preserving:

- Exact format (DXT1 / DXT3 / DXT5 / XRGB32 / ARGB8888)
- Mipmap rules
- Structure and headers
- Unmatched textures stay byte-for-byte unchanged

## Two specialized versions

### `other_img/gta_sa_txd_mirror_other.py`
For `gta3.img`, `gta_int.img`, cutscene archives, etc.

### `player_img/gta_sa_txd_mirror_player.py`
Specialized for `player.img` (faces, beards, hair, clothes) with correct handling of uncompressed formats (X8R8G8B8 / A8R8G8B8).

## Matching rules (very strict)
1. Exact stem match
2. Doubled name (`texture_texture`)
3. Contextual (`{txd_stem}_{texture}`)
4. Suffix match (`*_{texture}`)

No fuzzy matching → prevents wrong texture swaps.

## How to use

1. Put your upscaled PNGs in a folder.
2. Run the appropriate script.
3. Point it to the original IMG and your PNG folder.
4. Check the generated log files (`_used_pngs_*.txt`, `_unused_pngs_*.txt`, etc.).

**Important:** Always work on a **copy** of the original IMG files.
