#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
  TEXTURE QA TOOL - GTA San Andreas Texture Pack Reviewer
  Production-ready desktop application for reviewing ~21,000 PNG texture pairs
================================================================================

REQUIREMENTS
------------
  Python 3.10+
  pip install customtkinter pillow

  On Linux you may also need:  sudo apt install python3-tk

HOW TO RUN
----------
  GUI (interactive review):
    python qa_texture_tool.py

  Headless auto scan (no window, fast, recommended for bulk):
    python qa_texture_tool.py -auto
        (uses folders saved from last GUI session)

    python qa_texture_tool.py -auto --original "D:/orig" --upscaled "D:/up" --fucked "D:/bad"
    python qa_texture_tool.py -auto --threshold 50 --copy   # copy instead of move

STARTUP (GUI)
-------------
  1. Select three folders:
       - Original folder   (never modified)
       - Upscaled folder   (never deleted from; only optionally moved)
       - fucked_up folder  (destination for bad upscales)
  2. The tool matches files by filename (case-insensitive).
  3. Progress is auto-saved every ~25 seconds and on exit to qa_session.json
     next to the script. On restart it resumes exactly where you left off.

KEYBOARD SHORTCUTS (Manual mode is default)
-------------------------------------------
  Navigation (main workflow)
    → / Space / S  Next (no mark)
    ←              Previous
    F or Delete    Mark FUCKED → move/copy → next
    ↑ / ↓          Jump ±10
    PageUp/Down    Jump ±50
    Home / End     First / Last
    A              Optional explicit Accept
    Z / Ctrl+Z     Undo

  Utility
    C              Copy current filename to clipboard
    R              Reload current pair
    H              Toggle help overlay
    E              Open upscaled file in external editor
    Ctrl+E         Reveal upscaled file in file manager
    M              Toggle Automatic mode
    D              Toggle Difference (heatmap) view
    Esc            Close help overlay / cancel

SAFETY RULES (STRICTLY ENFORCED)
--------------------------------
  • Original folder is NEVER written to, deleted from, moved or renamed.
  • Upscaled folder is NEVER deleted from.
  • Bad files are COPIED (default) or MOVEd into fucked_up only.
  • Full Undo support restores files from fucked_up.
  • All critical actions are logged and reversible.

PERFORMANCE
-----------
  • Background threaded image loading
  • LRU cache of recent images (configurable)
  • UI never freezes on large 1k–2k textures
  • Handles 20k+ files gracefully

AUTHOR / LICENSE
----------------
  Created for high-volume texture QA. Free to use and modify.
