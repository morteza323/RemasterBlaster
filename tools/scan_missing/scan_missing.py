import os
from pathlib import Path

# ==================== تنظیمات ====================
ORIGINAL_FOLDER = "original"
FIXED_FOLDER = "fucked up fixed with ai"
OUTPUT_FILE = "missing_textures.txt"

# کلیدواژه‌های مهم مربوط به جاده، خاک، سنگ، محیط GTA SA
KEYWORDS = [
    # === جاده و آسفالت ===
    "road", "roads", "asphalt", "asph", "tarmac", "tar", "highway", "freeway",
    "street", "streets", "avenue", "blvd", "boulevard", "lane", "drive",
    "pavement", "pave", "sidewalk", "sidewalks", "curb", "kerb", "crosswalk",
    "crossing", "intersection", "junction", "overpass", "underpass", "bridge",
    "roadside", "roadway", "carriage", "motorway", "expressway", "turnpike",
    "midtrack", "sidetrack", "trackroad", "roadmark", "roadline", "roadsign",
    "yellowline", "whiteline", "centerline", "dashed", "solidline",
    "laeroad", "lawroad", "sfroad", "vegasroad", "desroad", "csroad",
    "cunteroad", "cuntwroad", "countryroad", "dirtroad", "gravelroad",
    "roadblend", "roadpulse", "roadcrash", "roadblank", "road2", "road3",
    "road7", "nn_road", "sa_road", "dt_road", "ind_road", "ws_road",
    "kart_asph", "sv_asph", "znam_asfalt", "znam_road", "edovo_asphalt",
    "edovo_asfalt", "grnd_asphalt", "auto_asphalt", "asfasv", "astoas",
    "bowattoboir", "dirtoas", "perroad", "wolv_roads", "rog2gar_road",
    "ruscount_road", "bysaevo", "scheben", "sheben", "graviy",

    # === خاک و گل ===
    "dirt", "dirty", "mud", "muddy", "soil", "earth", "ground", "grounds",
    "track", "tracks", "path", "paths", "trail", "trails", "footpath",
    "dirttrack", "dirtroad", "mudtrack", "wasteground", "wasteland",
    "woodland", "forest", "jungle", "field", "fields", "meadow", "pasture",
    "flowerbed", "flower_bed", "cornfield", "corn", "marsh", "swamp",
    "riverbed", "riveredge", "riverbank", "bank", "shore", "shoreline",
    "dump", "junkyard", "rubble", "debris", "litter", "rubbish",
    "desdirt", "csdirt", "ladirt", "sfdirt", "vegdirt", "cuntwland",
    "groundmix", "groundblend", "special_ground", "land_p", "land_a",
    "pesok", "setunka", "dnodirt", "dnotograss", "sandplace", "sandhole",
    "dirtrocky", "dirtweed", "weeds", "stumps", "sticks", "leaves",
    "sparse", "dense", "lush", "dry", "wet", "rocky", "arid",

    # === سنگ و صخره ===
    "stone", "stones", "rock", "rocks", "rocky", "cliff", "cliffs",
    "boulder", "boulders", "pebble", "pebbles", "gravel", "gravely",
    "rubble", "rubblepile", "mountain", "mountains", "hill", "hills",
    "crag", "ledge", "outcrop", "bedrock", "slate", "granite", "basalt",
    "desrock", "csrock", "rockwall", "rockface", "rocktype", "global_rock",
    "undw_stone", "stone3", "cliff_rel", "tailing", "quarry", "mine",
    "cuntrock", "cunte_rocks", "gtarock", "desertrock", "sandrock",

    # === بتن و سیمان ===
    "concrete", "conc", "cement", "paving", "paver", "tile", "tiles",
    "slab", "slabs", "block", "blocks", "brick", "bricks", "masonry",
    "pavement", "sidewalk", "curbstone", "kerbstone", "gutter",
    "bridgeconc", "airportgnd", "dockland", "industrial", "jetty",
    "poolside", "alley", "floor_concrete", "paintedground", "painted",
    "kart_beton", "mp_conc", "conchev", "concrete_64", "beton",
    "ws_carpark", "trainstation", "pereezd", "metpat",

    # === شن و ماسه ===
    "sand", "sandy", "beach", "beaches", "dune", "dunes", "desert",
    "deserts", "arid", "dryland", "wasteland", "barren",
    "sanddeep", "sandmedium", "sandcompact", "sandbeach", "sandrocky",
    "sanddense", "underwater", "coral", "seaweed", "cactus",
    "des_sand", "cs_sand", "la_sand", "sf_sand", "veg_sand",
    "new_sand", "sv_peso", "pesok", "sandplace", "sandhole",
    "roadsidedes", "desertrock", "cactusdense",

    # === چمن و گیاه ===
    "grass", "grassy", "lawn", "turf", "meadow", "pasture", "field",
    "golf", "park", "garden", "vegetation", "veg", "plant", "plants",
    "bush", "bushes", "hedge", "hedges", "shrub", "shrubs", "weed", "weeds",
    "flower", "flowers", "fern", "ferns", "rush", "rushes",
    "grasslush", "grassdry", "grassshort", "grasslong", "grassmed",
    "grassrocky", "grassmix", "grassdirt", "parkgrass", "golfgrass",
    "steepgrass", "slidy", "wee_flowers", "tallgrass", "lowgrass",
    "gta_proc", "gta_grass", "proc_grass", "proc_bush", "proc_fern",
    "bow_church_grass", "church_grass",

    # === محیط کلی و زمین ===
    "ground", "grounds", "floor", "floors", "surface", "surfaces",
    "terrain", "landscape", "land", "lands", "area", "zone",
    "hub", "hubs", "plaza", "square", "courtyard", "yard",
    "parking", "carpark", "lot", "lots", "driveway", "driveways",
    "alley", "alleys", "backyard", "frontyard", "patio",
    "airport", "runway", "tarmac", "apron", "taxiway",
    "dock", "docks", "harbor", "harbour", "pier", "wharf", "quay",
    "railroad", "railway", "rail", "rails", "track", "tracks",
    "train", "station", "platform",

    # === پیشوندها و نام‌های رایج TXD/تکسچر GTA SA ===
    "des_", "cs_", "lae", "law", "sf_", "vegas", "veg_", "cunt",
    "country", "countrys", "countryn", "countrye", "countryw",
    "la_", "sf_", "lv_", "ls_", "sfw", "sfs", "sfx", "sfse",
    "lahills", "lahill", "las", "las2", "laf", "lan", "law2",
    "ce_", "ce_ground", "ce_land", "ce_road", "ce_dirt",
    "ws_", "ws_road", "ws_carpark", "ws_rooftarmac",
    "bow_", "bow_abpave", "bow_church",
    "cxref", "cxrf", "cuntw", "cunte", "cunts",
    "desn2", "des2", "desert", "desertdam", "desertroads",
    "airport", "airroad", "airp_", "airprtrunway",
    "landhub", "lae2road", "lae2roads", "land_",
    "freeway", "freeways", "highway", "highways",
    "barrier", "barriers", "fence", "fences",
    "wall", "walls", "brickwall", "stonewall", "rockwall",
    "roof", "roofs", "rooftop", "rooftarmac",
    "floor", "floors", "carpet", "tile", "tiles",
    "metal", "sheetmetal", "steel", "iron",
    "wood", "wooden", "plank", "planks", "board", "boards",
    "glass", "window", "windows", "door", "doors",

    # === کلمات اضافی مرتبط با سطح و متریال ===
    "surface", "material", "mat_", "tex_", "texture",
    "blend", "blends", "mix", "mixed", "overlay",
    "detail", "details", "alpha", "mask", "normal",
    "bump", "specular", "diffuse", "albedo",
    "wet", "dry", "damaged", "fucked", "worn", "old", "new",
    "clean", "dirty", "muddy", "dusty", "sandy", "rocky",
    "smooth", "rough", "coarse", "fine", "cracked", "broken",
    "painted", "unpainted", "marked", "unmarked",
    "yellow", "white", "black", "gray", "grey", "brown", "red",
    "green", "blue", "dark", "light", "mid", "medium",
]

