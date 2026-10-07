# delete_fixed_from_upscaled.py
# Deletes files from "upscaled" that have a matching name in "fucked up fixed with ai"
# Nothing else is touched.

from pathlib import Path

FIXED_DIR = Path(r"F:\gta sa textures\fucked up fixed with ai")
UPSCALED_DIR = Path(r"F:\upscaled")

def main():
    print("=" * 60)
    print("  Delete Flux versions that already have a manual fix")
    print("=" * 60)

    if not FIXED_DIR.exists():
        print(f"[ERROR] Fixed folder not found: {FIXED_DIR}")
        return
    if not UPSCALED_DIR.exists():
        print(f"[ERROR] Upscaled folder not found: {UPSCALED_DIR}")
        return

    # Collect all filenames from the fixed folder (case-insensitive)
    fixed_files = {}
    for f in FIXED_DIR.rglob("*"):
        if f.is_file():
            fixed_files[f.name.lower()] = f.name

    print(f"Files found in fixed folder : {len(fixed_files)}")
    print("-" * 60)

    deleted = 0
    not_found = 0
    errors = 0

    for name_lower, original_name in fixed_files.items():
        # First try exact name, then case-insensitive search
        candidates = list(UPSCALED_DIR.rglob(original_name))
        if not candidates:
            candidates = [
                p for p in UPSCALED_DIR.rglob("*")
                if p.is_file() and p.name.lower() == name_lower
            ]

        if not candidates:
            not_found += 1
            continue

        for target in candidates:
            try:
                target.unlink()
                print(f"[DELETED] {target.relative_to(UPSCALED_DIR)}")
                deleted += 1
            except Exception as e:
                print(f"[ERROR]   Could not delete {target}: {e}")
                errors += 1

    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Successfully deleted     : {deleted}")
    print(f"  Not found in upscaled    : {not_found}")
    print(f"  Errors                   : {errors}")
    print("=" * 60)

    if deleted > 0:
        print("Done. Matching Flux versions have been removed from upscaled.")
    else:
        print("No matching files were found to delete.")
    print()

if __name__ == "__main__":
    main()