"""GTA SA texture knowledge base.

The built-in database is deliberately conservative: filename tokens are context,
not visual truth. Entries can say that a token is a material, a vehicle/model ID,
a map/zone code, a texture-part suffix, or an unknown asset identifier. Custom TXT
files use: keyword<TAB>description<TAB>category (category optional).
"""
from __future__ import annotations
from pathlib import Path
import re
from functools import lru_cache


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def load_txt(path: Path) -> dict[str, tuple[str, str]]:
    out={}
    if not path or not Path(path).is_file(): return out
    for line in Path(path).read_text(encoding='utf-8', errors='ignore').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or line.startswith(';'): continue
        parts=re.split(r'\t+|\s*\|\s*|\s*=\s*', line, maxsplit=2)
        if len(parts)<2: continue
        key=_norm(parts[0]); desc=parts[1].strip(); cat=parts[2].strip() if len(parts)>2 else 'custom context'
        if key and desc: out[key]=(desc,cat)
    return out

@lru_cache(maxsize=8)
def _load_cached(path_str: str):
    return load_txt(Path(path_str))


def load_knowledge(cfg) -> dict[str, tuple[str,str]]:
    merged={}
    try:
        if getattr(cfg.knowledge,'enabled',True):
            built=cfg.resolve_path(getattr(cfg.knowledge,'builtin_file','knowledge/gta_sa_keywords.txt'))
            merged.update(_load_cached(str(built)))
            custom=getattr(cfg.knowledge,'custom_txt','') or getattr(cfg.inference,'custom_knowledge_txt','')
            if custom:
                cp=Path(custom)
                if not cp.is_absolute(): cp=cfg.resolve_path(custom)
                merged.update(_load_cached(str(cp)))
    except Exception:
        pass
    return merged


def matched_context(filename: str, cfg, limit: int = 12) -> list[tuple[str,str,str]]:
    db=load_knowledge(cfg)
    if not db: return []
    stem=_norm(Path(filename).stem)
    tokens=[]
    for raw in stem.split('_'):
        if not raw: continue
        tokens.append(raw)
        # GTA names commonly concatenate model/part/size, e.g. sandking92interior128.
        # Split alpha<->digit and digit<->alpha boundaries so known keywords remain discoverable.
        parts=[x for x in re.split(r'(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])', raw) if x]
        if len(parts)>1: tokens.extend(parts)
        # Also recognize known keyword substrings only when they occupy a clear alpha run.
    # Also exact stem lookup first.
    candidates=[]
    if stem in db: candidates.append((stem,*db[stem]))
    for t in tokens:
        if t in db and not any(x[0]==t for x in candidates): candidates.append((t,*db[t]))
    return candidates[:max(1,int(limit))]


def format_context(filename: str, cfg) -> str:
    if not getattr(cfg.inference,'gta_knowledge_base',True): return ''
    rows=matched_context(filename,cfg,getattr(cfg.knowledge,'max_prompt_entries',12))
    if not rows: return ''
    safe=[]
    for k,d,c in rows:
        safe.append(f"{k}: {d} ({c})")
    return "GTA SA asset knowledge context (weak guidance; pixels override it): " + '; '.join(safe) + '.'
