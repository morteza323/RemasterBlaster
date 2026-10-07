#!/usr/bin/env python3
# GTA SA TXD Mirror Builder FINAL — half of PNG size + legal sides only
#
# FINAL (schema 67):
#   - Target size = HALF of the upscaled PNG, then snap to legal sides:
#       64 / 128 / 256 / 512 / 1024 / 2048
#   - Examples: 1024x1024 PNG → 512x512 TXD
#               1024x2048 PNG → 512x1024 TXD
#               2048x1024 PNG → 1024x512 TXD
#   - Original TXD format (DXT1/3/5 / XRGB32 / ARGB8888) is preserved.
#   - Original alpha behaviour is preserved.
#   - Ped/face/hair/clothing hard max = 1024; world = 2048.
#   - Unmatched TextureNative chunks remain byte-for-byte unchanged.
#   - Protected body/face names stay strict; clothing uses broad match.
#
# STATE_SCHEMA_VERSION = 67
from __future__ import annotations
import csv, io, json, os, struct, sys, threading, time, traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    from PIL import Image
except ImportError:
    Image = None

SECTOR = 2048
RW_STRUCT = 0x00000001
RW_TEXDICTIONARY = 0x00000016
RW_TEXTURENATIVE = 0x00000015
RW_EXTENSION = 0x00000003
RW_CLUMP = 0x00000010
RW_STRING = 0x00000002
PLATFORM_D3D9 = 9
D3DFMT_DXT1 = 0x31545844
D3DFMT_DXT5 = 0x35545844
D3DFMT_A8R8G8B8 = 21
D3DFMT_X8R8G8B8 = 22
D3DFMT_DXT3 = 0x33545844
RASTER_MIPMAP = 0x8000
# Raster format bits commonly used in SA D3D9 TXDs
RASTER_FORMAT_8888 = 0x0500
RASTER_FORMAT_888 = 0x0600
RASTER_FORMAT_LUM8 = 0x0400
RASTER_FORMAT_1555 = 0x0100
RASTER_FORMAT_565 = 0x0200
RASTER_FORMAT_4444 = 0x0300
MAX_TEXTURE_SIDE = 2048
MAX_PED_TEXTURE_SIDE = 1024
MAX_BEARD_TEXTURE_SIDE = 1024
MAX_HAIR_TEXTURE_SIDE = 1024
MAX_FACE_TEXTURE_SIDE = 1024
MAX_MIP_LEVELS_PED = 7
# TEST MODE: player/ped/face/hair/clothing replacements are deliberately
# emitted at 64px base resolution to isolate mip/texture-size issues.
TEST_PED_TEXTURE_SIDE = 64  # disabled in v53; kept only as a legacy constant
STATE_SCHEMA_VERSION = 67

DEFAULT_IMG = Path(r'H:\gta sa textures\models\models\gta3.img')
DEFAULT_PNG = Path(r'H:\upscaled')
DEFAULT_OUT = Path(r'H:\GTA3_MIRROR')

# Names that usually belong to player/ped face, head, hair, beard, body, clothing
PED_SAFE_KEYWORDS = (
    'head', 'face', 'bald', 'beard', 'hair', 'skin', 'body', 'arm', 'leg',
    'hand', 'foot', 'cj', 'player', 'ped', 'face_', '_face', 'head_', '_head',
    'hair_', '_hair', 'beard_', '_beard', 'bald_', 'mustache', 'eyebrow',
    'eye', 'mouth', 'teeth', 'lip', 'nose', 'ear', 'cheek', 'afro', 'dread',
    'cornrow', 'fade', 'goatee', 'sideburn', 'moustache', 'mustach', 'wig',
    'haircut', 'hairstyle', 'braid', 'ponytail', 'bun', 'crewcut', 'flattop',
    # clothing / outfit (critical for clothing shop crash + rainbow pants)
    'torso', 'jeans', 'shirt', 'pants', 'shorts', 'shoes', 'sneaker', 'boot',
    'hat', 'cap', 'bandana', 'glasses', 'watch', 'chain', 'necklace', 'ring',
    'vest', 'jacket', 'coat', 'sweater', 'hoodie', 'tshirt', 't_shirt',
    'underwear', 'boxers', 'socks', 'glove', 'belt', 'bag', 'backpack',
    'mask', 'helmet', 'goggles', 'earring', 'tattoo', 'outfit', 'clothes',
    'legs', 'feet', 'hands', 'arms', 'upper', 'lower', 'suit', 'tie',
    'player_', 'cj_', 'ped_'
)
BEARD_KEYWORDS = ('beard', 'afrobeard', 'bald', 'mustache', 'mustach', 'goatee', 'sideburn', 'stubble')
HAIR_KEYWORDS = (
    'hair', 'afro', 'dread', 'cornrow', 'fade', 'wig', 'braid', 'ponytail',
    'bun', 'crewcut', 'flattop', 'hairstyle', 'haircut', 'bandana'
)

class TxdError(Exception):
    pass

@dataclass
class Chunk:
    type_id: int
    version: int
    payload: bytes
    def pack(self) -> bytes:
        return struct.pack('<III', self.type_id, len(self.payload), self.version) + self.payload

@dataclass
class ImgEntry:
    index: int
    offset: int
    stream_sectors: int
    size_sectors: int
    name: str
    data: Optional[bytes] = None

def parse_chunks(data: bytes, strict: bool = True) -> List[Chunk]:
    out: List[Chunk] = []
    pos = 0
    while pos < len(data):
        if len(data) - pos < 12:
            if strict and any(data[pos:]):
                raise TxdError('Trailing bytes in RenderWare container')
            break
        t, s, v = struct.unpack_from('<III', data, pos)
        end = pos + 12 + s
        if end > len(data):
            raise TxdError(f'RW chunk 0x{t:08X} exceeds container')
        out.append(Chunk(t, v, data[pos + 12:end]))
        pos = end
    return out

def cstr(b: bytes) -> str:
    return b.split(b'\0', 1)[0].decode('latin-1', 'replace')

def list_texture_names_in_txd(data: bytes) -> List[str]:
    """Return every TextureNative diffuse name inside a TXD blob."""
    names: List[str] = []
    if len(data) < 12:
        return names
    rt, rs, rv = struct.unpack_from('<III', data, 0)
    if rt != RW_TEXDICTIONARY:
        return names
    body = data[12:12 + min(rs, len(data) - 12)]
    try:
        top = parse_chunks(body, strict=False)
    except Exception:
        return names
    for ch in top:
        if ch.type_id != RW_TEXTURENATIVE:
            continue
        try:
            kids = parse_chunks(ch.payload, strict=False)
        except Exception:
            continue
        if not kids or kids[0].type_id != RW_STRUCT:
            continue
        sp = kids[0].payload
        if len(sp) >= 40:
            names.append(cstr(sp[8:40]))
    return names

def scan_dff_texture_name_refs(data: bytes) -> Set[str]:
    """Best-effort: collect RW String chunks that look like texture names (from DFF materials)."""
    found: Set[str] = set()
    pos = 0
    n = len(data)
    while pos + 12 <= n:
        t, s, v = struct.unpack_from('<III', data, pos)
        if s < 0 or pos + 12 + s > n + 64:
            pos += 1
            continue
        if t == RW_STRING and 1 <= s <= 64:
            raw = data[pos + 12:pos + 12 + s]
            name = cstr(raw).strip()
            if name and all(32 <= ord(c) < 127 for c in name) and len(name) >= 2:
                # skip obvious non-texture strings
                if name.lower() not in ('frame', 'null', 'default'):
                    found.add(name)
        pos += 12 + max(s, 0)
        if pos <= 0:
            break
    return found

class ImgV2:
    def __init__(self, path: Path):
        self.path = path
        with path.open('rb') as f:
            hdr = f.read(8)
        if hdr[:4] != b'VER2':
            raise ValueError('This is not a GTA SA VER2 IMG archive.')
        self.count = struct.unpack('<I', hdr[4:8])[0]
        if self.count <= 0 or self.count > 1_000_000:
            raise ValueError(f'Invalid IMG entry count: {self.count}')
        self.entries: List[ImgEntry] = []
        with path.open('rb') as f:
            f.seek(8)
            for i in range(self.count):
                raw = f.read(32)
                if len(raw) != 32:
                    raise ValueError('IMG directory is truncated.')
                off, stream, size = struct.unpack_from('<IHH', raw, 0)
                name = cstr(raw[8:32])
                if not name:
                    continue
                self.entries.append(ImgEntry(i, off, stream, size, name))

    def read_entry(self, e: ImgEntry) -> bytes:
        with self.path.open('rb') as f:
            f.seek(e.offset * SECTOR)
            sectors = e.stream_sectors or e.size_sectors
            if sectors <= 0:
                raise ValueError(f'IMG entry {e.name} has zero size.')
            return f.read(sectors * SECTOR)

    def write_rebuilt(self, dest: Path, log) -> None:
        final_data: List[bytes] = []
        for e in self.entries:
            final_data.append(e.data if e.data is not None else self.read_entry(e))
        padded: List[bytes] = []
        for blob in final_data:
            pad = (SECTOR - (len(blob) % SECTOR)) % SECTOR
            if pad:
                blob = blob + b'\0' * pad
            padded.append(blob)
        dir_size = 8 + 32 * len(self.entries)
        first_data_sector = (dir_size + SECTOR - 1) // SECTOR
        new_dir = bytearray()
        new_dir += b'VER2'
        new_dir += struct.pack('<I', len(self.entries))
        cur_sector = first_data_sector
        for i, e in enumerate(self.entries):
            blob = padded[i]
            sectors = len(blob) // SECTOR
            name_bytes = (e.name.encode('latin-1', 'replace')[:23] + b'\0').ljust(24, b'\0')
            new_dir += struct.pack('<IHH', cur_sector, sectors, 0) + name_bytes
            cur_sector += sectors
        dir_pad = (SECTOR - (len(new_dir) % SECTOR)) % SECTOR
        if dir_pad:
            new_dir += b'\0' * dir_pad
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + '.tmp')
        with tmp.open('wb') as f:
            f.write(new_dir)
            for blob in padded:
                f.write(blob)
        os.replace(tmp, dest)
        log(f'Rebuilt IMG written: {dest}  ({len(self.entries):,} entries)')

