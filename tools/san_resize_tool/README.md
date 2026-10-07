# SAN Resize Tool (GTA SA Texture Dimension Tool)

**Life-saving tool** for fixing illegal texture dimensions.

GTA SA is picky about texture sizes. Many AI-upscaled or manually-fixed textures end up with non-power-of-two sides or wrong aspect ratios that break the game or look stretched.

This tool:
- Scans a folder
- Detects illegal dimensions
- Resizes them to the nearest legal power-of-two size (2048 / 1024 / 512 / 256 ...) while preserving aspect ratio as much as possible
- Supports backup and dry-run modes
- Has both modern GUI (CustomTkinter) and CLI

## How to use

```bash
# GUI
python san_resize_tool.py

# CLI examples
python san_resize_tool.py --cli --folder "H:\textures" --scan
python san_resize_tool.py --cli --folder "H:\textures" --fix
python san_resize_tool.py --cli --folder "H:\textures" --fix --dry-run
```

**Requirements:**
```bash
pip install customtkinter pillow
```
