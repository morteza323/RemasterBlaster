"""
Infer material / texture type from GTA SA filename – v5.3 image-first safety
~90 fine-grained categories, most-specific first.

v3.7 FIX (critical):
- NEVER inject raw filename tokens into the prompt.
  Words like blood / bloodra / bloodrb / gore / dirty were being
  interpreted literally by the model → red blood splatters, wrong
  recolors, etc.
- Only the category label + strong color lock are used.
- Extra lock: if the source looks grayscale / B&W, stay grayscale.
- Checker pattern gets its own safe rule.
"""

from __future__ import annotations

import re
from pathlib import Path
from .knowledge_base import format_context
from typing import Tuple

# (compiled_regex, short_label, detail_phrase_for_prompt)
# ORDER MATTERS — first match wins.
_RULES: list[tuple] = [

    # ========== LOD ==========
    (re.compile(r"(^|_)lod(_|$)|lod\d|welod|wshxreflod|_lod", re.I),
     "low detail LOD texture",
     "low-detail distant LOD texture, keep exact colors and simple pattern, do not invent fine detail"),

    # ========== v5.0.1 GTA SA expanded props (checked BEFORE broad catch-alls below) ==========
    # Manhole must win over the later generic "drain" match inside vent/grate.
    (re.compile(r"manhole|drain.?cover|sewercover", re.I),
     "manhole cover texture",
     "flat top-down manhole or drain cover texture, keep exact circular/square shape and casting pattern, do not invent a hole or 3D depth"),
    (re.compile(r"trafficlight|traflight|stoplight", re.I),
     "traffic light texture",
     "traffic light housing and lenses, preserve exact red/amber/green lens colors and casing color"),
    (re.compile(r"streetlamp|lamphead|lampshade|lightfixture|floodlight", re.I),
     "street lamp fixture texture",
     "street lamp or floodlight fixture surface, preserve exact metal/glass color and shape"),
    (re.compile(r"parkmeter|parkingmeter", re.I),
     "parking meter texture",
     "parking meter body surface, keep exact colors, dials and text"),
    (re.compile(r"mailbox|postbox", re.I),
     "mailbox texture",
     "mailbox or postbox surface, keep exact color and markings"),
    (re.compile(r"hydrant", re.I),
     "fire hydrant texture",
     "fire hydrant surface, preserve exact color (often red/yellow) and cap shapes"),
    (re.compile(r"(^|_)atm(_|s|$)|cashmachine", re.I),
     "ATM machine texture",
     "ATM machine front panel, keep exact screen keypad and casing colors, no invented reflections"),
    (re.compile(r"vend(ing)?mach|vendmach", re.I),
     "vending machine texture",
     "vending machine front panel, keep exact product graphics and colors, no rewritten text"),
    (re.compile(r"payphone|telephonebox|phonebooth", re.I),
     "payphone texture",
     "payphone or phone booth surface, keep exact colors and panel layout"),
    (re.compile(r"busstop|busshelter|bus.?shelter", re.I),
     "bus stop shelter texture",
     "bus stop shelter glass/metal surface, keep exact panel layout and ad graphics"),
    (re.compile(r"gaspump|fuelpump|pumphead|petrolpump", re.I),
     "gas pump texture",
     "fuel pump body surface, keep exact brand colors, panels and hose"),
    (re.compile(r"gasstation|fuelcanopy|petrolcanopy|gas.?canopy", re.I),
     "gas station canopy texture",
     "gas station canopy fascia surface, keep exact brand colors and panel lines"),
    (re.compile(r"trainrail|traintrack|(^|_)rails?(_|$)", re.I),
     "train track texture",
     "rail track and sleeper ground texture, preserve exact metal rail color and sleeper spacing"),
    (re.compile(r"traincar|traincarriage|boxcar|freightcar|traintrain|subwaycar", re.I),
     "train carriage body texture",
     "flat train/subway carriage body panel texture, keep exact panel lines livery text and colors, do not invent a full 3D train"),
    (re.compile(r"bridge", re.I),
     "bridge structure texture",
     "bridge structural surface (girder, deck or support), preserve exact dark metal/concrete tone and geometry, do not bleach to light gray"),
    (re.compile(r"pierleg|pierpile|jettyleg|legbot|legtop|pierpost", re.I),
     "pier piling multi-band texture",
     "vertical pier support / piling texture made of distinct stacked bands (sky, water, wet wood/concrete, sand) — "
     "preserve each band's exact original color and boundary position, do not convert any band into asphalt, "
     "uniform gravel, or sand; do not blur the band edges together"),
    (re.compile(r"boardwalk|beachwalk|jettywalk|pierdeck", re.I),
     "boardwalk walkway texture",
     "elevated walkway surface (wood planks or concrete, matching whatever the input already shows), "
     "preserve exact plank/panel lines and original color, do not force it into sand or generic pavement"),
    (re.compile(r"(^|_)dock(?!ing)|jetty(?!leg)", re.I),
     "dock or jetty texture",
     "dock or jetty wood/concrete surface, preserve exact material and color as shown in the input"),
    (re.compile(r"boathull|yacht\d*|dinghy|vessel(?!s)", re.I),
     "boat hull texture",
     "flat boat hull panel texture, keep exact hull color, waterline and panel lines, do not invent a full 3D boat render"),
    (re.compile(r"fuselage|airplane|aeroplane|jetbody|jumbojet", re.I),
     "airplane fuselage texture",
     "flat airplane fuselage panel texture, keep exact livery colors, panel lines and rivets, do not invent a full 3D aircraft render"),
    (re.compile(r"heli(?!x)\w*body|chopper|helitex", re.I),
     "helicopter body texture",
     "flat helicopter body panel texture, keep exact livery colors and panel lines, do not invent a full 3D helicopter render"),
    (re.compile(r"hangar", re.I),
     "hangar wall texture",
     "aircraft hangar wall or door surface, preserve exact corrugation and color"),
    (re.compile(r"runway|airstrip|tarmac.?mark", re.I),
     "runway marking texture",
     "airport runway or taxiway asphalt with markings, preserve exact marking color/position and asphalt grain"),
    (re.compile(r"stadium|bleacher|grandstand", re.I),
     "stadium seating texture",
     "stadium seating or bleacher surface, preserve exact seat color rows and pattern"),
    (re.compile(r"poolside|pooltile|swimpool|swimmingpool", re.I),
     "pool tile texture",
     "swimming pool tile or coping surface, preserve exact tile color and grid, keep water areas blue as in input"),
    (re.compile(r"gymequip|treadmill|dumbbell|weightbench", re.I),
     "gym equipment texture",
     "gym equipment surface, preserve exact color and material as shown"),
    (re.compile(r"hospital|medcenter|clinictex", re.I),
     "hospital interior tile texture",
     "hospital or clinic interior wall/floor tile, preserve exact clean tile color and grid, no added grime unless present in input"),
    (re.compile(r"cellbar|prisonbar|jailbar", re.I),
     "prison bar texture",
     "flat metal prison bar texture, keep exact bar spacing thickness and color, do not merge bars or add a scene behind them"),
    (re.compile(r"casino|vegas(?!world)", re.I),
     "casino interior texture",
     "casino interior surface (carpet, wall or neon trim), preserve exact pattern and saturated colors, do not desaturate neon"),
    (re.compile(r"marquee|cinema|movietheater|moviethe", re.I),
     "cinema marquee texture",
     "cinema marquee sign surface, preserve exact bulb pattern and lettering, sharp readable text"),
    (re.compile(r"monitor|computerscreen|pcscreen|labscreen", re.I),
     "computer screen texture",
     "computer or lab monitor screen graphic, keep exact on-screen colors, text and layout, no invented reflections"),
    (re.compile(r"tvscreen|television|tv_screen", re.I),
     "television screen texture",
     "television screen graphic, keep exact on-screen image and colors, no invented reflections"),
    (re.compile(r"newspaper|magazine(?!tex)", re.I),
     "newspaper or magazine texture",
     "flat newspaper or magazine page graphic, keep exact text layout and colors, sharp readable text, do not rewrite words"),
    (re.compile(r"moneytex|dollarbill|cashstack|banknote", re.I),
     "money texture",
     "flat banknote or cash surface, keep exact printed design and color, do not rewrite text or numbers"),
    (re.compile(r"gunmetal|weaponskin|pistoltex|rifletex|weapontex", re.I),
     "weapon surface texture",
     "flat weapon body panel/skin surface texture, keep exact color and panel lines, do not invent a full 3D weapon render"),
    (re.compile(r"(^|_)flame\w*|firetex|fireball", re.I),
     "fire flame texture",
     "fire or flame effect sprite, preserve exact orange/yellow gradient and shape, do not desaturate"),
    (re.compile(r"(^|_)smoke(?!stack)", re.I),
     "smoke texture",
     "smoke effect sprite, preserve exact gray/white gradient and softness"),
    (re.compile(r"explo(sion)?tex|blasttex", re.I),
     "explosion effect texture",
     "explosion effect sprite, preserve exact color gradient and shape, do not add unrelated detail"),
    (re.compile(r"vehshad|pedshad|shadowblob|(^|_)shadow(?!box)\w*", re.I),
     "baked shadow texture",
     "soft baked shadow blob sprite, keep it a simple dark soft gradient, do not add ground detail or objects"),
    (re.compile(r"lightmap|lightpatch|(^|_)lmap", re.I),
     "lightmap patch texture",
     "baked lightmap patch, keep it a soft gradient of light/dark values, do not add surface detail or objects"),
    (re.compile(r"cactus|saguaro", re.I),
     "cactus texture",
     "cactus plant surface, preserve exact green tone and spine pattern from the input"),
    (re.compile(r"palmtrunk|palmbark", re.I),
     "palm tree trunk texture",
     "palm tree trunk bark surface, preserve exact fibrous pattern and brown tone, not generic tree bark"),
    (re.compile(r"(^|_)snow(?!board)", re.I),
     "snow ground texture",
     "snow ground surface, preserve exact white/blue-white tone and sparkle, do not tint toward gray or beige"),
    (re.compile(r"(^|_)ice(?!cream)\w*", re.I),
     "ice surface texture",
     "ice surface, preserve exact pale blue-white tone and cracks, do not tint toward gray"),
    (re.compile(r"puddle|wetpatch", re.I),
     "puddle reflection texture",
     "wet ground puddle patch, preserve exact dark reflective tone and shape, do not fill with unrelated reflection content"),
    (re.compile(r"trafficcone|roadcone", re.I),
     "traffic cone texture",
     "traffic cone surface, preserve exact orange/white color and stripe pattern"),
    (re.compile(r"jerseybar|roadbarrier|concbarrier|k.?rail", re.I),
     "concrete traffic barrier texture",
     "concrete road barrier surface, preserve exact gray tone and wear marks, do not brighten to white"),
    (re.compile(r"sandbag", re.I),
     "sandbag texture",
     "stacked sandbag surface, preserve exact tan/khaki tone and seam lines"),
    (re.compile(r"canvastent|tentcloth|tentfab", re.I),
     "tent fabric texture",
     "canvas tent fabric surface, preserve exact color and fold pattern"),
    (re.compile(r"flagtex|nationflag|(^|_)flag\d", re.I),
     "flag fabric texture",
     "flag fabric surface, preserve exact printed colors and folds, do not rewrite emblem"),
    (re.compile(r"billboardframe|adframe|sign.?frame", re.I),
     "billboard frame structure texture",
     "billboard support frame/structure surface, preserve exact dark metal tone, keep separate from the ad graphic itself"),
    (re.compile(r"neon\w*sign|neonlight|neonglow|neontube", re.I),
     "neon glow sign texture",
     "neon glow sign tube and glass, preserve exact saturated neon color and glow against dark background, do not dim or desaturate the glow"),
    (re.compile(r"watertower|watertank", re.I),
     "water tower texture",
     "water tower tank/leg structure surface, preserve exact metal color and rivets"),
    (re.compile(r"radiotower|commtower|antennatower", re.I),
     "radio tower structure texture",
     "radio/comm tower lattice structure, preserve exact thin metal color and geometry, do not thicken beams"),
    (re.compile(r"bball|basketcourt|hoopcourt", re.I),
     "basketball court texture",
     "basketball court surface, preserve exact court color and line markings"),
    (re.compile(r"tenniscourt", re.I),
     "tennis court texture",
     "tennis court surface, preserve exact court color and line markings"),
    (re.compile(r"headstone|gravestone|tombstone", re.I),
     "gravestone texture",
     "stone gravestone/headstone surface, preserve exact gray stone tone and carved text, do not rewrite text"),
    (re.compile(r"stainglass|stainedglass", re.I),
     "stained glass texture",
     "stained glass window surface, preserve exact saturated glass colors and leading lines"),

    (re.compile(r"texpage|skinpage|pedtex\d|char.?skin", re.I),
     "character skin texture page",
     "flat 2D ped/character texture page made of several mapped regions (skin, clothing, props all packed "
     "together) — keep every region exactly where it is with its own original color and material, do not "
     "merge regions, do not turn any region into a different material, do not add unrelated surface grain"),
    # ========== tattoos / skins / character (before generic cj_ / clothes) ==========
    # Character body / multi-part UV atlases (hands+jacket etc.) — must stay exact layout
    (re.compile(
        r"bodyg\d|bodymap|body_?uv|(^|_)body[0-9]*(_|$)|torso|playerbody|cjbody|"
        r"pedbody|armtex|legtex|handtex|foottex",
        re.I),
     "character body UV texture",
     "flat 2D character body UV atlas texture, keep exact UV islands layout seams and colors, "
     "do not invent missing body parts, do not merge islands, do not turn into a posed 3D body, "
     "preserve every region (skin cloth metal) exactly where it is in the input"),
    (re.compile(r"tattcolor|tattoo|tatt_", re.I),
     "tattoo design texture",
     "flat tattoo ink design, keep exact line art and colors, sharp edges, no 3D body reinterpretation"),
    (re.compile(r"(^|_)(face|head|hair|beard)(_|$)|pedskin|bodyskin|playerskin", re.I),
     "character skin or face texture",
     "human skin or face texture, preserve exact skin tone and features, no restyle"),
    (re.compile(r"(^|_)skin(_|$)", re.I),
     "skin texture",
     "skin surface texture, preserve exact tone and detail, no reinterpretation"),
    (re.compile(r"shirt|jeans|pants|shoes|boot|hat|cap|glasses|bandana|clothes|denim|fabric|leather", re.I),
     "clothing fabric texture",
     "clothing fabric texture, keep exact pattern colors and logos, fabric micro-detail only"),

    # ========== CJ house / Grove / Ganton ==========
    (re.compile(r"\bhs[1-4]_|hsv_|bdup|contachou|landhub|ganton", re.I),
     "residential house surface texture",
     "Grove Street / Ganton residential house material, preserve exact original pattern and colors"),
    (re.compile(r"grove", re.I),
     "grove street surface texture",
     "Grove Street area material surface, preserve exact original pattern and colors"),

    # ========== checker / pattern (before vehicle body so bloodra*checker stays B&W) ==========
    (re.compile(r"checker|chequer|checkered|chess", re.I),
     "checker pattern texture",
     "checkerboard or checkered pattern texture, preserve exact black-and-white or original two-tone colors, do not recolor, do not add red or any new hue"),

    # ========== vehicle liveries / adverts BEFORE generic vehicle body ==========
    (re.compile(r"adverts?\d*|livery|paintjob", re.I),
     "vehicle advertisement livery texture",
     "vehicle side advertisement or livery graphic, keep exact text logos and art, sharp edges, no rewrite, no recolor"),
    (re.compile(r"wheel|tire|tyre", re.I),
     "vehicle wheel UV texture",
     "FLAT 2D CROPPED wheel UV fragment only — NEVER complete into a full circular 3D wheel photo — keep the exact "
     "projected layout geometry and proportions of the input, do not invent a "
     "full circular 3D wheel, do not complete missing parts of the rim or tire, "
     "preserve exact tread pattern and colors as shown in the input image only"),
    (re.compile(r"interior\d*|dashboard|seat", re.I),
     "vehicle interior texture",
     "FLAT 2D vehicle interior UV ATLAS — keep exact island layout gauges buttons seat slices as in input, do NOT turn this into a photoreal 3D cabin or single seat photo, do not invent missing instruments, keep original dark tones including dark teal/black plastic, do not flatten to pure black"),
    (re.compile(r"badge", re.I),
     "vehicle badge texture",
     "vehicle badge or emblem, keep exact logo sharp and clear, no recolor"),
    (re.compile(
        r"(body|paint|chassis|bumper).*(car|veh)|(^|_)auto_|"
        r"[a-z]+92(body|interior|wheel|extra|adverts|crate|logos)|"
        r"vehicle|carbody|bloodr[ab]",
        re.I),
     "vehicle body texture",
     "vehicle body paint or panel texture, keep exact original color and panel lines, no blood, no splatters, no recolor"),

    # ========== roofs BEFORE asphalt (rooftarmac contains "tarmac") ==========
    (re.compile(r"rooftarmac|tar.?roof|roof.?tarmac", re.I),
     "tar roof texture",
     "tar or bitumen flat roof surface, preserve exact color and wear, do not invent tiles"),
    (re.compile(r"shingle", re.I),
     "shingle roof texture",
     "shingle roof surface, preserve exact shingle pattern and colors"),
    (re.compile(r"corrug|corrugated", re.I),
     "corrugated metal roof texture",
     "corrugated metal roof sheet, preserve wave pattern and color, do not invent tiles"),
    (re.compile(r"roof.?tile|tile.?roof|ceramicroof", re.I),
     "tile roof texture",
     "ceramic or clay tile roof surface, preserve exact tile layout and colors"),
    (re.compile(r"roof|rooftop|genroof|sjmroof|ws_roof", re.I),
     "roof surface texture",
     "TOP-DOWN or surface roof texture, preserve exact gravel/tar/membrane pattern and colors, "
     "small dark objects are roof vents handles or fixtures — NEVER convert them into windows glass or building facade, "
     "do not invent extra windows or architectural openings, keep brightness close to source"),

    # ========== roads / asphalt / markings ==========
    (re.compile(r"road.?mark|lane.?mark|line.?texture|yellow.?line|white.?line|chevron", re.I),
     "road marking texture",
     "painted road marking lines, keep exact geometry and colors, sharp edges, no extra symbols"),
    (re.compile(r"freeway|highway|dualroad", re.I),
     "freeway asphalt texture",
     "freeway highway asphalt road surface, preserve exact cracks grain and color, photoreal asphalt"),
    (re.compile(r"carpark|parking", re.I),
     "car park surface texture",
     "car park asphalt or concrete surface, preserve markings and wear"),
    (re.compile(r"sidewalk|pavement|footpath|boardwalk|pave(?!ment)", re.I),
     "sidewalk pavement texture",
     "sidewalk pavement surface, preserve exact tile or concrete pattern"),
    (re.compile(r"curb|kerb", re.I),
     "road curb texture",
     "street curb edge texture, keep exact shape and color"),
    (re.compile(r"cobble|crazy.?pave|stone.?path", re.I),
     "cobblestone path texture",
     "cobblestone or stone path surface, preserve exact stone layout"),
    (re.compile(r"asphalt|plaintarmac|ruffroad|tarmac", re.I),
     "asphalt road texture",
     "asphalt road surface, preserve exact cracks grain roughness and original color, photoreal road"),
    (re.compile(r"road(?!side)|street|lane", re.I),
     "road surface texture",
     "road surface texture, preserve exact cracks grain and original color"),

    # ========== doors / windows / gates ==========
    (re.compile(r"garage.?door|garagedoor", re.I),
     "garage door texture",
     "garage door panel surface, metal or wood panels, keep exact colors and panel layout"),
    (re.compile(r"door|gate", re.I),
     "door surface texture",
     "FLAT 2D door PANEL texture sheet only — keep exact colors shapes and proportions from the input, "
     "do NOT invent a complete 3D door product photo, door frame, handle, hinges or surroundings; "
     "only enhance surface detail of the existing panel"),
    (re.compile(r"window|win(?!d|g)|glass|blinds", re.I),
     "window surface texture",
     "window glass or frame surface, keep exact original look, no invented reflections of scenes"),

    # ========== walls ==========
    (re.compile(r"brick|brikwall|redbrick", re.I),
     "brick wall texture",
     "brick wall surface ONLY if the input image already shows clear brick masonry; "
     "preserve exact brick layout mortar and colors from the input; if the input is not "
     "clearly brick, ignore the brick filename and keep the exact original surface"),
    (re.compile(r"concrete|cement", re.I),
     "concrete surface texture",
     "concrete wall or surface, preserve exact formwork marks cracks and color"),
    (re.compile(r"plaster|stucco", re.I),
     "plaster wall texture",
     "plaster or stucco wall surface, preserve exact texture and color"),
    (re.compile(r"newall|whitewall|washapartwall|wall", re.I),
     "wall surface texture",
     "building wall surface, preserve exact original pattern and colors"),

    # ========== metal / industrial ==========
    (re.compile(r"rusty|rust", re.I),
     "rusty metal texture",
     "rusty metal surface, preserve exact rust patterns and colors"),
    (re.compile(r"chrome", re.I),
     "chrome metal texture",
     "chrome metal surface, realistic reflections micro-detail, keep exact color"),
    (re.compile(r"crane|mast|girder|beam|truss", re.I),
     "structural metal texture",
     "dark structural metal beam or truss texture, preserve exact dark gray/black metal tones and geometry, "
     "do not bleach to light gray or white paint, keep rivets and edges as in input"),
    (re.compile(r"aluminium|aluminum|steel|iron|sheetmetal|metal", re.I),
     "metal surface texture",
     "metal surface, industrial micro-detail, preserve exact color and brightness — dark metal stays dark"),
    (re.compile(r"mesh|chain.?link|railing", re.I),
     "mesh fence texture",
     "mesh or chain-link fence surface, keep exact grid pattern"),
    (re.compile(r"fence", re.I),
     "fence texture",
     "fence surface texture, keep exact pattern"),
    (re.compile(r"pipe|pole|girder|scaffold|lamppost", re.I),
     "metal pole or pipe texture",
     "cylindrical metal pole pipe or girder surface"),
    (re.compile(r"container|barrel|drum", re.I),
     "container or barrel texture",
     "industrial container or barrel surface, keep exact colors and markings"),
    (re.compile(r"vent|grate|drain|aircon|air.?con", re.I),
     "vent or grate texture",
     "metal roof vent grate or air-con unit texture, keep exact shape — NOT a window, no glass panes"),

    # ========== signs / graffiti / posters ==========
    (re.compile(r"graf|tag_|ganggraf|spray", re.I),
     "graffiti texture",
     "graffiti spray art, keep exact letters shapes and colors, vector-like sharp edges"),
    (re.compile(r"billboard|poster|mural", re.I),
     "poster or billboard texture",
     "poster or billboard graphic, keep exact image and text, no reinterpretation"),
    (re.compile(r"headroom|warning|hazard|caution|maxhead|stripes?", re.I),
     "warning sign texture",
     "warning or height-limit sign with yellow/black hazard stripes, preserve exact yellow saturation black text and stripe geometry, do not wash yellow into beige"),
    (re.compile(r"sign|logo|font|letter|number|decal|sticker|sanice|sancorn|ticket", re.I),
     "sign or logo texture",
     "FLAT painted game sign / logo — keep exact letter shapes colors background tint and decorations, "
     "sharp readable text, do not invent bolts stones 3D frames or rewrite words, do not recolor"),

    # ========== ground / nature ==========
    # NOTE: "lawn"/"grass" in GTA filenames often labels a MAP AREA, not the material.
    # Image-color override in infer_material() will demote these when the image is not green.
    (re.compile(r"grass|lawn|turf", re.I),
     "grass ground texture",
     "only enhance grass if the input image itself is already predominantly green grass; "
     "otherwise treat as a generic surface and preserve exact original colors layout and materials "
     "of the input image, never convert walls buildings or other surfaces into grass"),
    (re.compile(r"dirt|mud|soil|earth", re.I),
     "dirt ground texture",
     "seamless dirt soil ground surface, earth texture, preserve exact color"),
    # Awning / canopy BEFORE sand|beach (beachawning must NOT become sand)
    (re.compile(r"awning|canopy|tarpaulin|tarp", re.I),
     "awning fabric texture",
     "flat colored awning or canopy panel, preserve exact solid colors and any stripes, "
     "do not invent sand fabric grain dirt or unrelated materials"),
    (re.compile(r"sand|beach|desert.?gravel", re.I),
     "sand ground texture",
     "sand or beach ground surface ONLY if the input image looks like sand; "
     "preserve exact grain and color; if the input is a solid color or non-sand material, "
     "ignore the filename and keep the exact original colors"),
    (re.compile(r"gravel|rubble", re.I),
     "gravel ground texture",
     "gravel or rubble ground texture, preserve exact color and stone size, do not turn into asphalt or sand"),
    (re.compile(r"(?:^|_)(?:rock|stone|cliff|boulder)(?:_|$)", re.I),
     "rock surface texture",
     "rock or stone surface texture, preserve exact rock structure cracks and original colors, "
     "do not convert rock into grass vegetation or soil, keep the same mineral appearance"),
    (re.compile(r"tree|bark", re.I),
     "tree bark texture",
     "tree bark surface, natural organic detail, keep exact look and colors from the input"),
    (re.compile(r"leaf|foliage|palm|agave|bush|plant|flower", re.I),
     "foliage or plant texture",
     "plant leaf or foliage texture only if the input image already shows leaves or plants, "
     "otherwise preserve the exact input surface, do not invent vegetation"),

    # ========== interiors / props ==========
    (re.compile(r"carpet|rug", re.I),
     "carpet texture",
     "carpet or rug fabric surface, preserve exact pattern"),
    (re.compile(r"floor.?tile|flroortile|tilestone|tiles(?!s)", re.I),
     "floor tile texture",
     "floor tile surface, preserve exact tile grid and colors"),
    (re.compile(r"floor|ground", re.I),
     "floor surface texture",
     "floor surface material, preserve exact original pattern"),
    (re.compile(r"curtain|blind", re.I),
     "curtain texture",
     "curtain or blind fabric, keep exact color"),
    (re.compile(r"wood|plank|timber|(?<![a-z])board(?![a-z])|wdpanel|chipboard", re.I),
     "wood surface texture",
     "wood plank or panel surface only if the input image itself is wood; "
     "preserve exact original grain pattern and colors, do not turn plastic metal "
     "or other materials into wood, do not invent wood grain on non-wood surfaces"),
    (re.compile(r"kitchen|cabinet|counter|sofa|bed|pillow|mattress|furniture", re.I),
     "furniture surface texture",
     "furniture surface material, keep exact colors and material type"),
    (re.compile(r"crate|box|cardboard", re.I),
     "crate or box texture",
     "box, crate or packaging surface — match whatever material the input already shows: if it is a flat "
     "printed graphic (label, logo, text) keep it exactly as a flat printed graphic; only render wood grain "
     "or corrugated cardboard if the input image itself already clearly shows that material; do not invent "
     "wood grain or crate planks on a flat printed surface; keep exact printed text and markings"),
    (re.compile(r"trash|bin|dumpster", re.I),
     "trash bin texture",
     "trash bin or dumpster surface, keep exact colors"),

    # ========== water / sky ==========
    (re.compile(r"water|wave|ocean|sea|river|waterfall", re.I),
     "water surface texture",
     "water surface texture, preserve exact color and wave pattern"),
    (re.compile(r"sky|cloud", re.I),
     "sky texture",
     "sky or cloud texture, preserve exact colors"),

    # ========== misc high-frequency ==========
    (re.compile(r"wire|cable|antenna", re.I),
     "wire or cable texture",
     "wire cable or antenna texture, keep exact look"),
    (re.compile(r"edge|trim|border|frame|ornate|column|pillar|cornice|architrave", re.I),
     "architectural trim texture",
     "building trim edge or frame surface, keep original colors and pattern"),
    (re.compile(r"plastic|rubber", re.I),
     "plastic surface texture",
     "plastic or rubber surface, keep exact color and finish"),
    (re.compile(r"ceramic|porcelain|marble", re.I),
     "ceramic or marble texture",
     "ceramic or marble surface, preserve exact pattern and color"),
]