# Allowed texture sides for GTA SA (RenderWare / D3D9). Never write anything else.
_ALLOWED_SIDES = (64, 128, 256, 512, 1024, 2048)


def _nearest_allowed_side(n: int, max_side: int = MAX_TEXTURE_SIDE, prefer_up: bool = True) -> int:
    """Pick nearest value from the legal set {64,128,256,512,1024,2048} ≤ max_side.

    prefer_up=True (default for upscaled PNGs): when equally close, take the larger size
    so we keep the remaster detail instead of downscaling back to original.
    """
    if n < 1:
        n = 64
    allowed = [s for s in _ALLOWED_SIDES if s <= max_side]
    if not allowed:
        return 64
    if n in allowed:
        return n
    best = allowed[0]
    best_dist = abs(n - best)
    for s in allowed[1:]:
        d = abs(n - s)
        if d < best_dist or (d == best_dist and prefer_up and s > best):
            best, best_dist = s, d
        elif d == best_dist and (not prefer_up) and s < best:
            best, best_dist = s, d
    return best


def _force_pow2_size(w: int, h: int, max_side: int = MAX_TEXTURE_SIDE,
                     prefer_up: bool = True) -> Tuple[int, int]:
    """Force both dimensions to a legal SA power-of-2 side.

    - Uses only {64, 128, 256, 512, 1024, 2048} capped by max_side.
    - Preserves aspect roughly.
    - prefer_up keeps upscaled PNG detail (rounds toward larger when close).
    """
    if w < 1:
        w = 64
    if h < 1:
        h = 64
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        w = max(1, int(round(w * scale)))
        h = max(1, int(round(h * scale)))
    nw = _nearest_allowed_side(w, max_side, prefer_up=prefer_up)
    nh = _nearest_allowed_side(h, max_side, prefer_up=prefer_up)
    # If result is badly distorted vs source aspect, rebuild from longest side
    if nw >= 64 and nh >= 64:
        aspect = w / float(h) if h else 1.0
        new_aspect = nw / float(nh)
        if abs(new_aspect - aspect) > 0.35:
            if nw >= nh:
                nh = _nearest_allowed_side(int(round(nw / aspect)), max_side, prefer_up=prefer_up)
            else:
                nw = _nearest_allowed_side(int(round(nh * aspect)), max_side, prefer_up=prefer_up)
    return nw, nh


class PngIndex:
    """PNG resolver with a hard safety boundary for player.img clothing/body.

    Generic IMG/TXD behavior:
      1) Exact TXD+texture contextual name: ``{txd}_{texture}.png``
      2) Exact global texture stem when it is plausibly the same asset.
      3) Contextual suffix match: ``{anything}_{texture}.png``. This restores
         prefixed exports such as ``*_AH_PAINTING.png`` without forcing the
         prefix to equal the TXD name.

    Player-sensitive behavior is handled by a strict filename/TXD allow-list
    below. Protected texture names never use suffix/global fallbacks: they
    must resolve to their explicitly assigned TXD(s). This prevents generic
    face/torso/legs/vest PNGs from leaking into unrelated clothing TXDs while
    leaving ordinary shared world/player textures free to be reused.
    """

    # Only these known CJ body/clothing/face textures are hard-bound.
    # The values are exact TXD stems in player.img.
    HARD_PLAYER_BINDINGS = {
        'face.png': {'player_face'},
        'player_face_face.png': {'player_face'},
        'slope_face.png': {'slope'},
        'wedge_face.png': {'wedge'},
        'afro_face_afro.png': {'afro'},
        'face_afro.png': {'afro'},
        'vestblack.png': {'vestblack'},
        'vestblack_vestblack.png': {'vestblack'},
        'vest.png': {'vest'},
        'vest_vest.png': {'vest'},
        'player_torso_torso.png': {'player_torso'},
        'hands_torso8bit.png': {'hands'},
        'torso8bit.png': {'player'},
        'torso.png': {'torso'},
        'torso_torso.png': {'torso'},
        'legsheart.png': {'legsheart'},
        'legsheart_legsheart.png': {'legsheart'},
        'legsboxerswht.png': {'legsboxerswht'},
        'legsboxerswht_legsboxerswht.png': {'legsboxerswht'},
        'legsblack_legsboxersblk.png': {'legsblack'},
        'legspantsgrey.png': {'legspantsgrey'},
        'legsboxersblk.png': {'legsblack'},
        'legs.png': {'legs'},
        'legs_fat.png': {'player_legs'},
        'legs_ripped.png': {'player_legs'},
        'player_legs_legs.png': {'player_legs'},
        'player_legs_legs_fat.png': {'player_legs'},
        'player_legs_legs_ripped.png': {'player_legs'},
        'legs_legs.png': {'legs'},
        'jeansdenim_legs.png': {'jeansdenim'},
    }

    HARD_NAMES = {k.casefold() for k in HARD_PLAYER_BINDINGS}

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.by_name: Dict[str, List[Path]] = {}
        self.by_suffix: Dict[str, List[Path]] = {}
        for p in self.root.rglob('*.png'):
            if not p.is_file():
                continue
            stem = p.stem.casefold().strip()
            self.by_name.setdefault(stem, []).append(p)
            # Index every underscore-delimited suffix. The final suffix
            # segment is enough for names like *_AH_PAINTING; also index the
            # full tail after each underscore so AH_PAINTING is recoverable.
            parts = stem.split('_')
            for i in range(1, len(parts)):
                tail = '_'.join(parts[i:])
                if tail:
                    self.by_suffix.setdefault(tail, []).append(p)

    @property
    def count(self) -> int:
        return sum(map(len, self.by_name.values()))

    def _folder_score(self, p: Path, txd_name: str) -> int:
        token = Path(txd_name).stem.casefold() if txd_name else ''
        rel = p.relative_to(self.root)
        folder_parts = [part.casefold() for part in rel.parts[:-1]]
        score = 0
        if token and token in folder_parts:
            score += 1000
        folder = '/'.join(folder_parts)
        if any(k in folder for k in ('player', 'ped', 'cj', 'clothes', 'skin', 'face', 'hair', 'outfit')):
            score += 50
        return score

    def _pick(self, candidates: List[Path], txd_name: str) -> Tuple[Path, int]:
        if len(candidates) == 1:
            return candidates[0], 0
        scored = []
        token = Path(txd_name).stem.casefold() if txd_name else ''
        for p in candidates:
            rel = p.relative_to(self.root)
            score = self._folder_score(p, txd_name)
            stem = p.stem.casefold()
            # Exact TXD contextual file remains the strongest possible choice.
            if token and stem.startswith(token + '_'):
                score += 5000
            # Prefer shorter prefixes for ambiguous suffix matches, then
            # deterministic lexical ordering.
            score -= len(stem)
            scored.append((score, str(p).casefold(), p))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][2], len(candidates)

    def _hard_bound_candidates(self, key: str, txd_stem: str) -> Tuple[Optional[Path], int, bool]:
        """Return hard-bound player candidate for a protected filename/name.
        The binding is filename-specific, so generic legs/torso/face candidates
        can never cross into another protected TXD.
        """
        for filename, allowed_txd in self.HARD_PLAYER_BINDINGS.items():
            if txd_stem in allowed_txd and filename.casefold() in self.by_name:
                # Only accept the protected file when its actual stem is the
                # expected exact filename stem and it corresponds to this key.
                stem = Path(filename).stem.casefold()
                if stem == key or stem.endswith('_' + key) or filename.casefold() in self.HARD_NAMES:
                    cands = self.by_name.get(stem, [])
                    if cands:
                        return self._pick(cands, txd_stem), True
        return None, 0, False

    def _is_protected_name(self, key: str) -> bool:
        # ONLY CJ body/skin/face — never regular clothing (hats, shirts, shoes, …)
        protected_keys = {
            # faces
            'face', 'face_afro', 'face_afro_blond',
            'head', 'head8bit',
            # body / torso skin
            'torso', 'torso8bit', 'torso_fat', 'torso_ripped',
            # legs skin / default body legs
            'legs', 'legs_fat', 'legs_ripped',
            # feet / hands skin
            'foot', 'feet', 'feet8bit', 'hands',
        }
        return key in protected_keys

    def resolve(self, name: str, txd_name: str, player_strict: bool = False) -> Tuple[Optional[Path], int]:
        """Resolve PNG for a TextureNative name.

        Protected (body/face/skin ONLY): contextual + hard-bound — no leakage.
        Everything else (clothing, hats, shoes, tattoos, accessories):
          exact → doubled → contextual → suffix → prefix → ends-with → token.
        """
        key = name.casefold().strip()
        txd_stem = Path(txd_name).stem.casefold().strip() if txd_name else ''
        if not key:
            return None, 0

        # ------------------------------------------------------------------
        # PROTECTED: only face / torso / legs skin / feet / hands
        # ------------------------------------------------------------------
        if player_strict and self._is_protected_name(key):
            if txd_stem:
                contextual = self.by_name.get(f'{txd_stem}_{key}', [])
                if contextual:
                    return self._pick(contextual, txd_name)
            hard = []
            for filename, allowed_txd in self.HARD_PLAYER_BINDINGS.items():
                if txd_stem in allowed_txd:
                    stem = Path(filename).stem.casefold()
                    if stem == key or stem.endswith('_' + key):
                        hard.extend(self.by_name.get(stem, []))
            if hard:
                return self._pick(hard, txd_name)
            # exact file named exactly like the texture is still OK for body
            # when it is bound or is the plain stem (player_face / player_torso)
            exact = self.by_name.get(key, [])
            if exact and txd_stem in {
                'player_face', 'player_torso', 'player_legs', 'player_feet',
                'torso', 'legs', 'hands', 'feet', 'foot', 'head', 'face',
                'afro', 'slope', 'wedge',
            }:
                return self._pick(exact, txd_name)
            return None, 0

        # ------------------------------------------------------------------
        # NON-PROTECTED clothing / accessories — broad match
        # ------------------------------------------------------------------
        # 1) exact stem
        c = self.by_name.get(key, [])
        if c:
            return self._pick(c, txd_name)

        # 2) doubled texture_texture
        c = self.by_name.get(f'{key}_{key}', [])
        if c:
            return self._pick(c, txd_name)

        # 3) contextual {txd}_{texture}
        if txd_stem:
            c = self.by_name.get(f'{txd_stem}_{key}', [])
            if c:
                return self._pick(c, txd_name)

        # 4) suffix *_{texture}
        suffix = f'_{key}'
        hits: List[Path] = []
        for stem, paths in self.by_name.items():
            if stem.endswith(suffix) and len(stem) > len(suffix):
                hits.extend(paths)
        if hits:
            return self._pick(hits, txd_name)

        # 5) prefix {texture}_*
        if len(key) >= 5:
            prefix = f'{key}_'
            hits = []
            for stem, paths in self.by_name.items():
                if stem.startswith(prefix) and len(stem) > len(prefix):
                    hits.extend(paths)
            if hits:
                return self._pick(hits, txd_name)

        # 6) ends-with texture (len >= 6)
        if len(key) >= 6:
            hits = []
            for stem, paths in self.by_name.items():
                if stem != key and stem.endswith(key):
                    hits.extend(paths)
            if hits:
                return self._pick(hits, txd_name)

        # 7) token match
        if len(key) >= 6:
            hits = []
            for stem, paths in self.by_name.items():
                if key in stem.replace('-', '_').split('_'):
                    hits.extend(paths)
            if hits:
                return self._pick(hits, txd_name)

        return None, 0



