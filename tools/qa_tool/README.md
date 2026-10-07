# Texture QA Tool

Production-ready desktop application for reviewing ~20,000+ PNG texture pairs (Original vs Upscaled).

**Features:**
- Manual review with keyboard shortcuts (very fast workflow)
- Auto mode for bulk processing
- Move or copy bad results to a "fucked" folder
- Session auto-save & resume
- Progress tracking

## Keyboard Shortcuts (Manual mode)

| Key | Action |
|-----|--------|
| → / Space / S | Next (no mark) |
| ← | Previous |
| F or Delete | Mark as FUCKED → move/copy → next |
| ↑ / ↓ | Jump ±10 |
| PageUp / PageDown | Jump ±50 |
| Home / End | First / Last |
| Z / Ctrl+Z | Undo |

## How to use

```bash
# GUI
python qa_texture_tool.py

# Headless auto mode
python qa_texture_tool.py -auto
python qa_texture_tool.py -auto --original "D:/orig" --upscaled "D:/up" --fucked "D:/bad"
python qa_texture_tool.py -auto --threshold 50 --copy
```

**Requirements:**
```bash
pip install customtkinter pillow
```