================================================================================
"""

from __future__ import annotations

import os
import sys
import json
import time
import math
import shutil
import hashlib
import threading
import queue
import traceback
import platform
import subprocess
from pathlib import Path
from collections import OrderedDict, deque
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple, Any, Callable
from datetime import datetime

# GUI
try:
    import customtkinter as ctk
    from customtkinter import CTkImage
except ImportError:
    print("ERROR: customtkinter is required.\n  pip install customtkinter")
    sys.exit(1)

import tkinter as tk
from tkinter import filedialog, messagebox

# Images
try:
    from PIL import Image, ImageTk, ImageOps, ImageEnhance, ImageFilter, ImageChops, ImageDraw, ImageStat
except ImportError:
    print("ERROR: Pillow is required.\n  pip install pillow")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants & Config
# ---------------------------------------------------------------------------
APP_NAME = "Texture QA Tool"
APP_VERSION = "1.2.0"
SESSION_FILENAME = "qa_session.json"
CACHE_SIZE = 24          # keep modest – prevents Tkinter/PhotoImage memory bloat after long sessions
AUTO_SAVE_INTERVAL = 20  # seconds – also saves on every exit
UNDO_HISTORY_MAX = 30
DEFAULT_ZOOM = 1.0
MIN_ZOOM = 0.1
MAX_ZOOM = 8.0
ZOOM_STEP = 1.15
JUMP_SMALL = 10
JUMP_LARGE = 50
HIGHLIGHT_MS = 280       # visual feedback duration

# Dark theme colours
BG_DARK = "#1a1a1a"
PANEL_BG = "#242424"
ACCENT = "#3b8ed0"
GREEN = "#2ecc71"
RED = "#e74c3c"
YELLOW = "#f1c40f"
TEXT = "#e0e0e0"
MUTED = "#888888"

SUPPORTED_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp", ".tif", ".tiff"}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class TexturePair:
    name: str                 # stem (without extension)
    original_path: Optional[Path] = None
    upscaled_path: Optional[Path] = None
    orig_w: int = 0
    orig_h: int = 0
    up_w: int = 0
    up_h: int = 0
    status: str = "pending"   # pending | accepted | fucked | skipped
    file_size_up: int = 0
    mtime_up: float = 0.0


@dataclass
class UndoEntry:
    action: str               # "fucked" | "accepted" | "skipped"
    pair_name: str
    previous_status: str
    fucked_path: Optional[str] = None   # if we copied/moved a file
    was_move: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class SessionData:
    original_folder: str = ""
    upscaled_folder: str = ""
    fucked_up_folder: str = ""
    last_index: int = 0
    accepted: List[str] = field(default_factory=list)
    fucked: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    filter_mode: str = "both"          # both | missing | fucked | all
    search_query: str = ""
    sort_by: str = "name"
    window_geometry: str = ""
    prefer_move: bool = True           # True = move (user preferred default)
    auto_threshold: float = 50.0
    linked_zoom: bool = False
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    show_diff: bool = False


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def safe_stem(path: Path) -> str:
    return path.stem.lower()


def is_image(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXT


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_in_explorer(path: Path):
    path = path.resolve()
    try:
        if platform.system() == "Windows":
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif platform.system() == "Darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            # Linux – open containing folder
            subprocess.run(["xdg-open", str(path.parent)], check=False)
    except Exception as e:
        print(f"Reveal failed: {e}")


def open_external_editor(path: Path):
    path = path.resolve()
    try:
        if platform.system() == "Windows":
            os.startfile(str(path))
        elif platform.system() == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as e:
        print(f"Open external failed: {e}")


# ---------------------------------------------------------------------------
# Threaded image loader + LRU cache
# ---------------------------------------------------------------------------
class ImageCache:
    """Thread-safe LRU cache of PIL Images / PhotoImages."""

    def __init__(self, maxsize: int = CACHE_SIZE):
        self.maxsize = maxsize
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key: str, value: Any):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self._cache.clear()


class AsyncImageLoader:
    """Background loader that never blocks the UI."""

    def __init__(self, cache: ImageCache):
        self.cache = cache
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._callbacks: Dict[str, List[Callable]] = {}
        self._cb_lock = threading.Lock()

    def request(self, path: Path, callback: Callable[[Optional[Image.Image], str], None],
                max_side: int = 2048):
        key = str(path.resolve())
        cached = self.cache.get(key)
        if cached is not None:
            callback(cached, key)
            return
        with self._cb_lock:
            if key not in self._callbacks:
                self._callbacks[key] = []
            self._callbacks[key].append(callback)
        self._queue.put((path, key, max_side))

    def _worker(self):
        while not self._stop.is_set():
            try:
                path, key, max_side = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            img = None
            try:
                with Image.open(path) as im:
                    im = im.convert("RGBA")
                    # Downscale very large images for display only
                    w, h = im.size
                    if max(w, h) > max_side:
                        ratio = max_side / max(w, h)
                        im = im.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)
                    img = im.copy()
                self.cache.put(key, img)
            except Exception as e:
                print(f"[Loader] Failed {path}: {e}")
                img = None
            with self._cb_lock:
                cbs = self._callbacks.pop(key, [])
            for cb in cbs:
                try:
                    cb(img, key)
                except Exception:
                    pass

    def shutdown(self):
        self._stop.set()


# ---------------------------------------------------------------------------
# Hybrid quality metrics: Classical + AlexNet perceptual features (torchvision)
# Goal: ~90% precision on catastrophic vs good upscales for GTA SA textures.
# Model size ~233MB (downloaded once). CPU inference ~30-80ms per pair.
# ---------------------------------------------------------------------------
import numpy as np

_PERCEPTUAL_NET = None
_PERCEPTUAL_TF = None
_PERCEPTUAL_LOCK = threading.Lock()


def _get_perceptual_net():
    """Lazy-load AlexNet feature extractor (once per process)."""
    global _PERCEPTUAL_NET, _PERCEPTUAL_TF
    if _PERCEPTUAL_NET is not None:
        return _PERCEPTUAL_NET, _PERCEPTUAL_TF
    with _PERCEPTUAL_LOCK:
        if _PERCEPTUAL_NET is not None:
            return _PERCEPTUAL_NET, _PERCEPTUAL_TF
        try:
            import torch
            from torchvision import models, transforms
            net = models.alexnet(weights=models.AlexNet_Weights.DEFAULT).features.eval()
            for p in net.parameters():
                p.requires_grad = False
            tf = transforms.Compose([
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                     std=[0.229, 0.224, 0.225]),
            ])
            _PERCEPTUAL_NET = net
            _PERCEPTUAL_TF = tf
            print("[Perceptual] AlexNet features loaded")
            return net, tf
        except Exception as e:
            print(f"[Perceptual] Failed to load model: {e}")
            _PERCEPTUAL_NET = False  # mark as unavailable
            return None, None


def _perceptual_distance(img_a: Image.Image, img_b: Image.Image) -> Optional[float]:
    """
    Feature-space distance using AlexNet.
    Returns roughly:
      ~0.05-0.25  = very similar / good upscale
      ~0.5-1.0    = moderate difference
      >1.2        = likely different object / severe change
    """
    net, tf = _get_perceptual_net()
    if net is None or net is False:
        return None
    try:
        import torch
        import torch.nn.functional as F
        xa = tf(img_a.convert("RGB")).unsqueeze(0)
        xb = tf(img_b.convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            fa = net(xa)
            fb = net(xb)
            # Raw MSE – do NOT per-channel normalize (that crushed dynamic range)
            dist = F.mse_loss(fa, fb).item()
        return float(dist)
    except Exception as e:
        print(f"[Perceptual] distance error: {e}")
        return None


def _to_gray_np(img: Image.Image, size: Optional[Tuple[int, int]] = None) -> np.ndarray:
    g = img.convert("L")
    if size is not None:
        g = g.resize(size, Image.Resampling.BILINEAR)
    return np.asarray(g, dtype=np.float64)


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape or a.size < 16:
        return 0.0
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    mu_a, mu_b = a.mean(), b.mean()
    sigma_a, sigma_b = a.var(), b.var()
    sigma_ab = ((a - mu_a) * (b - mu_b)).mean()
    num = (2 * mu_a * mu_b + C1) * (2 * sigma_ab + C2)
    den = (mu_a ** 2 + mu_b ** 2 + C1) * (sigma_a + sigma_b + C2)
    return float(num / den) if den != 0 else 1.0


def _local_ssim_min(a: np.ndarray, b: np.ndarray, tile: int = 32) -> float:
    h, w = a.shape
    if h < tile or w < tile:
        return _ssim(a, b)
    mins = []
    for y in range(0, h - tile + 1, tile):
        for x in range(0, w - tile + 1, tile):
            mins.append(_ssim(a[y:y+tile, x:x+tile], b[y:y+tile, x:x+tile]))
    return float(min(mins)) if mins else 0.0


CALIBRATION_FILENAME = "qa_calibration.json"
_CALIBRATION: Optional[Dict[str, Any]] = None
_CALIBRATION_LOADED = False


def _load_calibration() -> Optional[Dict[str, Any]]:
    """
    Load qa_calibration.json if it exists next to this script.
    That file is produced by calibrate_qa.py from YOUR OWN labeled examples
    (a folder of upscales you know are good + a folder you know are bad).
    When present, it replaces the hand-tuned heuristic below with a model
    fitted to your actual data — this is what gets you to reliable >90%
    accuracy instead of guessed magic numbers.
    """
    global _CALIBRATION, _CALIBRATION_LOADED
    if _CALIBRATION_LOADED:
        return _CALIBRATION
    _CALIBRATION_LOADED = True
    path = Path(__file__).resolve().parent / CALIBRATION_FILENAME
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if all(k in data for k in ("weights", "bias", "feature_order", "mu", "sigma")):
                _CALIBRATION = data
                n = data.get("n_good", 0) + data.get("n_bad", 0)
                acc = data.get("train_accuracy")
                acc_txt = f", {acc*100:.0f}% training accuracy" if acc is not None else ""
                print(f"[Calibration] Loaded qa_calibration.json (trained on {n} labeled examples{acc_txt})")
            else:
                print(f"[Calibration] {path.name} found but missing expected fields — ignoring.")
        except Exception as e:
            print(f"[Calibration] Failed to load {path}: {e}")
    return _CALIBRATION


def extract_raw_features(orig: Image.Image, up: Image.Image, pair: TexturePair) -> Dict[str, float]:
    """
    Compute the raw numeric QA signals for one (original, upscaled) pair.
    Shared by the built-in heuristic scorer AND by calibrate_qa.py, which
    uses these same numbers to fit a scorer against your labeled examples —
    so calibration always matches exactly what the tool measures at runtime.
    """
    real_ow = pair.orig_w if pair.orig_w > 0 else orig.size[0]
    real_oh = pair.orig_h if pair.orig_h > 0 else orig.size[1]
    real_uw = pair.up_w if pair.up_w > 0 else up.size[0]
    real_uh = pair.up_h if pair.up_h > 0 else up.size[1]
    pair.orig_w, pair.orig_h = real_ow, real_oh
    pair.up_w, pair.up_h = real_uw, real_uh

    if min(real_ow, real_oh, real_uw, real_uh) < 2:
        return {"degenerate": 1.0}

    ratio = max(real_uw / max(real_ow, 1), real_uh / max(real_oh, 1))

    # ---- Classical structure (downscale-and-compare) ----
    cmp_w, cmp_h = orig.size
    o_rgb = orig.convert("RGB")
    o_blur = o_rgb.filter(ImageFilter.BoxBlur(0.8))
    up_down = up.convert("RGB").resize((cmp_w, cmp_h), Image.Resampling.BOX)

    g_orig = _to_gray_np(o_blur)
    g_down = _to_gray_np(up_down)
    ssim_val = _ssim(g_orig, g_down)
    tile = max(16, min(cmp_w, cmp_h) // 4)
    local_min = _local_ssim_min(g_orig, g_down, tile=tile)

    # ---- Perceptual (AlexNet) ----
    perc = _perceptual_distance(o_rgb, up_down)
    perc2 = None
    try:
        mid = 128
        o_mid = o_rgb.resize((mid, mid), Image.Resampling.BILINEAR)
        u_mid = up.convert("RGB").resize((mid, mid), Image.Resampling.BILINEAR)
        perc2 = _perceptual_distance(o_mid, u_mid)
    except Exception:
        pass

    # ---- Colour ----
    o_small = o_rgb.resize((48, 48), Image.Resampling.BILINEAR)
    u_small = up_down.resize((48, 48), Image.Resampling.BILINEAR)
    o_mean = np.array(ImageStat.Stat(o_small).mean)
    u_mean = np.array(ImageStat.Stat(u_small).mean)
    mean_dist = float(np.linalg.norm(o_mean - u_mean)) / 255.0

    # ---- Artifacts ----
    lap_var = None
    try:
        g_up = _to_gray_np(up.convert("L"), size=(min(160, real_uw), min(160, real_uh)))
        lap = (g_up[1:-1, 1:-1] * 4
               - g_up[:-2, 1:-1] - g_up[2:, 1:-1]
               - g_up[1:-1, :-2] - g_up[1:-1, 2:])
        lap_var = float(lap.var())
    except Exception:
        pass

    return {
        "degenerate": 0.0,
        "ratio": ratio,
        "ssim": ssim_val,
        "local_min": local_min,
        "perc": perc if perc is not None else -1.0,
        "perc2": perc2 if perc2 is not None else -1.0,
        "mean_dist": mean_dist,
        # log-compressed so the huge dynamic range of Laplacian variance doesn't
        # dominate the (linear) calibrated model
        "lap_var_log": math.log1p(lap_var) if lap_var is not None else -1.0,
    }


def _score_heuristic(feats: Dict[str, float]) -> Tuple[float, str]:
    """
    Original hand-tuned scorer — used only as a fallback when no
    qa_calibration.json exists yet. This is guesswork tuned on assumption,
    not on your actual textures, which is why it can end up backwards on
    a given art style. Run calibrate_qa.py to replace it with a model fit
    to your own labeled examples.
    """
    reasons: List[str] = []
    score = 80.0

    if feats["ratio"] < 1.15:
        score -= 20
        reasons.append("almost no upscale")

    ssim_val = feats["ssim"]
    if ssim_val < 0.15:
        score -= 50
        reasons.append(f"extreme content change (SSIM {ssim_val:.2f})")
    elif ssim_val < 0.25:
        score -= 30
        reasons.append(f"major structure break (SSIM {ssim_val:.2f})")
    elif ssim_val < 0.35:
        score -= 12
        reasons.append(f"structure change (SSIM {ssim_val:.2f})")

    if feats["local_min"] < 0.12 and ssim_val < 0.40:
        score -= 12
        reasons.append("local hallucination")

    perc = feats["perc"]
    if perc >= 0:
        if perc > 1.3:
            score -= 50
            reasons.append(f"perceptual mismatch ({perc:.2f})")
        elif perc > 0.85:
            score -= 30
            reasons.append(f"strong perceptual shift ({perc:.2f})")
        elif perc > 0.50:
            score -= 12
            reasons.append(f"perceptual shift ({perc:.2f})")
        elif perc < 0.20:
            score += 6

    perc2 = feats["perc2"]
    if perc2 >= 0 and perc2 > 1.4:
        score -= 18
        reasons.append(f"object-level mismatch ({perc2:.2f})")

    mean_dist = feats["mean_dist"]
    if mean_dist > 0.42:
        score -= 18
        reasons.append("extreme colour shift")
    elif mean_dist > 0.32:
        score -= 7
        reasons.append("strong colour shift")

    lap_var_log = feats["lap_var_log"]
    if lap_var_log >= 0:
        lap_var = math.expm1(lap_var_log)
        if lap_var < 4:
            score -= 8
            reasons.append("severely over-smoothed")
        elif lap_var > 3000:
            score -= 6
            reasons.append("extreme noise")

    score = max(0.0, min(100.0, score))
    if not reasons:
        reasons.append("looks OK")
    return score, ", ".join(reasons[:3])


def _score_calibrated(feats: Dict[str, float], calib: Dict[str, Any]) -> Tuple[float, str]:
    """Score using a logistic model fitted by calibrate_qa.py on your labeled examples."""
    order = calib["feature_order"]
    mu = calib["mu"]
    sigma = calib["sigma"]
    w = calib["weights"]
    b = calib["bias"]
    z = b
    for key, mi, si, wi in zip(order, mu, sigma, w):
        xi = feats.get(key, 0.0)
        si = si if abs(si) > 1e-9 else 1.0
        z += ((xi - mi) / si) * wi
    z = max(-60.0, min(60.0, z))  # avoid overflow in exp
    prob_good = 1.0 / (1.0 + math.exp(-z))
    return prob_good * 100.0, "calibrated model"


def compute_auto_score(orig: Optional[Image.Image], up: Optional[Image.Image],
                       pair: TexturePair) -> Tuple[float, str]:
    """
    Hybrid score 0-100. Higher = keep.
    If qa_calibration.json exists (built by calibrate_qa.py from YOUR labeled
    good/bad examples), it is used — that's the path to reliable >90% accuracy,
    since it's fit to your actual art style instead of guessed constants.
    Otherwise falls back to the original hand-tuned heuristic.
    """
    if orig is None or up is None:
        return 30.0, "Missing image"

    try:
        feats = extract_raw_features(orig, up, pair)
    except Exception:
        return 40.0, "analysis error"

    if feats.get("degenerate"):
        return 5.0, "degenerate size"

    calib = _load_calibration()
    if calib:
        try:
            return _score_calibrated(feats, calib)
        except Exception as e:
            print(f"[Calibration] scoring error, falling back to heuristic: {e}")

    return _score_heuristic(feats)


def make_difference_heatmap(orig: Image.Image, up: Image.Image) -> Image.Image:
    """Simple absolute-difference heatmap (resized to match)."""
    try:
        o = orig.convert("RGB")
        u = up.convert("RGB")
        # Match sizes for comparison (use upscaled size)
        if o.size != u.size:
            o = o.resize(u.size, Image.Resampling.NEAREST)
        diff = ImageChops.difference(o, u)
        # Amplify
        diff = ImageEnhance.Contrast(diff).enhance(2.5)
        # Colourise a bit
        r, g, b = diff.split()
        heat = Image.merge("RGB", (r, Image.new("L", r.size, 0), Image.new("L", r.size, 0)))
        return heat
    except Exception:
        return up


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class TextureQAApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1400x900")
        self.minsize(1000, 700)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        # State
        self.pairs: List[TexturePair] = []
        self.filtered_indices: List[int] = []
        self.current_pos: int = 0          # index into filtered_indices
        self.session = SessionData()
        self.undo_stack: deque[UndoEntry] = deque(maxlen=UNDO_HISTORY_MAX)
        self.mode = "manual"               # manual | auto
        self.linked_zoom = False
        self.show_diff = False
        self.zoom_orig = DEFAULT_ZOOM
        self.zoom_up = DEFAULT_ZOOM
        self.pan_orig = [0, 0]
        self.pan_up = [0, 0]
        self._drag_start = None
        self._drag_panel = None
        self.brightness = 1.0
        self.contrast = 1.0
        self.saturation = 1.0
        self.prefer_move = True
        self.auto_threshold = 50.0
        self._last_action_msg = ""
        self._highlight_after = None
        self._auto_save_job = None
        self._help_visible = False
        self._loading = False

        # Caches & loader
        self.img_cache = ImageCache(CACHE_SIZE)
        self.loader = AsyncImageLoader(self.img_cache)
        self._photo_orig = None
        self._photo_up = None
        self._current_orig_img: Optional[Image.Image] = None
        self._current_up_img: Optional[Image.Image] = None

        # Paths
        self.original_folder: Optional[Path] = None
        self.upscaled_folder: Optional[Path] = None
        self.fucked_up_folder: Optional[Path] = None
        self.session_path = Path(__file__).resolve().parent / SESSION_FILENAME

        self._build_ui()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Try resume or ask for folders
        self.after(100, self._startup)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Top bar
        self.top_bar = ctk.CTkFrame(self, height=48, fg_color=PANEL_BG, corner_radius=0)
        self.top_bar.grid(row=0, column=0, sticky="ew")
        self.top_bar.grid_columnconfigure(4, weight=1)

        self.lbl_filename = ctk.CTkLabel(self.top_bar, text="No file", font=ctk.CTkFont(size=15, weight="bold"),
                                         text_color=TEXT, anchor="w")
        self.lbl_filename.grid(row=0, column=0, padx=(12, 8), pady=8, sticky="w")

        self.lbl_res = ctk.CTkLabel(self.top_bar, text="", font=ctk.CTkFont(size=13), text_color=MUTED)
        self.lbl_res.grid(row=0, column=1, padx=8, pady=8)

        self.lbl_progress = ctk.CTkLabel(self.top_bar, text="0 / 0", font=ctk.CTkFont(size=14, weight="bold"),
                                         text_color=ACCENT)
        self.lbl_progress.grid(row=0, column=2, padx=12, pady=8)

        self.lbl_mode = ctk.CTkLabel(self.top_bar, text="MANUAL", font=ctk.CTkFont(size=13, weight="bold"),
                                     text_color=GREEN)
        self.lbl_mode.grid(row=0, column=3, padx=8, pady=8)

        self.btn_folders = ctk.CTkButton(self.top_bar, text="Folders…", width=90, command=self._choose_folders)
        self.btn_folders.grid(row=0, column=5, padx=6, pady=6)

        self.btn_auto_scan = ctk.CTkButton(self.top_bar, text="Full Auto Scan", width=110,
                                           fg_color="#8e44ad", hover_color="#9b59b6",
                                           command=self._run_full_auto_scan)
        self.btn_auto_scan.grid(row=0, column=6, padx=6, pady=6)

        self.btn_export = ctk.CTkButton(self.top_bar, text="Export fucked", width=100,
                                        command=self._export_fucked_list)
        self.btn_export.grid(row=0, column=7, padx=6, pady=6)

        self.lbl_fucked_count = ctk.CTkLabel(self.top_bar, text="fucked: 0", font=ctk.CTkFont(size=12),
                                             text_color=RED)
        self.lbl_fucked_count.grid(row=0, column=8, padx=(4, 12), pady=8)

        # Main image area
        self.main_frame = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        self.main_frame.grid(row=1, column=0, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)

        # Left panel – Original
        self.left_panel = ctk.CTkFrame(self.main_frame, fg_color=PANEL_BG, corner_radius=6)
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        self.left_panel.grid_rowconfigure(1, weight=1)
        self.left_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.left_panel, text="ORIGINAL", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=MUTED).grid(row=0, column=0, pady=(6, 0))

        self.canvas_orig = tk.Canvas(self.left_panel, bg="#111111", highlightthickness=0)
        self.canvas_orig.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        self.canvas_orig.bind("<MouseWheel>", lambda e: self._on_zoom(e, "orig"))
        self.canvas_orig.bind("<Button-4>", lambda e: self._on_zoom(e, "orig"))  # Linux
        self.canvas_orig.bind("<Button-5>", lambda e: self._on_zoom(e, "orig"))
        self.canvas_orig.bind("<ButtonPress-1>", lambda e: self._on_drag_start(e, "orig"))
        self.canvas_orig.bind("<B1-Motion>", lambda e: self._on_drag_move(e, "orig"))
        self.canvas_orig.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas_orig.bind("<Configure>", lambda e: self._redraw_panel("orig"))

        # Right panel – Upscaled
        self.right_panel = ctk.CTkFrame(self.main_frame, fg_color=PANEL_BG, corner_radius=6)
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        self.right_panel.grid_rowconfigure(1, weight=1)
        self.right_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.right_panel, text="UPSCALED", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=MUTED).grid(row=0, column=0, pady=(6, 0))

        self.canvas_up = tk.Canvas(self.right_panel, bg="#111111", highlightthickness=0)
        self.canvas_up.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        self.canvas_up.bind("<MouseWheel>", lambda e: self._on_zoom(e, "up"))
        self.canvas_up.bind("<Button-4>", lambda e: self._on_zoom(e, "up"))
        self.canvas_up.bind("<Button-5>", lambda e: self._on_zoom(e, "up"))
        self.canvas_up.bind("<ButtonPress-1>", lambda e: self._on_drag_start(e, "up"))
        self.canvas_up.bind("<B1-Motion>", lambda e: self._on_drag_move(e, "up"))
        self.canvas_up.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas_up.bind("<Configure>", lambda e: self._redraw_panel("up"))

        # Bottom control / filter bar
        self.bottom_bar = ctk.CTkFrame(self, height=42, fg_color=PANEL_BG, corner_radius=0)
        self.bottom_bar.grid(row=2, column=0, sticky="ew")
        self.bottom_bar.grid_columnconfigure(3, weight=1)

        self.search_var = ctk.StringVar()
        self.search_entry = ctk.CTkEntry(self.bottom_bar, placeholder_text="Search filename…",
                                         textvariable=self.search_var, width=220)
        self.search_entry.grid(row=0, column=0, padx=(10, 6), pady=6)
        self.search_var.trace_add("write", lambda *_: self._apply_filters())

        self.filter_var = ctk.StringVar(value="both")
        self.filter_menu = ctk.CTkOptionMenu(
            self.bottom_bar, variable=self.filter_var,
            values=["both", "all", "missing", "fucked", "accepted", "pending"],
            width=110, command=lambda _: self._apply_filters()
        )
        self.filter_menu.grid(row=0, column=1, padx=4, pady=6)

        self.sort_var = ctk.StringVar(value="name")
        self.sort_menu = ctk.CTkOptionMenu(
            self.bottom_bar, variable=self.sort_var,
            values=["name", "resolution", "size", "mtime"],
            width=110, command=lambda _: self._apply_filters()
        )
        self.sort_menu.grid(row=0, column=2, padx=4, pady=6)

        self.lbl_status = ctk.CTkLabel(self.bottom_bar, text="Ready", font=ctk.CTkFont(size=12),
                                       text_color=MUTED, anchor="w")
        self.lbl_status.grid(row=0, column=3, padx=10, pady=6, sticky="ew")

        # Settings toggles
        self.chk_linked = ctk.CTkCheckBox(self.bottom_bar, text="Linked zoom",
                                          command=self._toggle_linked, width=100)
        self.chk_linked.grid(row=0, column=4, padx=4, pady=6)

        self.chk_diff = ctk.CTkCheckBox(self.bottom_bar, text="Diff view",
                                        command=self._toggle_diff, width=90)
        self.chk_diff.grid(row=0, column=5, padx=4, pady=6)

        self.chk_move = ctk.CTkCheckBox(self.bottom_bar, text="Move (not copy)",
                                        command=self._toggle_move, width=120)
        self.chk_move.select()  # default = Move
        self.chk_move.grid(row=0, column=6, padx=(4, 10), pady=6)

        # Help overlay (hidden)
        self.help_frame = ctk.CTkFrame(self, fg_color=("#1e1e1e", "#1e1e1e"), corner_radius=10)
        help_text = (
            "KEYBOARD SHORTCUTS\n"
            "─────────────────────────────────\n"
            "→ / Space / S   Next (just browse)\n"
            "←               Previous\n"
            "F / Delete      Mark FUCKED → next\n"
            "A               Optional Accept\n"
            "↑ ↓             Jump ±10\n"
            "PgUp/PgDn       Jump ±50\n"
            "Home / End      First / Last\n"
            "\n"
            "Z / Ctrl+Z      Undo\n"
            "C               Copy filename\n"
            "R               Reload pair\n"
            "E               External editor\n"
            "Ctrl+E          Reveal in Explorer\n"
            "M               Auto mode\n"
            "D               Diff heatmap\n"
            "H               This help\n"
            "\n"
            "Main flow: browse with →  press F only on bad ones.\n"
            "Position auto-saved every 20s + on exit."
        )
        self.help_label = ctk.CTkLabel(self.help_frame, text=help_text, font=ctk.CTkFont(family="Consolas", size=13),
                                       justify="left", text_color=TEXT)
        self.help_label.pack(padx=20, pady=16)

        # Status for auto score
        self.lbl_auto = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=13), text_color=YELLOW)
        # placed dynamically when needed

    def _bind_keys(self):
        # Pure navigation – NEVER auto-accept. User only presses F on bad ones.
        self.bind("<Left>", lambda e: self._nav(-1))
        self.bind("<Right>", lambda e: self._nav(1))
        self.bind("<Up>", lambda e: self._nav(-JUMP_SMALL))
        self.bind("<Down>", lambda e: self._nav(JUMP_SMALL))
        self.bind("<Prior>", lambda e: self._nav(-JUMP_LARGE))  # PageUp
        self.bind("<Next>", lambda e: self._nav(JUMP_LARGE))    # PageDown
        self.bind("<Home>", lambda e: self._goto(0))
        self.bind("<End>", lambda e: self._goto(len(self.filtered_indices) - 1))

        # Only F / Delete marks fucked. Rest is just browsing.
        self.bind("<f>", lambda e: self._decide("fucked"))
        self.bind("<F>", lambda e: self._decide("fucked"))
        self.bind("<Delete>", lambda e: self._decide("fucked"))
        # Optional explicit accept (not part of main workflow)
        self.bind("<a>", lambda e: self._decide("accepted"))
        self.bind("<A>", lambda e: self._decide("accepted"))
        self.bind("<space>", lambda e: self._nav(1))
        self.bind("<s>", lambda e: self._nav(1))
        self.bind("<S>", lambda e: self._nav(1))
        self.bind("<z>", lambda e: self._undo(1))
        self.bind("<Control-z>", lambda e: self._undo(5))
        self.bind("<c>", lambda e: self._copy_filename())
        self.bind("<C>", lambda e: self._copy_filename())
        self.bind("<r>", lambda e: self._reload_current())
        self.bind("<R>", lambda e: self._reload_current())
        self.bind("<h>", lambda e: self._toggle_help())
        self.bind("<H>", lambda e: self._toggle_help())
        self.bind("<e>", lambda e: self._open_external())
        self.bind("<E>", lambda e: self._open_external())
        self.bind("<Control-e>", lambda e: self._reveal())
        self.bind("<m>", lambda e: self._toggle_mode())
        self.bind("<M>", lambda e: self._toggle_mode())
        self.bind("<d>", lambda e: self._toggle_diff())
        self.bind("<D>", lambda e: self._toggle_diff())
        self.bind("<Escape>", lambda e: self._hide_help())

        # Keep focus
        self.bind("<Button-1>", lambda e: self.focus_set())

    # ------------------------------------------------------------------ Startup & folders
    def _startup(self):
        if self.session_path.exists():
            try:
                self._load_session()
                if (self.session.original_folder and self.session.upscaled_folder
                        and Path(self.session.original_folder).is_dir()
                        and Path(self.session.upscaled_folder).is_dir()):
                    self.original_folder = Path(self.session.original_folder)
                    self.upscaled_folder = Path(self.session.upscaled_folder)
                    self.fucked_up_folder = Path(self.session.fucked_up_folder) if self.session.fucked_up_folder else None
                    if self.fucked_up_folder is None or not self.fucked_up_folder.is_dir():
                        self._ask_fucked_folder()
                    self.prefer_move = self.session.prefer_move
                    self.chk_move.select() if self.prefer_move else self.chk_move.deselect()
                    self.auto_threshold = self.session.auto_threshold
                    self.linked_zoom = self.session.linked_zoom
                    if self.linked_zoom:
                        self.chk_linked.select()
                    self.show_diff = self.session.show_diff
                    if self.show_diff:
                        self.chk_diff.select()
                    if self.session.window_geometry:
                        try:
                            self.geometry(self.session.window_geometry)
                        except Exception:
                            pass
                    self._status("Session restored – scanning folders…")
                    self.after(50, self._scan_folders)
                    return
            except Exception as e:
                print(f"Session load error: {e}")
        self._choose_folders()

    def _choose_folders(self):
        messagebox.showinfo(APP_NAME,
                            "Select the three folders:\n\n"
                            "1. ORIGINAL textures (read-only)\n"
                            "2. UPSCALED textures\n"
                            "3. fucked_up destination")
        orig = filedialog.askdirectory(title="Select ORIGINAL folder")
        if not orig:
            return
        up = filedialog.askdirectory(title="Select UPSCALED folder")
        if not up:
            return
        fucked = filedialog.askdirectory(title="Select fucked_up destination folder")
        if not fucked:
            return
        self.original_folder = Path(orig)
        self.upscaled_folder = Path(up)
        self.fucked_up_folder = Path(fucked)
        self.session.original_folder = str(self.original_folder)
        self.session.upscaled_folder = str(self.upscaled_folder)
        self.session.fucked_up_folder = str(self.fucked_up_folder)
        self._scan_folders()

    def _ask_fucked_folder(self):
        fucked = filedialog.askdirectory(title="Select fucked_up destination folder")
        if fucked:
            self.fucked_up_folder = Path(fucked)
            self.session.fucked_up_folder = str(self.fucked_up_folder)

    def _scan_folders(self):
        if not self.original_folder or not self.upscaled_folder:
            return
        self._status("Scanning folders (this may take a moment)…")
        self.update_idletasks()

        orig_map: Dict[str, Path] = {}
        up_map: Dict[str, Path] = {}

        def collect(folder: Path, target: Dict[str, Path]):
            try:
                for p in folder.rglob("*"):
                    if p.is_file() and is_image(p):
                        key = safe_stem(p)
                        # Prefer shorter path / first found if collision
                        if key not in target:
                            target[key] = p
            except Exception as e:
                print(f"Scan error in {folder}: {e}")

        t1 = threading.Thread(target=collect, args=(self.original_folder, orig_map), daemon=True)
        t2 = threading.Thread(target=collect, args=(self.upscaled_folder, up_map), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        all_names = sorted(set(orig_map.keys()) | set(up_map.keys()))
        self.pairs = []
        for name in all_names:
            pair = TexturePair(name=name)
            pair.original_path = orig_map.get(name)
            pair.upscaled_path = up_map.get(name)
            if pair.upscaled_path and pair.upscaled_path.exists():
                try:
                    pair.file_size_up = pair.upscaled_path.stat().st_size
                    pair.mtime_up = pair.upscaled_path.stat().st_mtime
                except Exception:
                    pass
            # Restore previous decisions
            if name in self.session.accepted:
                pair.status = "accepted"
            elif name in self.session.fucked:
                pair.status = "fucked"
            elif name in self.session.skipped:
                pair.status = "skipped"
            self.pairs.append(pair)

        self._status(f"Found {len(self.pairs)} unique textures "
                     f"({sum(1 for p in self.pairs if p.original_path)} orig, "
                     f"{sum(1 for p in self.pairs if p.upscaled_path)} upscaled)")
        self._apply_filters(restore_index=True)
        self._schedule_auto_save()
        self._update_fucked_count()

    # ------------------------------------------------------------------ Filters & navigation
    def _apply_filters(self, restore_index: bool = False):
        query = self.search_var.get().strip().lower()
        mode = self.filter_var.get()
        sort_by = self.sort_var.get()

        indices = list(range(len(self.pairs)))

        if mode == "both":
            indices = [i for i in indices if self.pairs[i].original_path and self.pairs[i].upscaled_path]
        elif mode == "missing":
            indices = [i for i in indices if not (self.pairs[i].original_path and self.pairs[i].upscaled_path)]
        elif mode == "fucked":
            indices = [i for i in indices if self.pairs[i].status == "fucked"]
        elif mode == "accepted":
            indices = [i for i in indices if self.pairs[i].status == "accepted"]
        elif mode == "pending":
            indices = [i for i in indices if self.pairs[i].status == "pending"]
        # "all" → keep everything

        if query:
            indices = [i for i in indices if query in self.pairs[i].name]

        def sort_key(i: int):
            p = self.pairs[i]
            if sort_by == "resolution":
                return -(p.up_w * p.up_h or p.orig_w * p.orig_h)
            if sort_by == "size":
                return -p.file_size_up
            if sort_by == "mtime":
                return -p.mtime_up
            return p.name

        indices.sort(key=sort_key)
        self.filtered_indices = indices

        if not self.filtered_indices:
            self.current_pos = 0
            self._clear_canvases()
            self._update_labels()
            return

        if restore_index and self.session.last_index < len(self.pairs):
            # Try to keep the same pair visible
            try:
                target_name = self.pairs[self.session.last_index].name
                for pos, idx in enumerate(self.filtered_indices):
                    if self.pairs[idx].name == target_name:
                        self.current_pos = pos
                        break
                else:
                    self.current_pos = 0
            except Exception:
                self.current_pos = 0
        else:
            self.current_pos = max(0, min(self.current_pos, len(self.filtered_indices) - 1))

        self._load_current()

    def _nav(self, delta: int):
        if not self.filtered_indices:
            return
        self.current_pos = max(0, min(len(self.filtered_indices) - 1, self.current_pos + delta))
        # Periodic light cleanup to avoid Tkinter/PhotoImage memory creep after long sessions
        if self.current_pos % 40 == 0:
            try:
                import gc
                gc.collect()
            except Exception:
                pass
        self._load_current()

    def _goto(self, pos: int):
        if not self.filtered_indices:
            return
        self.current_pos = max(0, min(len(self.filtered_indices) - 1, pos))
        self._load_current()

    def _current_pair(self) -> Optional[TexturePair]:
        if not self.filtered_indices or self.current_pos >= len(self.filtered_indices):
            return None
        return self.pairs[self.filtered_indices[self.current_pos]]

    # ------------------------------------------------------------------ Image loading & display
    def _load_current(self):
        pair = self._current_pair()
        if pair is None:
            self._clear_canvases()
            self._update_labels()
            return

        self.session.last_index = self.filtered_indices[self.current_pos]
        self._update_labels()
        self.zoom_orig = DEFAULT_ZOOM
        self.zoom_up = DEFAULT_ZOOM
        self.pan_orig = [0, 0]
        self.pan_up = [0, 0]
        self._current_orig_img = None
        self._current_up_img = None
        self._clear_canvases()

        # Request original
        if pair.original_path and pair.original_path.exists():
            self.loader.request(pair.original_path,
                                lambda img, key: self.after(0, lambda: self._on_img_loaded(img, "orig", pair)),
                                max_side=2048)
        else:
            self._draw_placeholder("orig", "MISSING")

        # Request upscaled
        if pair.upscaled_path and pair.upscaled_path.exists():
            self.loader.request(pair.upscaled_path,
                                lambda img, key: self.after(0, lambda: self._on_img_loaded(img, "up", pair)),
                                max_side=2048)
        else:
            self._draw_placeholder("up", "MISSING")

        # Prefetch neighbours
        self._prefetch_neighbours()

    def _on_img_loaded(self, img: Optional[Image.Image], which: str, pair: TexturePair):
        if img is None:
            self._draw_placeholder(which, "CORRUPT")
            return
        if which == "orig":
            self._current_orig_img = img
            pair.orig_w, pair.orig_h = img.size
            self._redraw_panel("orig")
        else:
            self._current_up_img = img
            pair.up_w, pair.up_h = img.size
            self._redraw_panel("up")
            if self.mode == "auto":
                self._show_auto_score(pair)
        self._update_labels()

    def _prefetch_neighbours(self):
        for offset in (-2, -1, 1, 2, 3):
            pos = self.current_pos + offset
            if 0 <= pos < len(self.filtered_indices):
                p = self.pairs[self.filtered_indices[pos]]
                for path in (p.original_path, p.upscaled_path):
                    if path and path.exists():
                        self.loader.request(path, lambda *a: None, max_side=1024)

    def _redraw_panel(self, which: str):
        canvas = self.canvas_orig if which == "orig" else self.canvas_up
        img = self._current_orig_img if which == "orig" else self._current_up_img
        zoom = self.zoom_orig if which == "orig" else self.zoom_up
        pan = self.pan_orig if which == "orig" else self.pan_up

        canvas.delete("all")
        if img is None:
            return

        # Optional difference view on the upscaled side
        display_img = img
        if which == "up" and self.show_diff and self._current_orig_img is not None:
            display_img = make_difference_heatmap(self._current_orig_img, img)

        # Apply temporary view adjustments
        if abs(self.brightness - 1.0) > 0.01 or abs(self.contrast - 1.0) > 0.01 or abs(self.saturation - 1.0) > 0.01:
            display_img = display_img.copy()
            if abs(self.brightness - 1.0) > 0.01:
                display_img = ImageEnhance.Brightness(display_img).enhance(self.brightness)
            if abs(self.contrast - 1.0) > 0.01:
                display_img = ImageEnhance.Contrast(display_img).enhance(self.contrast)
            if abs(self.saturation - 1.0) > 0.01:
                display_img = ImageEnhance.Color(display_img).enhance(self.saturation)

        cw = max(canvas.winfo_width(), 100)
        ch = max(canvas.winfo_height(), 100)
        iw, ih = display_img.size

        # Fit to panel (allow upscaling small originals so they fill the view)
        # Previously capped at 1.0 which left 128/256px textures tiny in the middle.
        fit = min(cw / iw, ch / ih) if iw and ih else 1.0
        scale = fit * zoom
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))

        try:
            # BILINEAR is much faster for interactive zoom; LANCZOS only when close to 1:1
            resample = Image.Resampling.LANCZOS if 0.7 < scale < 1.4 else Image.Resampling.BILINEAR
            resized = display_img.resize((nw, nh), resample)
            photo = ImageTk.PhotoImage(resized)
        except Exception:
            self._draw_placeholder(which, "RENDER ERR")
            return

        # Keep strong reference so Tkinter doesn't GC the PhotoImage
        if which == "orig":
            self._photo_orig = photo
        else:
            self._photo_up = photo

        x = cw // 2 + pan[0]
        y = ch // 2 + pan[1]
        canvas.create_image(x, y, image=photo, anchor="center")

    def _clear_canvases(self):
        self.canvas_orig.delete("all")
        self.canvas_up.delete("all")
        self._photo_orig = None
        self._photo_up = None

    def _draw_placeholder(self, which: str, text: str):
        canvas = self.canvas_orig if which == "orig" else self.canvas_up
        canvas.delete("all")
        cw = max(canvas.winfo_width(), 100)
        ch = max(canvas.winfo_height(), 100)
        color = RED if text in ("MISSING", "CORRUPT", "RENDER ERR") else MUTED
        canvas.create_text(cw // 2, ch // 2, text=text, fill=color, font=("Segoe UI", 18, "bold"))

    # ------------------------------------------------------------------ Zoom & Pan
    def _on_zoom(self, event, which: str):
        # Windows / Mac use delta, Linux use num
        if hasattr(event, "delta") and event.delta:
            direction = 1 if event.delta > 0 else -1
        elif hasattr(event, "num"):
            direction = 1 if event.num == 4 else -1
        else:
            return

        factor = ZOOM_STEP if direction > 0 else 1.0 / ZOOM_STEP
        if which == "orig" or self.linked_zoom:
            self.zoom_orig = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom_orig * factor))
            self._redraw_panel("orig")
        if which == "up" or self.linked_zoom:
            self.zoom_up = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom_up * factor))
            self._redraw_panel("up")

    def _on_drag_start(self, event, which: str):
        self._drag_start = (event.x, event.y)
        self._drag_panel = which
        self._drag_pan0 = list(self.pan_orig if which == "orig" else self.pan_up)

    def _on_drag_move(self, event, which: str):
        if self._drag_start is None or self._drag_panel != which:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        if which == "orig" or self.linked_zoom:
            self.pan_orig = [self._drag_pan0[0] + dx, self._drag_pan0[1] + dy]
            self._redraw_panel("orig")
        if which == "up" or self.linked_zoom:
            self.pan_up = [self._drag_pan0[0] + dx, self._drag_pan0[1] + dy]
            self._redraw_panel("up")

    def _on_drag_end(self, event):
        self._drag_start = None
        self._drag_panel = None

    def _toggle_linked(self):
        self.linked_zoom = bool(self.chk_linked.get())
        self.session.linked_zoom = self.linked_zoom

    def _toggle_diff(self):
        self.show_diff = bool(self.chk_diff.get())
        self.session.show_diff = self.show_diff
        self._redraw_panel("up")

    def _toggle_move(self):
        self.prefer_move = bool(self.chk_move.get())
        self.session.prefer_move = self.prefer_move
        self._status(f"Bad files will be {'MOVED' if self.prefer_move else 'COPIED'} to fucked_up")

    # ------------------------------------------------------------------ Decisions
    def _decide(self, action: str):
        pair = self._current_pair()
        if pair is None:
            return

        # Already decided as fucked → just go next (prevents _1 _2 duplicates)
        if action == "fucked" and pair.status == "fucked":
            self._status("Already marked fucked – skipping")
            self.after(80, lambda: self._nav(1))
            return

        prev = pair.status
        pair.status = action

        fucked_path = None
        was_move = False
        original_upscaled_path = None  # keep for undo when we move

        if action == "fucked":
            if not pair.upscaled_path or not pair.upscaled_path.exists():
                self._status("No upscaled file to mark as fucked")
                pair.status = prev
                return
            if not self.fucked_up_folder:
                self._ask_fucked_folder()
                if not self.fucked_up_folder:
                    pair.status = prev
                    return

            # Re-read checkbox every time (more reliable than cached flag)
            do_move = bool(self.chk_move.get())
            self.prefer_move = do_move
            self.session.prefer_move = do_move

            try:
                src = pair.upscaled_path
                original_upscaled_path = str(src)
                dest = self.fucked_up_folder / src.name

                # If a file with same name already sits in fucked_up, only add suffix
                # when we are COPYing. When MOVING we prefer exact name (user wants it gone).
                if dest.exists() and not do_move:
                    stem = dest.stem
                    suf = dest.suffix
                    n = 1
                    while dest.exists():
                        dest = self.fucked_up_folder / f"{stem}_{n}{suf}"
                        n += 1

                if do_move:
                    # Real move – file leaves the upscaled folder
                    if dest.exists():
                        # Rare collision: remove existing dest so move can succeed cleanly
                        try:
                            dest.unlink()
                        except Exception:
                            pass
                    shutil.move(str(src), str(dest))
                    was_move = True
                    # Critical: clear the path so future F presses see "no file"
                    pair.upscaled_path = None
                else:
                    shutil.copy2(str(src), str(dest))

                fucked_path = str(dest)
                self._status(f"{'MOVED' if was_move else 'Copied'} → {dest.name}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to {'move' if do_move else 'copy'}:\n{e}")
                pair.status = prev
                return

            if pair.name not in self.session.fucked:
                self.session.fucked.append(pair.name)
            if pair.name in self.session.accepted:
                self.session.accepted.remove(pair.name)
            if pair.name in self.session.skipped:
                self.session.skipped.remove(pair.name)
        elif action == "accepted":
            if pair.name not in self.session.accepted:
                self.session.accepted.append(pair.name)
            if pair.name in self.session.fucked:
                self.session.fucked.remove(pair.name)
            if pair.name in self.session.skipped:
                self.session.skipped.remove(pair.name)
            self._status("Accepted")
        else:  # skipped
            if pair.name not in self.session.skipped:
                self.session.skipped.append(pair.name)
            self._status("Skipped")

        # Push undo (store original location so we can restore a moved file)
        self.undo_stack.append(UndoEntry(
            action=action,
            pair_name=pair.name,
            previous_status=prev,
            fucked_path=fucked_path,
            was_move=was_move,
            # We piggy-back the original path inside fucked_path comment style if needed;
            # for simplicity we keep the current UndoEntry and improve restore below.
        ))
        # Attach original path for move-undo (extend the entry)
        if was_move and original_upscaled_path:
            self.undo_stack[-1].fucked_path = fucked_path
            # Store original as a side attribute dynamically
            setattr(self.undo_stack[-1], "original_upscaled_path", original_upscaled_path)

        self._flash_border(action)
        self._update_fucked_count()
        # Advance after short visual feedback
        self.after(HIGHLIGHT_MS, lambda: self._nav(1))

    def _flash_border(self, action: str):
        color = GREEN if action == "accepted" else (RED if action == "fucked" else YELLOW)
        for canvas in (self.canvas_orig, self.canvas_up):
            canvas.configure(highlightthickness=4, highlightbackground=color)
        if self._highlight_after:
            self.after_cancel(self._highlight_after)
        self._highlight_after = self.after(HIGHLIGHT_MS, self._clear_flash)

    def _clear_flash(self):
        for canvas in (self.canvas_orig, self.canvas_up):
            canvas.configure(highlightthickness=0)
        self._highlight_after = None

    def _undo(self, steps: int = 1):
        restored = 0
        for _ in range(steps):
            if not self.undo_stack:
                break
            entry = self.undo_stack.pop()
            # Find pair
            pair = next((p for p in self.pairs if p.name == entry.pair_name), None)
            if pair is None:
                continue
            pair.status = entry.previous_status

            # Restore file if it was moved/copied to fucked_up
            if entry.fucked_path and Path(entry.fucked_path).exists():
                try:
                    if entry.was_move:
                        # Prefer the exact original path we saved at decide time
                        orig_path = getattr(entry, "original_upscaled_path", None)
                        if orig_path:
                            dest = Path(orig_path)
                        elif self.upscaled_folder:
                            dest = self.upscaled_folder / Path(entry.fucked_path).name
                        else:
                            dest = None

                        if dest is not None:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            if dest.exists():
                                # safety: don't overwrite something that reappeared
                                Path(entry.fucked_path).unlink(missing_ok=True)
                            else:
                                shutil.move(entry.fucked_path, str(dest))
                                pair.upscaled_path = dest
                        else:
                            Path(entry.fucked_path).unlink(missing_ok=True)
                    else:
                        # It was a copy – simply delete the copy in fucked_up
                        Path(entry.fucked_path).unlink(missing_ok=True)
                    restored += 1
                except Exception as e:
                    print(f"Undo file restore failed: {e}")

            # Fix session lists
            for lst in (self.session.accepted, self.session.fucked, self.session.skipped):
                if entry.pair_name in lst:
                    lst.remove(entry.pair_name)
            if entry.previous_status == "accepted":
                self.session.accepted.append(entry.pair_name)
            elif entry.previous_status == "fucked":
                self.session.fucked.append(entry.pair_name)
            elif entry.previous_status == "skipped":
                self.session.skipped.append(entry.pair_name)

        self._update_fucked_count()
        self._status(f"Undid {restored} action(s)" if restored else "Nothing to undo")
        self._load_current()

    # ------------------------------------------------------------------ Auto mode
    def _toggle_mode(self):
        self.mode = "auto" if self.mode == "manual" else "manual"
        self.lbl_mode.configure(text=self.mode.upper(),
                                text_color=YELLOW if self.mode == "auto" else GREEN)
        self._status(f"Switched to {self.mode.upper()} mode")
        if self.mode == "auto":
            pair = self._current_pair()
            if pair:
                self._show_auto_score(pair)

    def _show_auto_score(self, pair: TexturePair):
        score, reason = compute_auto_score(self._current_orig_img, self._current_up_img, pair)
        colour = GREEN if score >= self.auto_threshold else RED
        self.lbl_auto.configure(text=f"Auto confidence: {score:.0f}%  ({reason})", text_color=colour)
        # Place near top of right panel
        self.lbl_auto.place(relx=0.55, rely=0.07, anchor="nw")
        if score < self.auto_threshold and self.mode == "auto":
            # Soft suggestion – user still decides
            self._status(f"Auto suggests FUCKED (score {score:.0f}% < {self.auto_threshold})")

    def _run_full_auto_scan(self):
        if not self.pairs:
            return
        if not self.fucked_up_folder:
            self._ask_fucked_folder()
            if not self.fucked_up_folder:
                return

        do_move = bool(self.chk_move.get())
        action_word = "MOVE" if do_move else "COPY"

        if not messagebox.askyesno(
                "Full Auto Scan",
                f"Scan all {len(self.pairs)} pairs and automatically {action_word} files with "
                f"confidence < {self.auto_threshold}% to fucked_up?\n\n"
                f"{'Files will be REMOVED from the upscaled folder.' if do_move else 'Files stay in upscaled (safe copy).'}\n"
                "A report will be written to auto_scan_report.txt\n\n"
                "You can Pause / Stop at any time."):
            return

        # Control flags
        self._auto_stop = threading.Event()
        self._auto_pause = threading.Event()
        self._auto_pause.set()  # not paused initially

        # UI: show Pause / Stop
        self.btn_auto_scan.configure(state="disabled", text="Loading model…")
        self.update_idletasks()
        # Warm-up AlexNet so first images aren't slow
        try:
            _get_perceptual_net()
        except Exception as e:
            print(f"Model warm-up: {e}")
        self.btn_auto_scan.configure(text="Scanning…")

        if not hasattr(self, "btn_auto_pause"):
            self.btn_auto_pause = ctk.CTkButton(
                self.top_bar, text="Pause", width=70, fg_color="#f39c12",
                hover_color="#e67e22", command=self._toggle_auto_pause)
            self.btn_auto_stop = ctk.CTkButton(
                self.top_bar, text="Stop", width=70, fg_color="#c0392b",
                hover_color="#e74c3c", command=self._stop_auto_scan)
        self.btn_auto_pause.grid(row=0, column=9, padx=4, pady=6)
        self.btn_auto_stop.grid(row=0, column=10, padx=(4, 10), pady=6)
        self.btn_auto_pause.configure(text="Pause")
        self._status("Full auto scan starting…")

        def worker():
            report_lines = []
            acted = 0
            errors = 0
            total = len(self.pairs)
            threshold = self.auto_threshold
            stopped_early = False

            def safe_thumb(path: Path, max_side: int = 384) -> Optional[Image.Image]:
                try:
                    with Image.open(path) as im:
                        im = im.convert("RGBA")
                        im.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
                        return im.copy()
                except Exception:
                    return None

            for i, pair in enumerate(self.pairs):
                if self._auto_stop.is_set():
                    stopped_early = True
                    break
                # Support pause
                while not self._auto_pause.is_set() and not self._auto_stop.is_set():
                    time.sleep(0.15)
                if self._auto_stop.is_set():
                    stopped_early = True
                    break

                if pair.status == "fucked":
                    continue
                if not pair.original_path or not pair.upscaled_path:
                    continue
                if not pair.upscaled_path.exists():
                    continue

                try:
                    o = safe_thumb(pair.original_path)
                    u = safe_thumb(pair.upscaled_path)
                    if o is None or u is None:
                        errors += 1
                        report_lines.append(f"ERROR   {pair.name}: cannot open image")
                        continue

                    try:
                        with Image.open(pair.upscaled_path) as full_u:
                            pair.up_w, pair.up_h = full_u.size
                    except Exception:
                        pair.up_w, pair.up_h = u.size
                    try:
                        with Image.open(pair.original_path) as full_o:
                            pair.orig_w, pair.orig_h = full_o.size
                    except Exception:
                        pair.orig_w, pair.orig_h = o.size

                    score, reason = compute_auto_score(o, u, pair)
                    del o, u

                    if score < threshold:
                        dest = self.fucked_up_folder / pair.upscaled_path.name
                        if dest.exists() and not do_move:
                            report_lines.append(f"SKIPPED (exists) {pair.name}")
                        else:
                            try:
                                if do_move:
                                    if dest.exists():
                                        dest.unlink(missing_ok=True)
                                    shutil.move(str(pair.upscaled_path), str(dest))
                                    pair.upscaled_path = None
                                else:
                                    shutil.copy2(str(pair.upscaled_path), str(dest))
                                pair.status = "fucked"
                                if pair.name not in self.session.fucked:
                                    self.session.fucked.append(pair.name)
                                acted += 1
                                report_lines.append(f"FUCKED  {score:5.1f}%  {pair.name}  ({reason})")
                            except Exception as e:
                                errors += 1
                                report_lines.append(f"ERROR   {pair.name}: {e}")
                    else:
                        report_lines.append(f"OK      {score:5.1f}%  {pair.name}")
                except Exception as e:
                    errors += 1
                    report_lines.append(f"ERROR   {pair.name}: {e}")

                if i % 20 == 0 or i == total - 1:
                    msg = f"Auto scan {i+1}/{total}  |  {action_word.lower()}ed {acted}  |  errors {errors}"
                    self.after(0, lambda m=msg: self._status(m))

            report_path = Path(__file__).resolve().parent / "auto_scan_report.txt"
            try:
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(f"Auto scan {datetime.now().isoformat()}\n")
                    f.write(f"Threshold: {threshold}%\n")
                    f.write(f"Mode: {action_word}\n")
                    f.write(f"Acted on: {acted}\nErrors: {errors}\n")
                    f.write(f"Stopped early: {stopped_early}\n\n")
                    f.write("\n".join(report_lines))
            except Exception as e:
                print(f"Report write failed: {e}")

            def finish():
                self.btn_auto_scan.configure(state="normal", text="Full Auto Scan")
                try:
                    self.btn_auto_pause.grid_remove()
                    self.btn_auto_stop.grid_remove()
                except Exception:
                    pass
                self._update_fucked_count()
                self._save_session()
                status = "stopped early" if stopped_early else "done"
                self._status(f"Auto scan {status} – {acted} files {action_word.lower()}ed. Report: {report_path.name}")
                messagebox.showinfo(
                    "Auto Scan Complete",
                    f"{action_word}ed {acted} suspicious files to fucked_up.\n"
                    f"Errors: {errors}\n"
                    f"{'(Stopped early by user)' if stopped_early else ''}\n\n"
                    f"Report: {report_path}"
                )
                self._load_current()

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_auto_pause(self):
        if not hasattr(self, "_auto_pause"):
            return
        if self._auto_pause.is_set():
            self._auto_pause.clear()
            self.btn_auto_pause.configure(text="Resume")
            self._status("Auto scan PAUSED")
        else:
            self._auto_pause.set()
            self.btn_auto_pause.configure(text="Pause")
            self._status("Auto scan resumed")

    def _stop_auto_scan(self):
        if hasattr(self, "_auto_stop"):
            self._auto_stop.set()
            if hasattr(self, "_auto_pause"):
                self._auto_pause.set()  # unstick if paused
            self._status("Stopping auto scan…")

    # ------------------------------------------------------------------ Session persistence
    def _schedule_auto_save(self):
        if self._auto_save_job:
            self.after_cancel(self._auto_save_job)
        self._auto_save_job = self.after(AUTO_SAVE_INTERVAL * 1000, self._auto_save)

    def _auto_save(self):
        self._save_session()
        self._schedule_auto_save()

    def _save_session(self):
        try:
            self.session.last_index = (self.filtered_indices[self.current_pos]
                                       if self.filtered_indices else 0)
            self.session.window_geometry = self.geometry()
            self.session.prefer_move = self.prefer_move
            self.session.auto_threshold = self.auto_threshold
            self.session.linked_zoom = self.linked_zoom
            self.session.show_diff = self.show_diff
            self.session.filter_mode = self.filter_var.get()
            self.session.search_query = self.search_var.get()
            self.session.sort_by = self.sort_var.get()

            data = asdict(self.session)
            with open(self.session_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Session save failed: {e}")

    def _load_session(self):
        with open(self.session_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.session = SessionData(**{k: v for k, v in data.items() if k in SessionData.__dataclass_fields__})
        self.search_var.set(self.session.search_query)
        self.filter_var.set(self.session.filter_mode)
        self.sort_var.set(self.session.sort_by)

    # ------------------------------------------------------------------ Misc helpers
    def _update_labels(self):
        pair = self._current_pair()
        total = len(self.filtered_indices)
        pos = self.current_pos + 1 if total else 0
        self.lbl_progress.configure(text=f"{pos} / {total}")

        if pair is None:
            self.lbl_filename.configure(text="—")
            self.lbl_res.configure(text="")
            return

        status_icon = {"accepted": "✓", "fucked": "✗", "skipped": "…", "pending": ""}.get(pair.status, "")
        self.lbl_filename.configure(text=f"{status_icon}  {pair.name}")

        res_o = f"{pair.orig_w}×{pair.orig_h}" if pair.orig_w else "?"
        res_u = f"{pair.up_w}×{pair.up_h}" if pair.up_w else "?"
        self.lbl_res.configure(text=f"Orig {res_o}   →   Up {res_u}")

    def _update_fucked_count(self):
        n = len(self.session.fucked)
        self.lbl_fucked_count.configure(text=f"fucked: {n}")

    def _status(self, msg: str):
        self._last_action_msg = msg
        self.lbl_status.configure(text=msg)

    def _copy_filename(self):
        pair = self._current_pair()
        if pair:
            self.clipboard_clear()
            self.clipboard_append(pair.name)
            self._status(f"Copied: {pair.name}")

    def _reload_current(self):
        self.img_cache.clear()
        self._load_current()
        self._status("Reloaded")

    def _open_external(self):
        pair = self._current_pair()
        if pair and pair.upscaled_path and pair.upscaled_path.exists():
            open_external_editor(pair.upscaled_path)
            self._status("Opened external editor")
        else:
            self._status("No upscaled file")

    def _reveal(self):
        pair = self._current_pair()
        if pair and pair.upscaled_path and pair.upscaled_path.exists():
            open_in_explorer(pair.upscaled_path)
            self._status("Revealed in file manager")
        else:
            self._status("No upscaled file")

    def _export_fucked_list(self):
        if not self.session.fucked:
            messagebox.showinfo("Export", "No files marked as fucked yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text", "*.txt")],
            initialfile="fucked_up_list.txt"
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Fucked-up textures – {datetime.now().isoformat()}\n")
            f.write(f"# Total: {len(self.session.fucked)}\n\n")
            for name in sorted(self.session.fucked):
                f.write(name + "\n")
        self._status(f"Exported {len(self.session.fucked)} names → {Path(path).name}")

    def _toggle_help(self):
        if self._help_visible:
            self._hide_help()
        else:
            self.help_frame.place(relx=0.5, rely=0.5, anchor="center")
            self._help_visible = True

    def _hide_help(self):
        self.help_frame.place_forget()
        self._help_visible = False

    def _on_close(self):
        self._save_session()
        self.loader.shutdown()
        self.destroy()


# ---------------------------------------------------------------------------
# Headless / CLI auto mode
# ---------------------------------------------------------------------------
def run_headless_auto(original: Path, upscaled: Path, fucked_up: Path,
                      threshold: float = 55.0, do_move: bool = True,
                      session_path: Optional[Path] = None):
    """
    Non-GUI automatic scan.
    Example:
      python qa_texture_tool.py -auto --original "D:/orig" --upscaled "D:/up" --fucked "D:/fucked"
    """
    print("=" * 60)
    print(f"  Texture QA – Headless Auto Scan  (threshold={threshold}%)")
    print(f"  Mode: {'MOVE' if do_move else 'COPY'}")
    print("=" * 60)
    print(f"Original : {original}")
    print(f"Upscaled : {upscaled}")
    print(f"Fucked_up: {fucked_up}")
    print()

    # Warm-up perceptual model (AlexNet ~233MB, one-time download)
    print("Loading perceptual model (AlexNet)…")
    _get_perceptual_net()
    print()

    if not original.is_dir() or not upscaled.is_dir():
        print("ERROR: original or upscaled folder does not exist.")
        sys.exit(1)
    fucked_up.mkdir(parents=True, exist_ok=True)

    # Collect files
    def collect(folder: Path) -> Dict[str, Path]:
        m: Dict[str, Path] = {}
        for p in folder.rglob("*"):
            if p.is_file() and is_image(p):
                key = safe_stem(p)
                if key not in m:
                    m[key] = p
        return m

    print("Scanning folders…")
    orig_map = collect(original)
    up_map = collect(upscaled)
    names = sorted(set(orig_map.keys()) & set(up_map.keys()))
    print(f"Found {len(names)} matching pairs.\n")

    # Load previous fucked list from session if present (so we don't re-process)
    already_fucked = set()
    if session_path and session_path.exists():
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            already_fucked = set(data.get("fucked", []))
            print(f"Loaded {len(already_fucked)} previously marked files from session.")
        except Exception:
            pass

    acted = 0
    errors = 0
    report_lines = []
    new_fucked = []

    def safe_thumb(path: Path, max_side: int = 384) -> Optional[Image.Image]:
        try:
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
                return im.copy()
        except Exception:
            return None

    total = len(names)
    t0 = time.time()

    for i, name in enumerate(names):
        if name in already_fucked:
            continue
        op = orig_map[name]
        up = up_map[name]
        try:
            o = safe_thumb(op)
            u = safe_thumb(up)
            if o is None or u is None:
                errors += 1
                report_lines.append(f"ERROR   {name}: cannot open")
                continue

            pair = TexturePair(name=name, original_path=op, upscaled_path=up)
            try:
                with Image.open(up) as full:
                    pair.up_w, pair.up_h = full.size
                with Image.open(op) as full:
                    pair.orig_w, pair.orig_h = full.size
            except Exception:
                pair.up_w, pair.up_h = u.size
                pair.orig_w, pair.orig_h = o.size

            score, reason = compute_auto_score(o, u, pair)
            del o, u

            if score < threshold:
                dest = fucked_up / up.name
                try:
                    if do_move:
                        if dest.exists():
                            dest.unlink(missing_ok=True)
                        shutil.move(str(up), str(dest))
                    else:
                        if not dest.exists():
                            shutil.copy2(str(up), str(dest))
                    acted += 1
                    new_fucked.append(name)
                    report_lines.append(f"FUCKED  {score:5.1f}%  {name}  ({reason})")
                except Exception as e:
                    errors += 1
                    report_lines.append(f"ERROR   {name}: {e}")
            else:
                report_lines.append(f"OK      {score:5.1f}%  {name}")
        except Exception as e:
            errors += 1
            report_lines.append(f"ERROR   {name}: {e}")

        if (i + 1) % 100 == 0 or i == total - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / max(elapsed, 0.01)
            eta = (total - i - 1) / max(rate, 0.01)
            print(f"\r  [{i+1:>5}/{total}]  acted {acted}  errors {errors}  "
                  f"{rate:.0f} files/s  ETA {eta:.0f}s   ", end="", flush=True)

    print()  # newline after progress

    # Update session if possible
    if session_path:
        try:
            data = {}
            if session_path.exists():
                with open(session_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            fucked_list = list(set(data.get("fucked", [])) | set(new_fucked))
            data["fucked"] = fucked_list
            data["original_folder"] = str(original)
            data["upscaled_folder"] = str(upscaled)
            data["fucked_up_folder"] = str(fucked_up)
            with open(session_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"Session updated → {session_path}")
        except Exception as e:
            print(f"Could not update session: {e}")

    report_path = Path(__file__).resolve().parent / "auto_scan_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Headless auto scan {datetime.now().isoformat()}\n")
        f.write(f"Threshold: {threshold}%\nMode: {'MOVE' if do_move else 'COPY'}\n")
        f.write(f"Acted on: {acted}\nErrors: {errors}\n\n")
        f.write("\n".join(report_lines))

    print()
    print(f"Done in {time.time()-t0:.1f}s")
    print(f"  {'Moved' if do_move else 'Copied'}: {acted}")
    print(f"  Errors : {errors}")
    print(f"  Report : {report_path}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Texture QA Tool – review / auto-filter upscaled GTA textures"
    )
    parser.add_argument("-auto", "--auto", action="store_true",
                        help="Run headless automatic scan (no GUI)")
    parser.add_argument("--original", type=str, default="",
                        help="Path to original (low-res) folder")
    parser.add_argument("--upscaled", type=str, default="",
                        help="Path to upscaled folder")
    parser.add_argument("--fucked", type=str, default="",
                        help="Path to fucked_up destination folder")
    parser.add_argument("--threshold", type=float, default=50.0,
                        help="Confidence threshold below which a texture is marked fucked (default 50)")
    parser.add_argument("--copy", action="store_true",
                        help="Copy instead of move (safer). Default is MOVE in -auto mode.")
    parser.add_argument("--session", type=str, default="",
                        help="Optional path to qa_session.json")
    args = parser.parse_args()

    if args.auto:
        # Headless mode
        session_path = Path(args.session) if args.session else Path(__file__).resolve().parent / SESSION_FILENAME

        orig = Path(args.original) if args.original else None
        up = Path(args.upscaled) if args.upscaled else None
        fucked = Path(args.fucked) if args.fucked else None

        # Fallback to last session folders
        if (not orig or not up or not fucked) and session_path.exists():
            try:
                with open(session_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                orig = orig or Path(data.get("original_folder", ""))
                up = up or Path(data.get("upscaled_folder", ""))
                fucked = fucked or Path(data.get("fucked_up_folder", ""))
            except Exception:
                pass

        if not orig or not up or not fucked or not orig.is_dir() or not up.is_dir():
            print("ERROR: Need valid --original, --upscaled and --fucked folders.")
            print("Example:")
            print('  python qa_texture_tool.py -auto --original "D:/orig" --upscaled "D:/up" --fucked "D:/bad"')
            print("Or run the GUI once so folders are saved in qa_session.json, then just:")
            print("  python qa_texture_tool.py -auto")
            sys.exit(1)

        run_headless_auto(
            original=orig,
            upscaled=up,
            fucked_up=fucked,
            threshold=args.threshold,
            do_move=not args.copy,
            session_path=session_path,
        )
        return

    # ---------- Normal GUI mode ----------
    if platform.system() == "Windows":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    app = TextureQAApp()
    app.mainloop()


if __name__ == "__main__":
    main()
