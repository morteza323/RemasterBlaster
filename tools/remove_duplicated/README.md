# Remove Duplicated (Delete Already-Fixed Files)

**What it does**  
Compares two folders:
- `FIXED_DIR` → textures you fixed manually (or with ChatGPT)
- `UPSCALED_DIR` → the big folder produced by the AI upscaler

It deletes every file from the Upscaled folder that already has a matching name in the Fixed folder.

**Result:**  
Only the textures that still need work remain in the Upscaled / "fucked up" folder.

## How to use

1. Edit the two paths at the top of `remove_duplicated.py`:
   ```python
   FIXED_DIR = Path(r"F:\gta sa textures\fucked up fixed with ai")
   UPSCALED_DIR = Path(r"F:\upscaled")
   ```
2. Run:
   ```bash
   python remove_duplicated.py
   ```

**Warning:** This permanently deletes files from the Upscaled folder. Make a backup first if you are unsure.