_DEFAULT = (
    "game texture material surface",
    "identify the visible surface from the pixels, not from the filename; reconstruct a physically plausible "
    "photorealistic material at higher resolution with crisp microtexture, natural roughness variation, "
    "fine surface structure and believable wear where the source visually supports it; preserve the source "
    "macro shapes, regions, markings, colors and spatial layout; never invent new objects or change the composition",
)

# Categories that must never invent vegetation / grass when the image is not green
_VEGETATION_LABELS = {
    "grass ground texture",
    "foliage or plant texture",
}

# Categories that should be gray/neutral in the source; if the image is actually a
# saturated solid color (e.g. a blue/green flat panel), the filename hint is wrong.
_GRAY_MATERIAL_LABELS = {
    "concrete surface texture",
    "asphalt road texture",
    "road surface texture",
    "car park surface texture",
    "freeway asphalt texture",
    "gravel ground texture",
    "metal surface texture",
    "structural metal texture",
    "concrete traffic barrier texture",
}

# Categories that are structural UV layouts (must stay exact, no object completion)
_UV_CRITICAL_LABELS = {
    "vehicle wheel UV texture",
    "vehicle interior texture",
    "character body UV texture",
    "character skin or face texture",
    "character skin texture page",
    "skin texture",
    "tattoo design texture",
    "clothing fabric texture",
}


