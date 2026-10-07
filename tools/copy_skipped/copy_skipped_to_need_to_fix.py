# copy_skipped_to_need_to_fix.py
# Copies SKIPPED / FAILED textures (excluding LOD and already-fixed ones)
# from "original" into "need to fix"

from pathlib import Path
import sqlite3
import shutil
import sys

ORIGINAL_DIR   = Path(r"H:\gta sa textures\original")
NEED_TO_FIX_DIR = Path(r"H:\gta sa textures\need to fix")
FIXED_DIR      = Path(r"H:\gta sa textures\fucked up fixed with ai")

# Possible locations for jobs.db (will try them in order)
POSSIBLE_DB_PATHS = [
    Path(r"H:\gta sa textures\GTA-Texture-AI-Upscaler-V6-RECONSTRUCTION\GTA-Texture-AI-Upscaler-v4_2_2\GTA-Texture-AI-Upscaler-v4.2.2\data\jobs\jobs.db"),
    Path(r"H:\gta sa textures\GTA-Texture-AI-Upscaler-v4.2.2\data\jobs\jobs.db"),
    Path(r"H:\gta sa textures\data\jobs\jobs.db"),
    Path(r".\data\jobs\jobs.db"),
    Path(r".\jobs.db"),
    Path(r"H:\gta sa textures\jobs.db"),
]

def find_database() -> Path | None:
    """Try to locate jobs.db automatically."""
    for p in POSSIBLE_DB_PATHS:
        if p.exists():
            return p

    # Last resort: search under H:\gta sa textures
    search_root = Path(r"H:\gta sa textures")
    if search_root.exists():
        matches = list(search_root.rglob("jobs.db"))
        if matches:
            # Prefer the one inside a "data/jobs" folder
            for m in matches:
                if "data" in m.parts and "jobs" in m.parts:
                    return m
            return matches[0]
    return None

def is_lod(filename: str, last_error: str | None) -> bool:
    name = filename.lower()
    if "lod" in name:
        return True
    if last_error and "LOD texture" in last_error:
        return True
    return False

def main():
    print("=" * 65)
    print("  Copy SKIPPED / FAILED textures to 'need to fix'")
    print("=" * 65)

    db_path = find_database()
    if db_path is None:
        print("[ERROR] Could not find jobs.db")
        print("Please put jobs.db next to this script or update POSSIBLE_DB_PATHS.")
        print()
        print("You can also run the script like this:")
        print('  python copy_skipped_to_need_to_fix.py "full\\path\\to\\jobs.db"')
        sys.exit(1)

    # Allow override from command line
    if len(sys.argv) > 1:
        db_path = Path(sys.argv[1])
        if not db_path.exists():
            print(f"[ERROR] Database not found: {db_path}")
            sys.exit(1)

    print(f"Using database : {db_path}")
    print("-" * 65)

    if not ORIGINAL_DIR.exists():
        print(f"[ERROR] Original folder not found: {ORIGINAL_DIR}")
        return

    NEED_TO_FIX_DIR.mkdir(parents=True, exist_ok=True)

    # Collect already-fixed filenames
    fixed_names = set()
    if FIXED_DIR.exists():
        fixed_names = {f.name.lower() for f in FIXED_DIR.rglob("*") if f.is_file()}
    print(f"Already fixed files : {len(fixed_names)}")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT relative_path, status, last_error
        FROM jobs
        WHERE status IN ('SKIPPED', 'FAILED')
    """).fetchall()
    conn.close()

    print(f"Total SKIPPED + FAILED records : {len(rows)}")
    print("-" * 65)

    copied = 0
    skipped_lod = 0
    skipped_fixed = 0
    not_found = 0
    already_exists = 0

    for rel_path, status, last_error in rows:
        filename = Path(rel_path).name

        # Skip LOD
        if is_lod(filename, last_error):
            skipped_lod += 1
            continue

        # Skip if already in fixed folder
        if filename.lower() in fixed_names or (last_error and "already in fixed-with-ai folder" in str(last_error)):
            skipped_fixed += 1
            continue

        # Locate source file
        src = ORIGINAL_DIR / filename
        if not src.exists():
            candidates = list(ORIGINAL_DIR.rglob(filename))
            if candidates:
                src = candidates[0]
            else:
                print(f"[MISSING] {filename}")
                not_found += 1
                continue

        dest = NEED_TO_FIX_DIR / filename
        if dest.exists():
            already_exists += 1
            continue

        try:
            shutil.copy2(src, dest)
            print(f"[COPIED]  {filename}  ({status})")
            copied += 1
        except Exception as e:
            print(f"[ERROR]   Failed to copy {filename}: {e}")

    print("=" * 65)
    print("  SUMMARY")
    print("=" * 65)
    print(f"  Successfully copied to 'need to fix' : {copied}")
    print(f"  Skipped (LOD)                        : {skipped_lod}")
    print(f"  Skipped (already fixed)              : {skipped_fixed}")
    print(f"  Already existed in target            : {already_exists}")
    print(f"  Not found in original                : {not_found}")
    print("=" * 65)

    if copied > 0:
        print("Done. Eligible skipped/failed textures have been copied.")
    else:
        print("No new files needed to be copied.")
    print()

if __name__ == "__main__":
    main()