def normalize_name(filename: str) -> str:
    """اسم فایل رو برای مقایسه نرمال می‌کنه (بدون پسوند و حروف کوچک)"""
    return Path(filename).stem.lower()

def has_keyword(filename: str, keywords: list) -> bool:
    """چک می‌کنه آیا اسم فایل شامل یکی از کلیدواژه‌ها هست یا نه"""
    name = normalize_name(filename)
    for kw in keywords:
        if kw.lower() in name:
            return True
    return False

def collect_pngs(folder: str) -> dict:
    """
    همه فایل‌های PNG داخل پوشه و زیرپوشه‌ها رو جمع می‌کنه
    برمی‌گردونه: {normalized_name: full_path}
    """
    result = {}
    folder_path = Path(folder)
    
    if not folder_path.exists():
        print(f"[خطا] پوشه پیدا نشد: {folder}")
        return result
    
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith(".png"):
                full_path = Path(root) / file
                norm = normalize_name(file)
                # اگر چند فایل با اسم یکسان باشن، اولی رو نگه می‌داریم
                if norm not in result:
                    result[norm] = str(full_path)
    
    return result

def main():
    print("=" * 60)
    print("اسکنر تکسچرهای جاده / خاک / سنگ GTA SA")
    print("=" * 60)
    
    print(f"\n[1] در حال اسکن پوشه: {ORIGINAL_FOLDER}")
    original_files = collect_pngs(ORIGINAL_FOLDER)
    print(f"    → {len(original_files)} فایل PNG پیدا شد")
    
    print(f"\n[2] در حال اسکن پوشه: {FIXED_FOLDER}")
    fixed_files = collect_pngs(FIXED_FOLDER)
    print(f"    → {len(fixed_files)} فایل PNG پیدا شد")
    
    print(f"\n[3] فیلتر کردن فایل‌های مرتبط با کلیدواژه‌ها...")
    original_relevant = {
        name: path for name, path in original_files.items()
        if has_keyword(name, KEYWORDS)
    }
    fixed_relevant = {
        name: path for name, path in fixed_files.items()
        if has_keyword(name, KEYWORDS)
    }
    
    print(f"    → در original: {len(original_relevant)} فایل مرتبط")
    print(f"    → در fucked up fixed: {len(fixed_relevant)} فایل مرتبط")
    
    print(f"\n[4] پیدا کردن فایل‌های گم‌شده...")
    missing = []
    for name, path in sorted(original_relevant.items()):
        if name not in fixed_relevant:
            missing.append((name, path))
    
    print(f"    → {len(missing)} فایل مرتبط در original هست ولی در fixed نیست")
    
    # نوشتن نتیجه
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write("فایل‌های مرتبط با جاده/خاک/سنگ/محیط که در 'fucked up fixed' موجود نیستن\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"تعداد کل: {len(missing)}\n")
        f.write(f"تاریخ اسکن: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("-" * 70 + "\n\n")
        
        if not missing:
            f.write("هیچ فایل گم‌شده‌ای پیدا نشد! همه چیز اوکیه.\n")
        else:
            for i, (name, path) in enumerate(missing, 1):
                f.write(f"{i:4d}. {name}.png\n")
                f.write(f"      مسیر: {path}\n\n")
    
    print(f"\n[✓] نتیجه در فایل ذخیره شد: {OUTPUT_FILE}")
    print("=" * 60)

if __name__ == "__main__":
    main()