def _dxt_level_size(width: int, height: int, dxt_mode: str = 'DXT1') -> int:
    """Exact byte size of one DXT mip level (always at least one 4x4 block).
    dxt_mode: 'DXT1' (8 B/block) or 'DXT3'/'DXT5' (16 B/block).
    """
    bw = max(1, (max(width, 1) + 3) // 4)
    bh = max(1, (max(height, 1) + 3) // 4)
    bpb = 8 if dxt_mode.upper() == 'DXT1' else 16
    return bw * bh * bpb


def _next_mip_dims(w: int, h: int) -> Tuple[int, int]:
    """True RenderWare mip step: floor-halve down to 1x1.
    DXT block padding happens only at encode time."""
    return max(1, w // 2), max(1, h // 2)


def _is_ped_safe_name(name: str) -> bool:
    """Return True if texture name looks like ped/face/skin/hair."""
    n = (name or '').lower()
    return any(k in n for k in PED_SAFE_KEYWORDS)


def _is_beard_name(name: str) -> bool:
    n = (name or '').lower()
    return any(k in n for k in BEARD_KEYWORDS)


def _is_hair_name(name: str) -> bool:
    n = (name or '').lower()
    return any(k in n for k in HAIR_KEYWORDS)


def _is_face_name(name: str) -> bool:
    """Explicit face / head skin detection (stricter than general ped)."""
    n = (name or '').lower()
    face_keys = (
        'face', 'head', 'bald', 'skin', 'cj', 'player', 'face_', '_face',
        'head_', '_head', 'eyebrow', 'eye', 'mouth', 'teeth', 'lip', 'nose',
        'ear', 'cheek'
    )
    # avoid treating pure hair/beard as face
    if _is_hair_name(n) or _is_beard_name(n):
        return False
    return any(k in n for k in face_keys)


def _pillow_dxt(img: 'Image.Image', dxt_mode: str = 'DXT1') -> bytes:
    """Encode a SINGLE mip level to DXT1 / DXT3 / DXT5. Never return multi-mip payload."""
    mode = dxt_mode.upper()
    if mode not in ('DXT1', 'DXT3', 'DXT5'):
        mode = 'DXT5' if mode in ('DXT3', 'DXT5') else 'DXT1'
    w, h = img.size
    expected = _dxt_level_size(w, h, mode)
    # Ensure multiple of 4 for encoder
    pw, ph = max(4, ((w + 3) // 4) * 4), max(4, ((h + 3) // 4) * 4)
    if (pw, ph) != (w, h):
        canvas = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
        canvas.paste(img, (0, 0))
        img = canvas
        expected = _dxt_level_size(pw, ph, mode)
    buf = io.BytesIO()
    try:
        img.save(buf, format='DDS', pixel_format=mode)
    except Exception as e:
        # Fallback: DXT3 encode can fail on some Pillow builds → try DXT5
        if mode == 'DXT3':
            try:
                buf = io.BytesIO()
                img.save(buf, format='DDS', pixel_format='DXT5')
                mode = 'DXT5'
                expected = _dxt_level_size(pw, ph, mode)
            except Exception as e2:
                raise TxdError(f'Pillow DDS encode failed ({dxt_mode}): {e} / fallback {e2}')
        else:
            raise TxdError(f'Pillow DDS encode failed ({mode}): {e}')
    data = buf.getvalue()
    if len(data) < 128 or data[:4] != b'DDS ':
        raise TxdError('Pillow DDS encode failed (bad header)')
    payload = data[128:]
    if len(payload) < expected:
        payload = payload + b'\0' * (expected - len(payload))
    return payload[:expected]


def _load_rgba_with_optional_alpha(png: Path) -> 'Image.Image':
    """Load PNG as RGBA and optionally apply an explicitly supplied alpha map.

    IMPORTANT: do not infer transparency from RGB darkness. A black wall is still opaque.
    """
    if Image is None:
        raise RuntimeError('Pillow is missing. Run INSTALL_DEPENDENCIES.bat first.')
    with Image.open(png) as im:
        im.load()
        rgba = im.convert('RGBA')
    stem = png.stem
    parent = png.parent
    candidates = [
        parent / f'{stem}_a.png',
        parent / f'{stem}_alpha.png',
        parent / f'{stem}alpha.png',
        parent / f'{stem}_A.png',
    ]
    for suffix in ('_diffuse', '_diff', '_color', '_col', '_rgb'):
        if stem.lower().endswith(suffix):
            base = stem[:-len(suffix)]
            candidates.extend([
                parent / f'{base}_a.png',
                parent / f'{base}_alpha.png',
                parent / f'{base}alpha.png',
            ])
            break
    for ap in candidates:
        if ap.is_file():
            try:
                with Image.open(ap) as aim:
                    aim.load()
                    a = aim.convert('L')
                    if a.size != rgba.size:
                        a = a.resize(rgba.size, Image.Resampling.LANCZOS)
                    rgba.putalpha(a)
                break
            except Exception:
                pass
    return rgba


def _decode_dxt1_alpha(width: int, height: int, data: bytes) -> Optional['Image.Image']:
    if Image is None or width <= 0 or height <= 0:
        return None
    bw = max(1, (width + 3) // 4)
    bh = max(1, (height + 3) // 4)
    need = bw * bh * 8
    if len(data) < need:
        return None
    mask = bytearray(width * height)
    off = 0
    for by in range(bh):
        for bx in range(bw):
            c0, c1, bits = struct.unpack_from('<HHI', data, off)
            off += 8
            if c0 > c1:
                # DXT1 four-color mode: no transparent entry.
                continue
            rows = bits
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    rows >>= 8
                    continue
                for px in range(4):
                    x = bx * 4 + px
                    idx = rows & 0x3
                    rows >>= 2
                    if x < width and y < height and idx == 3:
                        mask[y * width + x] = 0
                    elif x < width and y < height:
                        mask[y * width + x] = 255
    return Image.frombytes('L', (width, height), bytes(mask))


def _decode_dxt5_alpha(width: int, height: int, data: bytes) -> Optional['Image.Image']:
    if Image is None or width <= 0 or height <= 0:
        return None
    bw = max(1, (width + 3) // 4)
    bh = max(1, (height + 3) // 4)
    need = bw * bh * 16
    if len(data) < need:
        return None
    mask = bytearray(width * height)
    off = 0
    for by in range(bh):
        for bx in range(bw):
            a0 = data[off]
            a1 = data[off + 1]
            alpha_bits = int.from_bytes(data[off + 2:off + 8], 'little')
            off += 16
            vals = [a0, a1]
            if a0 > a1:
                vals += [((6 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
            else:
                vals += [((4 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
            for py in range(4):
                y = by * 4 + py
                for px in range(4):
                    x = bx * 4 + px
                    idx = alpha_bits & 0x7
                    alpha_bits >>= 3
                    if x < width and y < height:
                        mask[y * width + x] = vals[idx]
    return Image.frombytes('L', (width, height), bytes(mask))


def _decode_original_alpha(width: int, height: int, fourcc: int, raster_format: int,
                           mip0_data: bytes) -> Optional['Image.Image']:
    """Recover alpha from original texture only when the replacement PNG is fully opaque."""
    if Image is None:
        return None
    if fourcc == D3DFMT_DXT5:
        return _decode_dxt5_alpha(width, height, mip0_data)
    if fourcc == D3DFMT_DXT1 and (raster_format & 0x0F00) in (0x0100, 0x0300, 0x0500):
        return _decode_dxt1_alpha(width, height, mip0_data)
    if (raster_format & 0x0F00) == 0x0500 and len(mip0_data) >= width * height * 4:
        # D3D9 native 8888 texels are BGRA; alpha is still byte 3.
        return Image.frombytes('L', (width, height), bytes(mip0_data[3::4][:width * height]))
    return None


def _pow2_at_or_below(n: int, hard_max: int) -> int:
    """Largest power-of-2 <= n and <= hard_max (min 4)."""
    n = min(max(4, n), hard_max)
    p = 4
    while p * 2 <= n and p * 2 <= hard_max:
        p *= 2
    return p


def make_mips(rgba: 'Image.Image', orig_w: int = 0, orig_h: int = 0,
              max_side: int = MAX_TEXTURE_SIDE, dxt_mode: str = 'DXT1',
              ped_safe: bool = False, beard_safe: bool = False,
              hair_safe: bool = False, face_safe: bool = False) -> Tuple[int, int, List[bytes], bool]:
    """Build DXT mip chain (DXT1 / DXT3 / DXT5).

    v36 size rules (user request):
    - ALWAYS power-of-2 (game requirement).
    - For ped/face/hair/clothes: compare original TXD size vs PNG.
      Choose the largest power-of-2 that is a clean scale of the original
      (1× or 2× original preferred), never random upscale to 1024.
    - World: keep upscale detail up to 2048.
    """
    if Image is None:
        raise RuntimeError('Pillow is missing. Run INSTALL_DEPENDENCIES.bat first.')
    png_w, png_h = rgba.size
    if png_w < 1 or png_h < 1:
        raise TxdError(f'PNG has invalid size {png_w}x{png_h}')

    is_pedish = ped_safe or hair_safe or beard_safe or face_safe

    if hair_safe:
        hard_max = MAX_HAIR_TEXTURE_SIDE
    elif beard_safe:
        hard_max = MAX_BEARD_TEXTURE_SIDE
    elif face_safe:
        hard_max = MAX_FACE_TEXTURE_SIDE
    elif ped_safe:
        hard_max = MAX_PED_TEXTURE_SIDE
    else:
        hard_max = max_side if max_side > 0 else MAX_TEXTURE_SIDE

    # FINAL: target = HALF of PNG size, then snap to legal sides (min 64).
    w, h = max(1, png_w // 2), max(1, png_h // 2)
    if orig_w >= 4 and orig_h >= 4:
        oa = orig_w / float(orig_h)
        pa = w / float(h) if h else 1.0
        if abs(pa - oa) > 0.08:
            if pa > oa:
                h = max(1, int(round(w / oa)))
            else:
                w = max(1, int(round(h * oa)))
    w, h = _force_pow2_size(w, h, hard_max, prefer_up=True)
    w, h = max(64, w), max(64, h)

    # Final resize to chosen size
    if rgba.size != (w, h):
        rgba = rgba.resize((w, h), Image.Resampling.LANCZOS)

    # Alpha: for ped/hair/face prefer opaque → DXT1 when almost fully opaque
    a_min, a_max = rgba.getchannel('A').getextrema()
    alpha = a_min < 255
    if (ped_safe or hair_safe or beard_safe or face_safe) and alpha:
        alpha_chan = rgba.getchannel('A')
        hist = alpha_chan.histogram()
        total = sum(hist) or 1
        if hist[255] / total > 0.92:
            alpha = False
            rgba.putalpha(Image.new('L', rgba.size, 255))

    pad_color = (0, 0, 0, 0) if alpha else (0, 0, 0, 255)
    pw, ph = max(4, ((w + 3) // 4) * 4), max(4, ((h + 3) // 4) * 4)
    if (pw, ph) != (w, h):
        canvas = Image.new('RGBA', (pw, ph), pad_color)
        canvas.paste(rgba, (0, 0))
        rgba = canvas
        w, h = pw, ph

    levels: List[bytes] = []
    logical_w, logical_h = w, h
    cur = rgba
    seen = set()
    max_levels = MAX_MIP_LEVELS_PED if (ped_safe or beard_safe or hair_safe or face_safe) else 16
    while True:
        key = (logical_w, logical_h)
        if key in seen:
            break
        seen.add(key)
        block = _pillow_dxt(cur, dxt_mode)
        expected = _dxt_level_size(logical_w, logical_h, dxt_mode)
        if len(block) < expected:
            block = block + b'\0' * (expected - len(block))
        elif len(block) > expected:
            block = block[:expected]
        if not block:
            block = b'\0' * expected
        levels.append(block)
        if logical_w <= 1 and logical_h <= 1:
            break
        if len(levels) >= max_levels:
            break
        nw, nh = _next_mip_dims(logical_w, logical_h)
        if (nw, nh) == (logical_w, logical_h):
            break
        logical_w, logical_h = nw, nh
        cur = cur.resize((max(1, logical_w), max(1, logical_h)), Image.Resampling.LANCZOS)
    if not levels:
        levels = [b'\0' * _dxt_level_size(max(w, 1), max(h, 1), dxt_mode)]
        w, h = max(w, 1), max(h, 1)
    return w, h, levels, alpha



@dataclass
class ReplaceInfo:
    name: str = ''
    orig_w: int = 0
    orig_h: int = 0
    new_w: int = 0
    new_h: int = 0
    mip_count: int = 0
    fourcc: str = ''
    ped_safe: bool = False
    beard_safe: bool = False
    hair_safe: bool = False
    face_safe: bool = False
    alpha: bool = False
    png_path: str = ''


def replace_native_struct(ch: Chunk, png: Path) -> Tuple[Chunk, ReplaceInfo]:
    """Replace texels while preserving the original RenderWare/D3D9 header semantics.
    Returns (new_chunk, info) for detailed logging."""
    p = ch.payload
    if len(p) < 88:
        raise TxdError('TextureNative struct too short')
    if struct.unpack_from('<I', p, 0)[0] != PLATFORM_D3D9:
        raise TxdError('Not PC D3D9 texture')

    orig_w = struct.unpack_from('<H', p, 80)[0]
    orig_h = struct.unpack_from('<H', p, 82)[0]
    orig_depth = p[84]
    orig_levels = p[85]
    orig_raster_type = p[86]
    orig_comp_flags = p[87]
    orig_raster = struct.unpack_from('<I', p, 72)[0]
    orig_fourcc = struct.unpack_from('<I', p, 76)[0]
    diffuse_name = cstr(p[8:40]).strip()
    mask_name = cstr(p[40:72]).strip()
    ped_safe = _is_ped_safe_name(diffuse_name) or _is_ped_safe_name(mask_name)
    beard_safe = _is_beard_name(diffuse_name) or _is_beard_name(mask_name)
    hair_safe = _is_hair_name(diffuse_name) or _is_hair_name(mask_name)
    face_safe = _is_face_name(diffuse_name) or _is_face_name(mask_name)
    # hair / face / beard are also ped-safe
    if hair_safe or face_safe or beard_safe:
        ped_safe = True

    info = ReplaceInfo(
        name=diffuse_name or png.stem,
        orig_w=orig_w, orig_h=orig_h,
        ped_safe=ped_safe, beard_safe=beard_safe, hair_safe=hair_safe,
        face_safe=face_safe,
        png_path=str(png),
    )

    with Image.open(png) as im:
        im.load()
        rgba = im.convert('RGBA')

    a_min, _ = rgba.getchannel('A').getextrema()
    png_has_alpha = a_min < 255
    original_has_alpha = bool(orig_comp_flags & 0x01) or bool(mask_name)
    original_fourcc = orig_fourcc
    if original_fourcc == D3DFMT_DXT5:
        original_has_alpha = True

    # Recover original alpha only when needed and safe
    if original_has_alpha and not png_has_alpha and len(p) > 88 and orig_levels:
        if not (ped_safe or beard_safe or hair_safe) or (orig_fourcc == D3DFMT_DXT5):
            first_size = struct.unpack_from('<I', p, 88)[0]
            first_data = p[92:92 + first_size]
            orig_alpha = _decode_original_alpha(orig_w, orig_h, orig_fourcc, orig_raster, first_data)
            if orig_alpha is not None:
                if orig_alpha.size != rgba.size:
                    orig_alpha = orig_alpha.resize(rgba.size, Image.Resampling.NEAREST)
                rgba.putalpha(orig_alpha)


    # ----- Format decision: support DXT1 / DXT3 / DXT5 / XRGB32 -----
    alpha_chan = rgba.getchannel('A')
    a2_min, a2_max = alpha_chan.getextrema()
    replacement_has_alpha = a2_min < 255

    # Classify original
    # SA often uses D3DFMT numeric values for uncompressed:
    #   21 = D3DFMT_A8R8G8B8, 22 = D3DFMT_X8R8G8B8 (with isNotRwCompatible)
    #   0  = classic RW raster path
    if orig_fourcc == D3DFMT_DXT1:
        target_mode = 'DXT1'
    elif orig_fourcc == D3DFMT_DXT3:
        target_mode = 'DXT3'
    elif orig_fourcc == D3DFMT_DXT5:
        target_mode = 'DXT5'
    elif orig_fourcc in (0, 21, 22, D3DFMT_A8R8G8B8, D3DFMT_X8R8G8B8):
        target_mode = 'UNCOMPRESSED'
    else:
        # Unknown / rare (palette, custom D3DFORMAT, etc.) — leave untouched
        info.new_w, info.new_h = orig_w, orig_h
        info.mip_count = orig_levels
        info.fourcc = f'SKIP(0x{orig_fourcc:08X})'
        info.alpha = bool(original_has_alpha)
        return ch, info

    # No format upgrades: replacement must keep the exact source compression format.
    is_pedish = ped_safe or beard_safe or hair_safe or face_safe

    if target_mode == 'UNCOMPRESSED':
        # ----- ABSOLUTE ORIGINAL HEADER PRESERVATION -----
        # Do not reinterpret XRGB32/888, do not convert 888 <-> 8888,
        # and do not let PNG alpha change the native format.  The source
        # TextureNative header is the authority: copy its raster/FourCC/
        # depth/mip/raster-type/flags semantics verbatim.
        #
        # GTA SA's XRGB32 is commonly represented as FORMAT_888 + depth 32
        # (BGRA bytes with an unused X byte) and D3DFMT_X8R8G8B8 (22).
        # The website's "XRGB32" label is a semantic format name; the RW
        # raster field may still say FORMAT_888.  Therefore we must NOT
        # translate it to another label or invent a new raster format.
        original_is_xrgb32 = (orig_fourcc == D3DFMT_X8R8G8B8) or (
            orig_fourcc == 0 and (orig_raster & 0x0F00) == RASTER_FORMAT_888
        )

        # FINAL: target = HALF of PNG size, then snap to legal sides (min 64).
        # Allowed: 64 / 128 / 256 / 512 / 1024 (/2048). Format + alpha preserved.
        hard = MAX_PED_TEXTURE_SIDE if is_pedish else MAX_TEXTURE_SIDE
        pw, ph = rgba.size
        if pw < 1 or ph < 1:
            raise TxdError(f'PNG has invalid size {pw}x{ph}')
        pw, ph = max(1, pw // 2), max(1, ph // 2)
        if orig_w >= 4 and orig_h >= 4:
            oa = orig_w / float(orig_h)
            pa = pw / float(ph) if ph else 1.0
            if abs(pa - oa) > 0.08:
                if pa > oa:
                    ph = max(1, int(round(pw / oa)))
                else:
                    pw = max(1, int(round(ph * oa)))
        w, h = _force_pow2_size(pw, ph, hard, prefer_up=True)
        w, h = max(64, w), max(64, h)
        if rgba.size != (w, h):
            rgba = rgba.resize((w, h), Image.Resampling.LANCZOS)

        # Preserve the exact source mip count.  No smart mip promotion.
        max_levels = max(1, min(orig_levels or 1, 255))
        levels: List[bytes] = []
        cw, ch_ = w, h
        cur = rgba
        for _ in range(max_levels):
            # XRGB32/888/depth32 is stored as BGRA-like 4-byte texels;
            # the X byte is forced to 255 so PNG alpha can never turn an
            # original XRGB32 texture into an alpha texture.
            if original_is_xrgb32:
                cur = cur.copy()
                cur.putalpha(Image.new('L', cur.size, 255))
            levels.append(cur.tobytes('raw', 'BGRA'))
            if cw <= 1 and ch_ <= 1:
                break
            if len(levels) >= max_levels:
                break
            nw, nh = _next_mip_dims(cw, ch_)
            if (nw, nh) == (cw, ch_):
                break
            cw, ch_ = nw, nh
            cur = cur.resize((max(1, cw), max(1, ch_)), Image.Resampling.LANCZOS)

        if not levels:
            levels = [rgba.tobytes('raw', 'BGRA')]

        # Keep the source header byte-for-byte in every format field.
        # Only width/height and the actual pixel payload are replaced.
        head = bytearray(p[:88])
        original_header = bytes(head)
        struct.pack_into('<H', head, 80, w)
        struct.pack_into('<H', head, 82, h)

        # IMPORTANT: do NOT modify bytes 72..87 at all:
        #   72 raster format + private/mipmap flags
        #   76 D3D FourCC / format
        #   84 depth
        #   85 mip count
        #   86 raster type
        #   87 compression/alpha flags
        # The source texture's exact native format is retained.
        source_levels = orig_levels or 1
        out_level_count = min(len(levels), source_levels, 255)
        head[85] = out_level_count
        rf = struct.unpack_from('<I', head, 72)[0]
        if out_level_count > 1:
            rf |= RASTER_MIPMAP
        else:
            rf &= ~RASTER_MIPMAP
        struct.pack_into('<I', head, 72, rf)

        # Keep the source raster semantics, but make mip flag/count agree.
        # We do not manufacture/remove it based on the replacement.
        # The source mip count remains authoritative.
        raster = bytearray()
        for lv in levels[:out_level_count]:
            raster += struct.pack('<I', len(lv)) + lv

        info.new_w, info.new_h = w, h
        info.mip_count = out_level_count
        if orig_fourcc == D3DFMT_X8R8G8B8:
            info.fourcc = 'XRGB32'
        elif orig_fourcc == D3DFMT_A8R8G8B8:
            info.fourcc = 'ARGB8888'
        else:
            info.fourcc = f'RAW(0x{orig_fourcc:08X})'
        info.alpha = bool(original_has_alpha)
        return Chunk(ch.type_id, ch.version, bytes(head) + raster), info

    # ----- DXT1 / DXT3 / DXT5 path -----
    # FINAL: target = HALF of PNG size, then snap to legal sides (min 64).
    # Allowed: 64 / 128 / 256 / 512 / 1024 (/2048). Format + alpha preserved.
    hard = MAX_PED_TEXTURE_SIDE if is_pedish else MAX_TEXTURE_SIDE
    pw, ph = rgba.size
    if pw < 1 or ph < 1:
        raise TxdError(f'PNG has invalid size {pw}x{ph}')
    pw, ph = max(1, pw // 2), max(1, ph // 2)
    if orig_w >= 4 and orig_h >= 4:
        oa = orig_w / float(orig_h)
        pa = pw / float(ph) if ph else 1.0
        if abs(pa - oa) > 0.08:
            if pa > oa:
                ph = max(1, int(round(pw / oa)))
            else:
                pw = max(1, int(round(ph * oa)))
    w, h = _force_pow2_size(pw, ph, hard, prefer_up=True)
    w, h = max(64, w), max(64, h)
    if rgba.size != (w, h):
        rgba = rgba.resize((w, h), Image.Resampling.LANCZOS)
    source_mips = max(1, orig_levels or 1)
    levels: List[bytes] = []
    cur = rgba
    cw, ch_ = w, h
    for _ in range(source_mips):
        levels.append(_pillow_dxt(cur, target_mode))
        # GTA/RenderWare DXT textures are safer when replacement mip chains
        # stop at a real 4x4 block. Deep 2x2/1x1 DXT levels are a known source
        # of invalid/damaged mip data in GTA-era RenderWare pipelines.
        if cw <= 4 and ch_ <= 4:
            break
        nw, nh = _next_mip_dims(cw, ch_)
        if (nw, nh) == (cw, ch_):
            break
        cw, ch_ = nw, nh
        cur = cur.resize((max(4, cw), max(4, ch_)), Image.Resampling.LANCZOS)
    info.new_w, info.new_h = w, h
    info.mip_count = len(levels)
    info.fourcc = {D3DFMT_DXT1: 'DXT1', D3DFMT_DXT3: 'DXT3', D3DFMT_DXT5: 'DXT5'}.get(orig_fourcc, target_mode)
    info.alpha = bool(original_has_alpha)
    alpha = info.alpha

    # Preserve source native header fields exactly. Only dimensions and the
    # generated mip count are written; FourCC/raster/depth/type/flags remain
    # the source values.
    head = bytearray(p[:88])
    struct.pack_into('<H', head, 80, w)
    struct.pack_into('<H', head, 82, h)
    head[85] = min(len(levels), 255)

    safe_levels: List[bytes] = []
    cw, ch_ = w, h
    for lv in levels[:min(len(levels), 255)]:
        exp = _dxt_level_size(cw, ch_, target_mode)
        lv = (lv or b'')[:exp].ljust(exp, b'\0')
        safe_levels.append(lv)
        if cw > 1 or ch_ > 1:
            cw, ch_ = _next_mip_dims(cw, ch_)
    n_mips = min(len(safe_levels), max(1, orig_levels or 1), 255)
    head[85] = n_mips
    # Keep the mip flag internally consistent with the actual chain.
    # 1 level => no mip chain; 2+ levels => mipmapped. This avoids a
    # mismatch between header flags and payload that can confuse SA.
    rf = struct.unpack_from('<I', head, 72)[0]
    if n_mips > 1:
        rf |= RASTER_MIPMAP
    else:
        rf &= ~RASTER_MIPMAP
    struct.pack_into('<I', head, 72, rf)
    raster = bytearray()
    cw, ch_ = w, h
    for lv in safe_levels[:n_mips]:
        exp = _dxt_level_size(cw, ch_, target_mode)
        raster += struct.pack('<I', exp) + lv[:exp].ljust(exp, b'\0')
        if cw > 1 or ch_ > 1:
            cw, ch_ = _next_mip_dims(cw, ch_)
    return Chunk(ch.type_id, ch.version, bytes(head) + raster), info



def validate_txd_blob(data: bytes) -> Tuple[bool, str]:
    """Light structural check. Original SA TXDs often have zero-sized deep mips;
    we only fail on hard corruption (missing first mip / overrun / bad dims).
    """
    try:
        if len(data) < 12:
            return False, 'TXD too small'
        rt, rs, rv = struct.unpack_from('<III', data, 0)
        if rt != RW_TEXDICTIONARY or 12 + rs > len(data):
            return False, 'Invalid TXD root chunk'
        top = parse_chunks(data[12:12 + rs], strict=False)
        for ch in top:
            if ch.type_id != RW_TEXTURENATIVE:
                continue
            try:
                kids = parse_chunks(ch.payload, strict=False)
            except Exception:
                continue
            if not kids or kids[0].type_id != RW_STRUCT or len(kids[0].payload) < 88:
                return False, 'Invalid TextureNative Struct'
            sp = kids[0].payload
            platform = struct.unpack_from('<I', sp, 0)[0]
            if platform != PLATFORM_D3D9:
                continue
            w = struct.unpack_from('<H', sp, 80)[0]
            h = struct.unpack_from('<H', sp, 82)[0]
            levels = sp[85]
            fourcc = struct.unpack_from('<I', sp, 76)[0]
            if w < 1 or h < 1 or levels < 1:
                return False, 'Invalid native dimensions/levels'
            if fourcc not in (D3DFMT_DXT1, D3DFMT_DXT3, D3DFMT_DXT5, 0, 21, 22):
                continue
            # Only require the BASE mip to be present and non-zero.
            if len(sp) < 92:
                return False, 'Struct missing base mip header'
            size0 = struct.unpack_from('<I', sp, 88)[0]
            if size0 == 0:
                return False, f'Base mip size 0 ({w}x{h})'
            if 92 + size0 > len(sp):
                return False, 'Base mip overruns struct'
            if fourcc == 0:
                # Uncompressed 8888: base mip should be at least w*h*4
                if size0 < w * h * 4:
                    return False, f'Uncompressed base mip too small ({size0} < {w*h*4})'
        return True, 'OK'
    except Exception as exc:
        return False, str(exc)


def repair_txd_mips(data: bytes) -> bytes:
    """Truncate or pad mip chains so no level has size 0. Safe for original + generated TXDs."""
    try:
        if len(data) < 12:
            return data
        rt, rs, rv = struct.unpack_from('<III', data, 0)
        if rt != RW_TEXDICTIONARY or 12 + rs > len(data):
            return data
        top = parse_chunks(data[12:12 + rs], strict=False)
        rebuilt: List[Chunk] = []
        changed = False
        for ch in top:
            if ch.type_id != RW_TEXTURENATIVE:
                rebuilt.append(ch)
                continue
            try:
                kids = parse_chunks(ch.payload, strict=False)
            except Exception:
                rebuilt.append(ch)
                continue
            if not kids or kids[0].type_id != RW_STRUCT or len(kids[0].payload) < 88:
                rebuilt.append(ch)
                continue
            sp = kids[0].payload
            platform = struct.unpack_from('<I', sp, 0)[0]
            if platform != PLATFORM_D3D9:
                rebuilt.append(ch)
                continue
            fourcc = struct.unpack_from('<I', sp, 76)[0]
            if fourcc not in (D3DFMT_DXT1, D3DFMT_DXT3, D3DFMT_DXT5):
                rebuilt.append(ch)
                continue
            w = struct.unpack_from('<H', sp, 80)[0]
            h = struct.unpack_from('<H', sp, 82)[0]
            nlevels = sp[85]
            dxt_mode = 'DXT5' if fourcc == D3DFMT_DXT5 else ('DXT3' if fourcc == D3DFMT_DXT3 else 'DXT1')
            pos = 88
            good: List[bytes] = []
            cw, chh = w, h
            for level in range(nlevels):
                if pos + 4 > len(sp):
                    break
                size = struct.unpack_from('<I', sp, pos)[0]
                pos += 4
                if size <= 0 or pos + size > len(sp):
                    break  # stop at first bad mip — keep what we have
                block = sp[pos:pos + size]
                pos += size
                exp = _dxt_level_size(cw, chh, dxt_mode)
                if len(block) < exp:
                    block = block + b'\0' * (exp - len(block))
                elif len(block) > exp:
                    block = block[:exp]
                good.append(block)
                cw, chh = _next_mip_dims(cw, chh)
            if not good:
                rebuilt.append(ch)
                continue
            if len(good) != nlevels:
                changed = True
            head = bytearray(sp[:88])
            head[85] = len(good)
            if len(good) > 1:
                rf = struct.unpack_from('<I', head, 72)[0]
                struct.pack_into('<I', head, 72, rf | RASTER_MIPMAP)
            else:
                rf = struct.unpack_from('<I', head, 72)[0]
                struct.pack_into('<I', head, 72, rf & ~RASTER_MIPMAP)
            raster = bytearray()
            for blk in good:
                raster += struct.pack('<I', len(blk)) + blk
            new_struct = Chunk(kids[0].type_id, kids[0].version, bytes(head) + raster)
            nk = [new_struct] + kids[1:]
            rebuilt.append(Chunk(ch.type_id, ch.version, b''.join(k.pack() for k in nk)))
        if not changed and len(rebuilt) == len(top):
            # still rebuild to normalize
            pass
        payload = b''.join(c.pack() for c in rebuilt)
        return struct.pack('<III', rt, len(payload), rv) + payload
    except Exception:
        return data


def replace_txd(data: bytes, txd_name: str, index: PngIndex, allow_fallback: bool = True, player_strict: bool = False) -> Tuple[Optional[bytes], int, List[str], List[str], List[str]]:
    if len(data) < 12:
        raise TxdError('TXD too small')
    rt, rs, rv = struct.unpack_from('<III', data, 0)
    if rt != RW_TEXDICTIONARY:
        raise TxdError('Not a RenderWare TextureDictionary')
    if 12 + rs > len(data):
        raise TxdError('TXD root chunk truncated')
    top = parse_chunks(data[12:12 + rs])
    replaced = 0
    names: List[str] = []
    warnings: List[str] = []
    details: List[str] = []
    rebuilt: List[Chunk] = []
    for ch in top:
        if ch.type_id != RW_TEXTURENATIVE:
            rebuilt.append(ch)
            continue
        kids = parse_chunks(ch.payload)
        if not kids or kids[0].type_id != RW_STRUCT:
            rebuilt.append(ch)
            warnings.append('TextureNative missing Struct')
            continue
        sp = kids[0].payload
        if len(sp) < 88:
            rebuilt.append(ch)
            warnings.append('Short TextureNative Struct')
            continue
        name = cstr(sp[8:40])
        png, collision = index.resolve(name, txd_name, player_strict=player_strict)
        if png is None:
            # Visible log for important player textures so skip is never silent
            ln = name.lower()
            if any(k in ln for k in ('beard', 'afro', 'face', 'head', 'torso', 'legs', 'hair', 'goatee')):
                details.append(f"  SKIP_NO_PNG {name} (txd={txd_name})")
            rebuilt.append(ch)
            continue
        if collision > 1:
            warnings.append(f'{name}: {collision} exact PNGs — used best folder match')
        try:
            new_struct, info = replace_native_struct(kids[0], png)
            nk = [new_struct] + kids[1:]
            rebuilt.append(Chunk(ch.type_id, ch.version, b''.join(k.pack() for k in nk)))
            replaced += 1
            names.append(name)
            details.append(
                f"  REPLACE {info.name}: orig={info.orig_w}x{info.orig_h} → new={info.new_w}x{info.new_h} "
                f"mips={info.mip_count} fmt={info.fourcc} alpha={info.alpha} "
                f"ped={info.ped_safe} face={info.face_safe} hair={info.hair_safe} beard={info.beard_safe} "
                f"png={Path(info.png_path).name}"
            )
        except Exception as e:
            warnings.append(f'{name}: {e}')
            rebuilt.append(ch)
    if not replaced:
        return None, 0, names, warnings, details
    payload = b''.join(c.pack() for c in rebuilt)
    out = struct.pack('<III', rt, len(payload), rv) + payload + data[12 + rs:]
    # Validate only. DO NOT run repair_txd_mips here:
    # repair_txd_mips() rewrites every DXT texture in the TXD, including
    # textures that had no matching PNG. That violates the mirror guarantee
    # that unmatched TextureNative chunks remain byte-for-byte untouched.
    ok, reason = validate_txd_blob(out)
    if not ok:
        warnings.append(f'TXD validation failed after replacement ({reason}) — original TXD kept')
        return None, 0, names, warnings, details
    return out, replaced, names, warnings, details

def replace_txd_single_mip(data: bytes, txd_name: str, index: 'PngIndex', player_strict: bool = False) -> Tuple[Optional[bytes], int, List[str], List[str], List[str]]:
    """Fallback: replace textures but force exactly 1 mip level (last resort). No recursion."""
    # Call replace without allowing further fallback
    new, count, names, warnings, details = replace_txd(data, txd_name, index, allow_fallback=False, player_strict=player_strict)
    if new is None:
        return None, 0, names, warnings, details
    # Re-parse and force each TextureNative to a single mip
    try:
        rt, rs, rv = struct.unpack_from('<III', new, 0)
        top = parse_chunks(new[12:12 + rs])
        rebuilt: List[Chunk] = []
        for ch in top:
            if ch.type_id != RW_TEXTURENATIVE:
                rebuilt.append(ch)
                continue
            kids = parse_chunks(ch.payload)
            if not kids or kids[0].type_id != RW_STRUCT or len(kids[0].payload) < 88:
                rebuilt.append(ch)
                continue
            sp = bytearray(kids[0].payload)
            fourcc = struct.unpack_from('<I', sp, 76)[0]
            if fourcc not in (D3DFMT_DXT1, D3DFMT_DXT3, D3DFMT_DXT5):
                rebuilt.append(ch)
                continue
            w = struct.unpack_from('<H', sp, 80)[0]
            h = struct.unpack_from('<H', sp, 82)[0]
            dxt_mode = 'DXT5' if fourcc == D3DFMT_DXT5 else ('DXT3' if fourcc == D3DFMT_DXT3 else 'DXT1')
            # keep only first mip
            if len(sp) < 92:
                rebuilt.append(ch)
                continue
            first_size = struct.unpack_from('<I', sp, 88)[0]
            first_data = bytes(sp[92:92 + first_size])
            exp = _dxt_level_size(w, h, dxt_mode)
            if len(first_data) < exp:
                first_data = first_data + b'\0' * (exp - len(first_data))
            first_data = first_data[:exp]
            sp[85] = 1
            # clear MIPMAP bit — critical for "Mip Mapping OFF" crash fix
            rf = struct.unpack_from('<I', sp, 72)[0]
            struct.pack_into('<I', sp, 72, rf & ~RASTER_MIPMAP)
            new_payload = bytes(sp[:88]) + struct.pack('<I', exp) + first_data
            nk = [Chunk(kids[0].type_id, kids[0].version, new_payload)] + kids[1:]
            rebuilt.append(Chunk(ch.type_id, ch.version, b''.join(k.pack() for k in nk)))
        payload = b''.join(c.pack() for c in rebuilt)
        out = struct.pack('<III', rt, len(payload), rv) + payload
        return out, count, names, warnings + ['SINGLE-MIP FALLBACK USED'], details
    except Exception as e:
        return None, 0, names, warnings + [f'single-mip fallback failed: {e}'], details

def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, path)

def load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if state.get('schema_version') != STATE_SCHEMA_VERSION:
            return {'completed': [], 'stats': {}, '_reset': True}
        return state
    except Exception:
        return {'completed': [], 'stats': {}, '_reset': True}

def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def write_name_diagnostics(
    out: Path,
    tag: str,
    png_stems: Set[str],
    txd_names: Set[str],
    dff_names: Set[str],
    log,
) -> None:
    """Write clear reports so user can see WHY PNGs did not match."""
    matched = png_stems & txd_names
    only_png = sorted(png_stems - txd_names)
    only_txd = sorted(txd_names - png_stems)
    dff_only = sorted(dff_names - txd_names - png_stems)

    p = out / f'_diagnostic_names_{tag}.txt'
    with p.open('w', encoding='utf-8') as f:
        f.write(f'PNG unique stems: {len(png_stems)}\n')
        f.write(f'TXD texture names found: {len(txd_names)}\n')
        f.write(f'Exact matches (PNG stem == TXD texture name): {len(matched)}\n')
        f.write(f'PNGs with NO matching texture name in this source: {len(only_png)}\n')
        f.write(f'TXD textures with NO matching PNG: {len(only_txd)}\n')
        f.write(f'DFF string refs not in TXD/PNG (informational): {len(dff_only)}\n')
        f.write('\n=== PNGs that did NOT match any TXD texture name (first 200) ===\n')
        for n in only_png[:200]:
            f.write(n + '\n')
        f.write('\n=== TXD texture names that have no PNG (first 200) ===\n')
        for n in only_txd[:200]:
            f.write(n + '\n')
        if dff_only:
            f.write('\n=== Sample DFF-referenced names not in TXDs (first 50) ===\n')
            for n in dff_only[:50]:
                f.write(n + '\n')
    log(f'Diagnostic written: {p}')
    log(f'  Exact PNG↔TXD matches possible: {len(matched):,}')
    log(f'  PNGs with no TXD texture of same name: {len(only_png):,}')
    log(f'  Matching: protected=strict hard-bind; non-protected=broad (exact/suffix/prefix/token)')
    log(f'  (same broad rules as v61 OTHER-IMG for ordinary clothing/world textures)')
    log(f'  v19: never keys alpha from dark RGB; original alpha is recovered when PNG export lost it.')

class WorkerIMG:
    def __init__(self, img: str, png: str, out: str, log):
        self.img = Path(img)
        self.png = Path(png)
        self.out = Path(out)
        self.log = log
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop = False

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def run(self) -> Tuple[dict, bool]:
        img_path = self.img.expanduser().resolve() if self.img.exists() else self.img
        if not img_path.is_file():
            raise ValueError(f'IMG not found: {self.img}\nUse Browse and select the .img file.')
        self.img = img_path
        if not self.png.is_dir():
            raise ValueError(f'PNG folder not found: {self.png}')
        self.out.mkdir(parents=True, exist_ok=True)

        archive = ImgV2(self.img)
        idx = PngIndex(self.png)
        player_strict = (self.img.name.casefold() == 'player.img')
        self.log(f'Player strict mode: {player_strict} (enabled only for exact filename player.img)')
        self.log(f'Mode: IMG')
        self.log(f'IMG: {self.img}')
        self.log(f'PNG folder: {self.png}')
        self.log(f'PNG indexed: {idx.count:,} (TXD-context + exact + underscore-suffix; protected player names hard-bound)')
        for probe in ('afrobeard', 'face', 'torso', 'legs', 'afrobeard_afrobeard'):
            n = len(idx.by_name.get(probe, []))
            self.log(f'  PNG probe "{probe}": {n} file(s)')
        self.log(f'IMG entries: {len(archive.entries):,}')

        txds = [e for e in archive.entries if e.name.casefold().endswith('.txd')]
        dffs = [e for e in archive.entries if e.name.casefold().endswith('.dff')]
        self.log(f'TXD entries: {len(txds):,}  |  DFF entries: {len(dffs):,}')
        self.log('Note: pixel data lives in TXDs. DFFs only *reference* texture names.')

        # Collect all texture names for diagnostics
        all_txd_names: Set[str] = set()
        all_dff_names: Set[str] = set()
        self.log('Scanning texture names inside TXDs (diagnostic)...')
        for e in txds:
            try:
                raw = archive.read_entry(e)
                if len(raw) >= 12 and struct.unpack_from('<I', raw, 0)[0] == RW_TEXDICTIONARY:
                    exact = 12 + struct.unpack_from('<I', raw, 4)[0]
                    if 12 <= exact <= len(raw):
                        raw = raw[:exact]
                for nm in list_texture_names_in_txd(raw):
                    if nm:
                        all_txd_names.add(nm.casefold())
            except Exception:
                pass
        self.log(f'Unique texture names in TXDs: {len(all_txd_names):,}')

        # Light DFF scan (optional, informational)
        self.log('Scanning DFF string refs (diagnostic, no pixel replace in DFF)...')
        for e in dffs[:500]:  # cap for speed
            try:
                raw = archive.read_entry(e)
                all_dff_names |= {x.casefold() for x in scan_dff_texture_name_refs(raw)}
            except Exception:
                pass
        write_name_diagnostics(
            self.out, self.img.stem,
            set(idx.by_name.keys()), all_txd_names, all_dff_names, self.log
        )

        mirror_txd = self.out / 'txd_files'
        mirror_txd.mkdir(exist_ok=True)
        img_stem = self.img.stem
        state_path = self.out / f'_txd_mirror_state_{img_stem}.json'
        report_path = self.out / f'_txd_mirror_report_{img_stem}.csv'
        detail_log_path = self.out / f'_txd_mirror_detail_{img_stem}.log'
        detail_lines: List[str] = []
        detail_lines.append(f'=== Detail log for {self.img.name} ===')
        detail_lines.append(f'PNG folder: {self.png}')


        state = load_state(state_path)
        reset_state = bool(state.pop('_reset', False))
        state['schema_version'] = STATE_SCHEMA_VERSION
        completed = set(state.get('completed', []))
        if reset_state:
            try:
                report_path.unlink(missing_ok=True)
            except Exception:
                pass
        stats = state.get('stats') or {
            'pngs': idx.count, 'img_entries': len(archive.entries), 'txds': len(txds),
            'changed_txds': 0, 'replaced_textures': 0, 'no_match_txds': 0,
            'errors': 0, 'warnings': 0,
        }
        rows: List[List] = []
        if report_path.exists():
            try:
                with report_path.open('r', encoding='utf-8-sig', newline='') as f:
                    rows = list(csv.reader(f))
            except Exception:
                rows = []
        if not rows:
            rows = [['entry_index', 'txd_name', 'offset_sector', 'stream_sectors',
                     'status', 'replaced_count', 'texture_names', 'warnings_or_error', 'output_file']]

        for n, e in enumerate(txds, 1):
            self.pause_event.wait()
            if self.stop:
                break
            key = e.name.casefold()
            if key in completed:
                prev = mirror_txd / e.name
                if prev.is_file():
                    candidate = prev.read_bytes()
                    ok, why = validate_txd_blob(candidate)
                    if ok:
                        e.data = candidate
                        self.log(f'[{n}/{len(txds)}] RESUME-LOAD {e.name}')
                        continue
                    self.log(f'[{n}/{len(txds)}] RESUME-INVALID {e.name}: {why} — rebuilding')
                completed.discard(key)
            status, count, names, warnings, outpath = 'NO_MATCH', 0, [], [], ''
            try:
                raw = archive.read_entry(e)
                if len(raw) >= 12 and struct.unpack_from('<I', raw, 0)[0] == RW_TEXDICTIONARY:
                    exact = 12 + struct.unpack_from('<I', raw, 4)[0]
                    if 12 <= exact <= len(raw):
                        raw = raw[:exact]
                new, count, names, warnings, details = replace_txd(raw, e.name, idx, player_strict=player_strict)
                used_single_mip = False
                if new is not None:
                    # IMPORTANT: never rewrite unrelated TextureNative chunks here.
                    # Only the exact-matched texture(s) were rebuilt above.
                    ok, why = validate_txd_blob(new)
                    if not ok:
                        # last resort only: single-mip
                        new2, c2, n2, w2, d2 = replace_txd_single_mip(raw, e.name, idx, player_strict=player_strict)
                        if new2 is not None:
                            ok2, why2 = validate_txd_blob(new2)
                            if ok2:
                                new, count, names, details = new2, c2, n2, d2
                                warnings = list(warnings) + list(w2) + [f'SINGLE-MIP because: {why}']
                                used_single_mip = True
                            else:
                                raise TxdError(f'validation failed: {why} | single-mip: {why2}')
                        else:
                            raise TxdError(f'Generated TXD failed structural validation: {why}')
                    e.data = new
                    dest = mirror_txd / e.name
                    atomic_write(dest, new)
                    outpath = str(dest)
                    stats['changed_txds'] += 1
                    stats['replaced_textures'] += count
                    status = 'CHANGED'
                    if used_single_mip:
                        status = 'CHANGED-SINGLE-MIP'
                else:
                    stats['no_match_txds'] += 1
                stats['warnings'] += len(warnings)
                self.log(f'[{n}/{len(txds)}] {status}: {e.name} ({count} textures)')
                for dline in details:
                    self.log(dline)
                    detail_lines.append(dline)
                detail_lines.append(f'{status}\t{e.name}\treplaced={count}\ttextures={names}\twarnings={warnings}')
                for w in warnings:
                    self.log('  WARN: ' + w)
            except Exception as ex:
                stats['errors'] += 1
                status, warnings = 'ERROR', [str(ex)]
                self.log(f'[{n}/{len(txds)}] ERROR: {e.name}: {ex}')
            rows.append([e.index, e.name, e.offset, e.stream_sectors or e.size_sectors,
                         status, count, '; '.join(names), ' | '.join(warnings), outpath])
            completed.add(key)
            state = {'schema_version': STATE_SCHEMA_VERSION, 'completed': sorted(completed), 'stats': stats,
                     'img': str(self.img), 'png': str(self.png), 'output': str(self.out),
                     'updated': time.time()}
            save_state(state_path, state)
            with report_path.open('w', encoding='utf-8-sig', newline='') as f:
                csv.writer(f).writerows(rows)

        # Do NOT copy every file from mirror_txd here. Only e.data produced/validated by
        # this run (or explicitly resumed above) is allowed into the rebuilt IMG.
        out_img = self.out / self.img.name
        self.log(f'Building complete rebuilt {self.img.name}...')
        try:
            archive.write_rebuilt(out_img, self.log)
            self.log(f'SUCCESS: {out_img}')
            self.log(f'Copy over original models\\{self.img.name} (backup first!)')
        except Exception as ex:
            self.log(f'ERROR rebuilding IMG: {ex}')
            traceback.print_exc()
            stats['errors'] += 1
        state['finished'] = len(completed) == len(txds)
        save_state(state_path, state)
        try:
            detail_log_path.write_text('\n'.join(detail_lines) + '\n', encoding='utf-8')
            self.log(f'Detail log: {detail_log_path}')
        except Exception as ex:
            self.log(f'Could not write detail log: {ex}')
        return stats, state['finished']

class WorkerTxdFolder:
    """Process a folder full of .txd files → mirrored .txd files in output/txd_files."""
    def __init__(self, txd_dir: str, png: str, out: str, log):
        self.txd_dir = Path(txd_dir)
        self.png = Path(png)
        self.out = Path(out)
        self.log = log
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop = False

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def run(self) -> Tuple[dict, bool]:
        if not self.txd_dir.is_dir():
            raise ValueError(f'TXD folder not found: {self.txd_dir}')
        if not self.png.is_dir():
            raise ValueError(f'PNG folder not found: {self.png}')
        self.out.mkdir(parents=True, exist_ok=True)
        idx = PngIndex(self.png)
        txd_paths = sorted(self.txd_dir.rglob('*.txd'))
        self.log(f'Mode: TXD FOLDER')
        self.log(f'TXD folder: {self.txd_dir}')
        self.log(f'TXD files found: {len(txd_paths):,}')
        self.log(f'PNG indexed: {idx.count:,} (TXD-context + exact + underscore-suffix; protected player names hard-bound)')
        for probe in ('afrobeard', 'face', 'torso', 'legs', 'afrobeard_afrobeard'):
            n = len(idx.by_name.get(probe, []))
            self.log(f'  PNG probe "{probe}": {n} file(s)')

        all_txd_names: Set[str] = set()
        for p in txd_paths:
            try:
                raw = p.read_bytes()
                for nm in list_texture_names_in_txd(raw):
                    if nm:
                        all_txd_names.add(nm.casefold())
            except Exception:
                pass
        write_name_diagnostics(
            self.out, 'txd_folder',
            set(idx.by_name.keys()), all_txd_names, set(), self.log
        )

        mirror = self.out / 'txd_files'
        mirror.mkdir(exist_ok=True)
        state_path = self.out / '_txd_mirror_state_txd_folder.json'
        report_path = self.out / '_txd_mirror_report_txd_folder.csv'
        state = load_state(state_path)
        reset_state = bool(state.pop('_reset', False))
        state['schema_version'] = STATE_SCHEMA_VERSION
        completed = set(state.get('completed', []))
        if reset_state:
            try:
                report_path.unlink(missing_ok=True)
            except Exception:
                pass
        stats = state.get('stats') or {
            'pngs': idx.count, 'txds': len(txd_paths),
            'changed_txds': 0, 'replaced_textures': 0, 'no_match_txds': 0,
            'errors': 0, 'warnings': 0,
        }
        rows: List[List] = [['txd_path', 'status', 'replaced_count', 'texture_names', 'warnings_or_error', 'output_file']]

        for n, tp in enumerate(txd_paths, 1):
            self.pause_event.wait()
            if self.stop:
                break
            key = str(tp.relative_to(self.txd_dir)).casefold()
            if key in completed:
                self.log(f'[{n}/{len(txd_paths)}] RESUME-SKIP {tp.name}')
                continue
            status, count, names, warnings, outpath = 'NO_MATCH', 0, [], [], ''
            try:
                raw = tp.read_bytes()
                if len(raw) >= 12 and struct.unpack_from('<I', raw, 0)[0] == RW_TEXDICTIONARY:
                    exact = 12 + struct.unpack_from('<I', raw, 4)[0]
                    if 12 <= exact <= len(raw):
                        raw = raw[:exact]
                new, count, names, warnings, details = replace_txd(raw, tp.name, idx)
                used_single_mip = False
                if new is not None:
                    # Do NOT globally repair the TXD here. Unmatched textures
                    # must remain untouched byte-for-byte.
                    ok, why = validate_txd_blob(new)
                    if not ok:
                        raise TxdError(f'Generated TXD failed structural validation: {why}')
                    rel = tp.relative_to(self.txd_dir)
                    dest = mirror / rel
                    atomic_write(dest, new)
                    outpath = str(dest)
                    stats['changed_txds'] += 1
                    stats['replaced_textures'] += count
                    status = 'CHANGED'
                    if used_single_mip:
                        status = 'CHANGED-SINGLE-MIP'
                else:
                    stats['no_match_txds'] += 1
                stats['warnings'] += len(warnings)
                self.log(f'[{n}/{len(txd_paths)}] {status}: {tp.name} ({count} textures)')
                for dline in details:
                    self.log(dline)
                for w in warnings:
                    self.log('  WARN: ' + w)
            except Exception as ex:
                stats['errors'] += 1
                status, warnings = 'ERROR', [str(ex)]
                self.log(f'[{n}/{len(txd_paths)}] ERROR: {tp.name}: {ex}')
            rows.append([str(tp), status, count, '; '.join(names), ' | '.join(warnings), outpath])
            completed.add(key)
            state = {'schema_version': STATE_SCHEMA_VERSION, 'completed': sorted(completed), 'stats': stats, 'updated': time.time()}
            save_state(state_path, state)
            with report_path.open('w', encoding='utf-8-sig', newline='') as f:
                csv.writer(f).writerows(rows)

        self.log(f'Done. Mirrored TXDs in: {mirror}')
        state['finished'] = len(completed) == len(txd_paths)
        save_state(state_path, state)
        return stats, state.get('finished', False)

def main_gui() -> None:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    root = tk.Tk()
    root.title('GTA SA — TXD Mirror Builder FINAL (half PNG size)')
    root.geometry('940x720')
    mode = tk.StringVar(value='img')
    imgv = tk.StringVar(value=str(DEFAULT_IMG))
    txdfolderv = tk.StringVar(value='')
    pngv = tk.StringVar(value=str(DEFAULT_PNG))
    outv = tk.StringVar(value=str(DEFAULT_OUT))
    top = ttk.Frame(root, padding=12)
    top.pack(fill='both', expand=True)

    ttk.Label(top, text='FINAL: TXD size = half of PNG → 64/128/256/512/1024/2048 • format+alpha preserved',
              font=('', 9, 'italic')).pack(anchor='w', pady=(0, 6))

    mf = ttk.Frame(top)
    mf.pack(fill='x', pady=4)
    ttk.Label(mf, text='Mode', width=18).pack(side='left')
    ttk.Radiobutton(mf, text='IMG file (gta3 / gta_int / player / cutscene…)', variable=mode, value='img').pack(side='left')
    ttk.Radiobutton(mf, text='Folder of .txd files', variable=mode, value='txd_folder').pack(side='left', padx=12)

    def browse_img():
        p = filedialog.askopenfilename(title='Select .img', filetypes=[('IMG', '*.img'), ('All', '*.*')],
                                       initialdir=r'H:\\Grand Theft Auto  San Andreas\\models')
        if p:
            imgv.set(p)

    def browse_txd_folder():
        p = filedialog.askdirectory(title='Select folder containing .txd files')
        if p:
            txdfolderv.set(p)

    def browse_png():
        p = filedialog.askdirectory(title='Select folder with upscaled PNGs', initialdir=r'H:\\upscaled')
        if p:
            pngv.set(p)

    def browse_out():
        p = filedialog.askdirectory(title='Select Mirror output folder', initialdir=r'H:\\GTA3_MIRROR')
        if p:
            outv.set(p)

    for label, var, cmd in [
        ('Input .img', imgv, browse_img),
        ('TXD folder', txdfolderv, browse_txd_folder),
        ('Upscaled PNGs', pngv, browse_png),
        ('Mirror output', outv, browse_out),
    ]:
        f = ttk.Frame(top)
        f.pack(fill='x', pady=3)
        ttk.Label(f, text=label, width=18).pack(side='left')
        ttk.Entry(f, textvariable=var).pack(side='left', fill='x', expand=True, padx=(0, 4))
        ttk.Button(f, text='Browse...', command=cmd, width=10).pack(side='left')

    txt = tk.Text(top, wrap='none', height=24)
    txt.pack(fill='both', expand=True, pady=10)
    status = tk.StringVar(value='Ready — check _diagnostic_names_*.txt after run to see name mismatches')
    ttk.Label(top, textvariable=status).pack(anchor='w')
    bar = ttk.Progressbar(top, mode='indeterminate')
    bar.pack(fill='x', pady=6)
    buttons = ttk.Frame(top)
    buttons.pack(fill='x')
    worker_box: dict = {'w': None}
    running = {'v': False}

    def log(s: str) -> None:
        root.after(0, lambda: (txt.insert('end', s + '\n'), txt.see('end')))

    def start() -> None:
        if running['v']:
            return
        txt.delete('1.0', 'end')
        running['v'] = True
        status.set('Running...')
        bar.start(12)
        if mode.get() == 'txd_folder':
            w = WorkerTxdFolder(txdfolderv.get(), pngv.get(), outv.get(), log)
        else:
            w = WorkerIMG(imgv.get(), pngv.get(), outv.get(), log)
        worker_box['w'] = w
        def job():
            try:
                st, done = w.run()
                root.after(0, lambda s=st, d=done: finish(s, d))
            except Exception as e:
                root.after(0, lambda msg=str(e): fail(msg))
        threading.Thread(target=job, daemon=True).start()

    def pause() -> None:
        w = worker_box['w']
        if w and running['v']:
            w.pause()
            status.set('Paused')

    def resume() -> None:
        w = worker_box['w']
        if w and running['v']:
            w.resume()
            status.set('Running...')

    def finish(st: dict, done: bool) -> None:
        running['v'] = False
        bar.stop()
        status.set('DONE' if done else 'Stopped')
        messagebox.showinfo(
            'Finished',
            f"Changed TXDs: {st.get('changed_txds', 0):,}\n"
            f"Replaced textures: {st.get('replaced_textures', 0):,}\n"
            f"No-match TXDs: {st.get('no_match_txds', 0):,}\n"
            f"Warnings: {st.get('warnings', 0):,}\n"
            f"Errors: {st.get('errors', 0):,}\n\n"
            f"A COMPLETE rebuilt .img (or mirrored TXDs) is in the Mirror folder.\n"
            f"Copy over the original (backup first!).\n\n"
            f"v16 uses PNG alpha only so solid walls stay solid; fences/plants keep transparency\n"
            f"and glass stay visible. Check _diagnostic_names_*.txt\n"
            f"for any remaining name mismatches."
        )

    def fail(e: str) -> None:
        running['v'] = False
        bar.stop()
        status.set('Failed')
        messagebox.showerror('Error', e)

    ttk.Button(buttons, text='START / RESUME', command=start).pack(side='left', padx=3)
    ttk.Button(buttons, text='PAUSE', command=pause).pack(side='left', padx=3)
    ttk.Button(buttons, text='CONTINUE', command=resume).pack(side='left', padx=3)
    ttk.Button(buttons, text='EXIT', command=root.destroy).pack(side='right', padx=3)
    root.mainloop()

def cli() -> int:
    if len(sys.argv) == 4:
        WorkerIMG(*sys.argv[1:], print).run()
        return 0
    if len(sys.argv) == 5 and sys.argv[1] == '--txd-folder':
        WorkerTxdFolder(sys.argv[2], sys.argv[3], sys.argv[4], print).run()
        return 0
    main_gui()
    return 0

if __name__ == '__main__':
    raise SystemExit(cli())
