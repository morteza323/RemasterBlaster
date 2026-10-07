# IMG Texture Checker

Checks which textures are actually present inside GTA SA / III / VC IMG archives.

Useful for:
- Verifying completeness of your extracted texture pack
- Finding textures that exist in the IMG but are missing from your PNG folder
- Cross-checking after rebuilding TXDs

## How to use

```bash
python img_texture_checker.py
```

Select the IMG file and (optionally) your textures folder. The tool will list matches and missing files.
