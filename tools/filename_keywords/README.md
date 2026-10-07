# Filename Keywords Analyzer

Two small GUI tools to discover naming patterns in your texture pack.

## 1. filename_keywords_first.py
Finds the most common **prefixes / first words** in filenames.

Useful for:
- Understanding how Rockstar named textures
- Building better category systems for the AI upscaler
- Finding groups of related textures

## 2. filename_keywords_last.py
Finds the most common **suffixes / endings** in filenames.

Example:
```
vgnamun_blueroof_64.png
vgnhseing1_blueroof_64.png
vgnretail5_blueroof_64.png
```
All share the ending `blueroof_64.png`.

## How to use

```bash
python filename_keywords_first.py
# or
python filename_keywords_last.py
```

1. Click **Choose Folder**
2. Optionally enable recursive scan
3. Set minimum count
4. Click on any keyword to see the list of files that use it

Requires only Python 3 + tkinter (included with standard Python on Windows).