def _green_ratio(image_path: Path) -> float:
    """Fraction of pixels that look green. Returns 0.0 on failure."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return 0.0
    try:
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            small = im.resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        greenish = (g > r + 12) & (g > b + 8) & (g > 40)
        return float(greenish.mean())
    except Exception:
        return 0.0


def _brick_ratio(image_path: Path) -> float:
    """
    Fraction of pixels that look reddish / brick-orange (typical fired clay brick).
    Beige/cream/gray walls score low → filename 'brick' should be demoted.
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return 0.0
    try:
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            small = im.resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        # Red-orange brick range: R dominant, moderate G, low B, not too dark/bright gray
        brickish = (
            (r > 90) & (r > g + 15) & (r > b + 25) & (g > 40) & (g < r - 5)
            & ((r - b) > 30)
        )
        return float(brickish.mean())
    except Exception:
        return 0.0


def _wood_ratio(image_path: Path) -> float:
    """Fraction of pixels in brown / wood-tone range."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return 0.0
    try:
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            small = im.resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        # Brown wood: R > G > B, moderate saturation, not pure red
        woodish = (
            (r > 70) & (g > 40) & (b < g) & (r > g + 8) & (g > b + 5)
            & (r < 220) & ((r - b) > 25) & ((r - g) < 80)
        )
        return float(woodish.mean())
    except Exception:
        return 0.0


def _mean_rgb(image_path: Path) -> tuple[float, float, float] | None:
    try:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            small = im.resize((32, 32), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        return float(arr[:, :, 0].mean()), float(arr[:, :, 1].mean()), float(arr[:, :, 2].mean())
    except Exception:
        return None


def _bg_black_ratio(image_path: Path) -> float:
    """Fraction of STRICT pure-black pixels (all channels < 10, low chroma).
    Dark teal / dark metal do NOT count.
    """
    try:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            small = im.resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        mx = arr.max(axis=2)
        mn = arr.min(axis=2)
        return float(((mx < 10.0) & ((mx - mn) < 6.0)).mean())
    except Exception:
        return 0.0



def _bg_white_ratio(image_path: Path) -> float:
    """Fraction of STRICT pure-white pixels (channels > 248, low chroma)."""
    try:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            small = im.resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        mn = arr.min(axis=2)
        mx = arr.max(axis=2)
        return float(((mn > 248.0) & ((mx - mn) < 8.0)).mean())
    except Exception:
        return 0.0




def _sand_ratio(image_path: Path) -> float:
    """Fraction of beige/tan/sand-like pixels. Solid blue/green/red score near 0."""
    try:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as im:
            small = im.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        # sand-ish: warm, R and G high, B lower, not pure white
        sand = (
            (r > 120) & (g > 100) & (b < g + 15) & (r > b + 20)
            & (r < 250) & ((r - b) > 25)
        )
        return float(sand.mean())
    except Exception:
        return 0.0


def _gray_ratio(image_path: Path) -> float:
    """Fraction of pixels that are low-saturation gray/neutral (asphalt, concrete, metal).
    A strongly colored image (e.g. solid blue/green panel) scores near 0.
    """
    try:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as im:
            small = im.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        mx = arr.max(axis=2)
        mn = arr.min(axis=2)
        chroma = mx - mn
        grayish = chroma < 22.0
        return float(grayish.mean())
    except Exception:
        return 0.0


def _tile_flat_fraction(image_path: Path, grid: int = 8, std_thresh: float = 9.0) -> float:
    """
    Fraction of an NxN grid of tiles that are individually near-flat/solid.
    Unlike _is_nearly_flat_color (whole-image std), this catches images that
    mix flat solid regions with a printed graphic/logo (e.g. a pizza box
    lid: mostly solid dark color plus some text) so we don't let the model
    invent asphalt/concrete/wood grain on the solid parts.
    """
    try:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as im:
            small = im.convert("RGB").resize((grid * 8, grid * 8), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        flat_tiles = 0
        total = 0
        for gy in range(grid):
            for gx in range(grid):
                tile = arr[gy * 8:(gy + 1) * 8, gx * 8:(gx + 1) * 8, :]
                total += 1
                if float(tile.std()) < std_thresh:
                    flat_tiles += 1
        return flat_tiles / total if total else 0.0
    except Exception:
        return 0.0


def has_large_flat_regions(image_path: Path) -> bool:
    """True when a large chunk of the image is made of flat/solid tiles,
    even though the image overall is not uniformly flat. Used to cap
    strength so Flux does not invent material grain on those regions."""
    return _tile_flat_fraction(image_path) >= 0.35


def _is_nearly_flat_color(image_path: Path, max_std: float = 12.0) -> bool:
    """True if image is basically solid / two-tone flat color (awning stripes, color plates)."""
    try:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as im:
            small = im.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        # overall channel std
        std = float(arr.std())
        return std < max_std
    except Exception:
        return False


def _looks_grayscale(image_path: Path, sat_thresh: float = 5.0, chroma_ratio: float = 0.92) -> bool:
    """
    True only for nearly pure B&W sources. Muted color textures must stay color.
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return False
    try:
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            small = im.resize((64, 64), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        # saturation proxy 0-100
        sat = np.zeros_like(mx, dtype=np.float32)
        np.divide((mx - mn), mx, out=sat, where=mx > 1e-3)
        sat *= 100.0
        mean_sat = float(sat.mean())
        achromatic = float((sat < sat_thresh).mean())
        return mean_sat < sat_thresh and achromatic >= chroma_ratio
    except Exception:
        return False


def infer_material(filename: str, image_path: Path | None = None, allow_filename_hints: bool = True) -> Tuple[str, str]:
    """
    Soft material hint from filename, validated against actual image colors.

    Filename rules are only a soft hint and must never override obvious identifier-boundary rules. When image_path is given, pixel content
    can demote wrong categories (e.g. filename says brick but image is beige wall).
    """
    name = Path(filename).stem
    full = filename
    label, detail = _DEFAULT

    # IMPORTANT: GTA asset names are identifiers, not ground truth.
    # Many names contain map/model prefixes such as "sancliff", "law2", "lax"
    # and semantic codes can coexist (e.g. interior + wheel). A broad substring
    # match can therefore classify a texture incorrectly.
    #
    # First resolve a few high-confidence structural tokens using boundaries.
    # These are intentionally ordered so a combined name like
    # sadler_sadler92interior_wheel128 is treated as an INTERIOR atlas rather
    # than a wheel just because the wheel rule appears earlier.
    stem_lower = name.lower()

    def _has_token(word: str) -> bool:
        # Match whole underscore/dash-separated semantic tokens, plus the
        # common GTA numeric form: sadler92wheel64 -> wheel is bounded by digits.
        w = re.escape(word.lower())
        return bool(re.search(rf"(?:^|[_\-\d]){w}(?=$|[_\-\d])", stem_lower))

    # Filename semantics are OFF by default in the pipeline. This is deliberate:
    # GTA filenames frequently contain map/model identifiers that look like material
    # words (e.g. sancliff02), and different semantic codes can coexist. The model
    # itself receives the actual image, which is the authoritative visual source.
    # When enabled for diagnostics/manual use, retain boundary-aware matching and
    # explicit interior-over-wheel precedence.
    if allow_filename_hints:
        if any(_has_token(x) for x in ("interior", "dashboard", "seat")):
            label, detail = next((lab, det) for pat, lab, det in _RULES if lab == "vehicle interior texture")
        elif any(_has_token(x) for x in ("wheel", "tire", "tyre")):
            label, detail = next((lab, det) for pat, lab, det in _RULES if lab == "vehicle wheel UV texture")
        else:
            for pattern, lab, det in _RULES:
                if pattern.search(name) or pattern.search(full):
                    label, detail = lab, det
                    break
    else:
        label, detail = _DEFAULT

    if image_path is not None and Path(image_path).is_file():
        ip = Path(image_path)

        # IMAGE-FIRST PIPELINE MODE: do not let any filename category influence the
        # material prompt. The source pixels are authoritative. We still allow two
        # purely visual safety observations: genuinely flat color plates and true
        # grayscale sources. Everything else stays semantically neutral so Flux
        # decides from the image rather than a misleading GTA asset identifier.
        if not allow_filename_hints:
            if _is_nearly_flat_color(ip):
                label = "flat color panel texture"
                detail = (
                    "the input is visually a flat or simple two-tone color panel; preserve exact color blocks "
                    "and boundaries, keep it clean and physically plausible, add only extremely subtle natural "
                    "surface variation if visually appropriate, never invent a different material or object"
                )
            elif _looks_grayscale(ip):
                label = "grayscale game texture surface"
                detail = (
                    "source is visually grayscale; reconstruct realistic surface microdetail and material response "
                    "while keeping the result grayscale and preserving the exact tonal structure"
                )
            else:
                label, detail = _DEFAULT

            mean = _mean_rgb(ip)
            if mean is not None:
                mr, mg, mb = mean
                detail += (
                    f" Source mean RGB is approximately ({mr:.0f},{mg:.0f},{mb:.0f}); keep the overall hue family "
                    "and tonal balance close to the input without bleaching, desaturating or recoloring it."
                )
            return label, detail

        green = _green_ratio(ip)
        brick = _brick_ratio(ip)
        wood = _wood_ratio(ip)

        # Filename said grass/lawn/foliage but image is not mostly green → demote
        if label in _VEGETATION_LABELS and green < 0.22:
            label = "game texture material surface"
            detail = (
                "the filename may mention a map area but the input image is NOT "
                "predominantly green vegetation; preserve the exact original colors, "
                "materials, walls, windows and layout of the input image; never convert "
                "buildings or other surfaces into grass"
            )

        # Filename said brick but image lacks brick-red tones → demote
        if ("brick" in label) and brick < 0.12:
            label = "game texture material surface"
            detail = (
                "the filename mentions brick but the input image does NOT look like "
                "red brick masonry; treat as a generic wall/surface and preserve the "
                "exact original colors layout and material of the input image; do not "
                "invent brick patterns"
            )

        # Filename said wood but image lacks wood-brown tones → demote
        if ("wood" in label) and wood < 0.12:
            label = "game texture material surface"
            detail = (
                "the filename mentions wood but the input image does NOT look like wood; "
                "preserve the exact original surface material and colors; do not invent "
                "wood grain or wooden panels"
            )

        # Filename said sand/beach but image is not sand-colored (e.g. blue awning)
        if ("sand" in label or "beach" in label) and _sand_ratio(ip) < 0.12:
            label = "game texture material surface"
            detail = (
                "the filename may mention beach/sand but the input image is NOT sand; "
                "preserve the exact original solid colors layout and material of the input; "
                "do not convert colored panels into sand dirt or ground texture"
            )

        # Filename said asphalt/concrete/metal/gravel but image is a saturated solid
        # color panel (not gray) → demote so we don't paint fake asphalt/concrete over it
        if label in _GRAY_MATERIAL_LABELS and _gray_ratio(ip) < 0.35:
            label = "game texture material surface"
            detail = (
                "the filename may suggest asphalt, concrete, metal or gravel but the input "
                "image is a distinctly colored (non-gray) surface; preserve the exact original "
                "solid colors, layout and material of the input image; do not paint asphalt, "
                "concrete or metal texture over a colored panel"
            )

        # Nearly flat solid / two-tone color plates (awnings, color fills)
        if _is_nearly_flat_color(ip):
            label = "flat color panel texture"
            detail = (
                "nearly flat solid or simple two-tone color panel; preserve the EXACT hues "
                "and color blocks of the input; only mild denoise; do not invent grain sand "
                "fabric weave concrete or any new surface texture; do not recolor"
            )

        # Rock / stone: never allow vegetation takeover
        if "rock" in label or "stone" in label:
            detail = (
                "rock or stone surface texture, preserve exact rock structure cracks "
                "and original colors from the input image, do not convert into grass "
                "vegetation soil or any organic cover"
            )

        # Mixed image: large flat/solid regions alongside detail (e.g. a printed
        # box/sign with a solid-color background) — forbid inventing grain there.
        if not _is_nearly_flat_color(ip) and _tile_flat_fraction(ip) >= 0.35:
            detail = (
                detail + ". Large parts of this image are smooth solid-colored regions; "
                "do not add concrete, asphalt, wood grain, fabric weave or any new surface "
                "texture to those smooth/solid parts, keep them clean and flat and only "
                "sharpen edges of any existing text or graphics"
            )

        # Color fidelity lock from mean RGB of source
        mean = _mean_rgb(ip)
        color_lock = ""
        if mean is not None:
            mr, mg, mb = mean
            color_lock = (
                f", source mean RGB is approximately ({mr:.0f},{mg:.0f},{mb:.0f}) — "
                f"keep the overall hue tint and brightness close to this, do not shift "
                f"toward pure white pure gray or unrelated colors"
            )

        if _looks_grayscale(ip):
            detail = (
                detail + ", source is pure grayscale or black-and-white, keep the result "
                "grayscale with no new color tints"
            )
        else:
            detail = (
                detail + color_lock +
                ", preserve the exact original colors and subtle tints of the input, "
                "do not desaturate, do not bleach toward white, do not convert to "
                "black-and-white, do not bleed colors from other textures"
            )


    return label, detail


def _uv_semantic_context(filename: str) -> str:
    """Return a very small, boundary-aware semantic hint ONLY for UV maps.

    GTA filenames are unreliable in general, but explicit tokens such as
    ``...92interior128`` or ``...92wheel64`` are useful when the asset is a
    known UV atlas. This helper never looks for broad substrings like
    ``cliff`` inside ``sancliff`` and never classifies ordinary world textures.
    Interior/dashboard/seat deliberately take precedence over wheel so a mixed
    identifier cannot turn an interior atlas into a wheel.
    """
    stem = Path(filename).stem.lower()
    def has(word: str) -> bool:
        return bool(re.search(rf"(?:^|[_\-\d]){re.escape(word)}(?=$|[_\-\d])", stem))
    if any(has(w) for w in ("interior", "dashboard", "seat")):
        return "vehicle interior UV atlas"
    if any(has(w) for w in ("wheel", "tire", "tyre")):
        return "vehicle wheel/tire UV atlas"
    if any(has(w) for w in ("body", "bodymap", "bodyuv")):
        return "vehicle body UV atlas"
    if any(has(w) for w in ("uv", "atlas")):
        return "generic game UV atlas"
    return ""


def is_uv_critical_filename(filename: str) -> bool:
    """Return whether the filename strongly indicates a game UV/layout map."""
    return bool(_uv_semantic_context(filename))


def build_dynamic_prompt(master: str, filename: str, image_path: Path | None = None, allow_filename_hints: bool = False, cfg=None) -> str:
    """Build a conservative prompt with independently switchable evidence sources."""
    # Filename material hints are opt-in. UV context is separately controlled.
    use_filename = bool(getattr(getattr(cfg, 'inference', None), 'filename_material_hints', allow_filename_hints)) if cfg else allow_filename_hints
    use_image = bool(getattr(getattr(cfg, 'inference', None), 'image_analysis_hints', True)) if cfg else True
    use_uv = bool(getattr(getattr(cfg, 'inference', None), 'uv_context_hints', True)) if cfg else True
    label, detail = infer_material(filename, image_path=image_path, allow_filename_hints=use_filename) if use_image or use_filename else (_DEFAULT[0], _DEFAULT[1])
    uv_context = _uv_semantic_context(filename) if use_uv else ''
    knowledge = format_context(filename, cfg) if cfg is not None else ''

    uv_lock = ''
    if uv_context:
        uv_lock = (
            f" This is a {uv_context}. Use that only as structural asset context; the supplied pixels remain authoritative. "
            "Preserve every UV island, boundary, seam, logo, gauge, button, seat slice, wheel/tire fragment, "
            "black/transparent padding and colored region at the exact same normalized coordinates. "
            "Never turn the 2D map into a complete 3D object, scene, building, room, cabin, wheel or vehicle. "
            "Do not add perspective, camera view, geometry, or semantic objects that are not explicitly present. "
            "Reconstruct realistic material microdetail INSIDE existing regions only."
        )

    material_part = f"Material category: {label}. {detail}." if (use_image or use_filename) else "Do not assign a semantic material label from the filename. Identify the visible material from the pixels."
    color = (
        "COLOR FIDELITY IS CRITICAL. Preserve the source hue, saturation and color-family of every existing region. "
        "A red region must remain red, green must remain green, blue must remain blue, yellow must remain yellow, "
        "and dark regions must remain dark. Never turn a saturated colored region into white, gray, beige or a different hue. "
        "Use generated luminance/microcontrast only to add realistic surface detail while keeping the source chromatic identity."
    )
    lock=(
        f"{material_part} Treat the supplied input image as the sole visual source of truth. "
        f"Perform a photorealistic texture restoration and resolution upscale, not a scene redesign. "
        f"{color} Preserve exact composition, layout, proportions, markings, logos, text, symbols, pattern geometry and object placement. "
        f"Do not move, crop, rotate, mirror, stretch, rearrange or invent objects. "
        f"Reconstruct realistic physically plausible microtexture, roughness, fibers, pores, grain, tiny wear and crisp material detail "
        f"inside existing regions only. Avoid watercolor, pastel, airbrush, waxy plastic, clay, smooth AI painting, oversmoothing and color washing. "
        f"Do not reinterpret a map/zone/model identifier as a material. "
        f"{knowledge} {uv_lock} "
        f"Priority: exact asset identity and color first; realistic material detail second; novelty last."
    )
    return f"{master}. {lock}"


def list_categories() -> list[str]:
    """Return unique category labels (for debugging)."""
    seen: list[str] = []
    for _, label, _ in _RULES:
        if label not in seen:
            seen.append(label)
    seen.append(_DEFAULT[0])
    return seen


def is_uv_critical(label: str) -> bool:
    """True when this category must use conservative strength / strict UV lock."""
    return label in _UV_CRITICAL_LABELS
