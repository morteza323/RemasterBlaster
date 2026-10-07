
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GTA Batch AI Upscaler
Single-file Tkinter GUI

Recommended for GTX 10xx / Pascal:
    Python 3.10.x
    PyTorch 2.14.x + CUDA 12.6

For Pascal GPUs, do NOT use CUDA 13.x PyTorch builds.

Install:
    pip install torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu126
    pip install spandrel pillow numpy psutil nvidia-ml-py

Optional:
    pip install spandrel_extra_arches

Run:
    python gta_batch_upscaler.py

Put model files in:
    ./models/

Supported:
    .pth
    .pt
    .safetensors
    .ckpt

Spandrel supports many SR/restoration architectures including:
    ESRGAN
    Real-ESRGAN
    SwinIR
    HAT
    DAT
    SPAN
    etc.

IMPORTANT FOR GTA SA TEXTURES
-----------------------------
The default pipeline preserves:
    - aspect ratio
    - alpha channel
    - texture orientation
    - UV texture layout
    - original filename
    - original relative folder structure

Do NOT enable:
    Round output size
    unless your target texture specification requires it.

For exact 2x/4x texture reconstruction:
    use Native (model)
    and keep Round output size = 1.
"""

import os
import sys
import re
import json
import math
import time
import queue
import shutil
import fnmatch
import threading
import traceback

from collections import deque
from functools import partial
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageFilter, ImageTk

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# =============================================================================
# PIL COMPATIBILITY
# =============================================================================

try:
    RS = Image.Resampling
except AttributeError:
    RS = Image


# =============================================================================
# PATHS / CONSTANTS
# =============================================================================

APP_DIR = Path(__file__).resolve().parent
MODELS_DIR = APP_DIR / "models"
SETTINGS_FILE = APP_DIR / "upscaler_settings.json"

BUILTIN = "[Built-in] Lanczos 4x (no AI - for testing)"

EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tga",
    ".webp",
    ".tif",
    ".tiff",
}

MODEL_EXTS = {
    ".pth",
    ".pt",
    ".safetensors",
    ".ckpt",
}

FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".bmp": "BMP",
    ".tga": "TGA",
    ".webp": "WEBP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}

ALPHA_FILTERS = {
    "Lanczos": RS.LANCZOS,
    "Bicubic": RS.BICUBIC,
    "Bilinear": RS.BILINEAR,
    "Nearest": RS.NEAREST,
}

ALPHA_MODES = [
    "AI (model)",
    "Lanczos",
    "Bicubic",
    "Bilinear",
    "Nearest",
]

SORT_MODES = [
    "Name (natural)",
    "Name (A-Z)",
    "Size (small first)",
    "Size (large first)",
    "Modified (newest first)",
]

SCALE_CHOICES = [
    "Native (model)",
    "1x",
    "1.5x",
    "2x",
    "3x",
    "4x",
    "6x",
    "8x",
]


# =============================================================================
# DEFAULTS
# =============================================================================

DEFAULTS = dict(
    input_dir="",
    output_dir="",
    model_path=BUILTIN,

    scale="Native (model)",

    device="auto",
    precision="auto",

    tile=512,
    overlap=16,

    free_vram=True,

    alpha_mode="AI (model)",
    bleed=True,
    bleed_iters=8,

    alpha_binarize=False,
    alpha_thr=128,

    ai_strength=100,

    sharpen=0,
    sharpen_radius=1.0,
    sharpen_thr=0,

    seamless=False,
    seamless_pad=16,

    max_side=0,
    round_mult=1,

    skip_over=0,
    skip_under=0,

    copy_skipped=False,

    recursive=False,
    skip_existing=False,

    name_filter="",
    sort_mode=SORT_MODES[0],

    out_ext="Same as source",

    png_level=3,
    jpg_quality=95,

    prefetch=2,
    save_threads=1,

    show_preview=True,
    checker=True,

    open_when_done=False,
)


# =============================================================================
# HEAVY IMPORTS
# =============================================================================

torch = None
F = None

ModelLoader = None
ImageModelDescriptor = None

IMPORT_ERR = {}


def import_heavy():
    """
    Import PyTorch and Spandrel.

    Kept separate so the GUI can appear even if dependencies
    are missing or broken.
    """

    global torch
    global F
    global ModelLoader
    global ImageModelDescriptor

    # -------------------------------------------------------------------------
    # PyTorch
    # -------------------------------------------------------------------------

    try:
        import torch as _torch
        import torch.nn.functional as _F

        torch = _torch
        F = _F

    except Exception as e:
        IMPORT_ERR["torch"] = str(e)
        return

    # -------------------------------------------------------------------------
    # Spandrel
    # -------------------------------------------------------------------------

    try:

        from spandrel import (
            ModelLoader as _ModelLoader,
            ImageModelDescriptor as _ImageModelDescriptor,
        )

        ModelLoader = _ModelLoader
        ImageModelDescriptor = _ImageModelDescriptor

        try:
            import spandrel_extra_arches

            spandrel_extra_arches.install()

        except Exception:
            pass

    except Exception as e:

        IMPORT_ERR["spandrel"] = str(e)


# =============================================================================
# HELPERS
# =============================================================================

def parse_scale(text):
    """
    Convert:
        Native (model) -> None
        2x -> 2.0
        2.5 -> 2.5
    """

    t = str(text).strip().lower()

    if t.startswith("native") or t == "":
        return None

    t = (
        t.replace("x", "")
         .replace("×", "")
         .strip()
    )

    value = float(t)

    if not (0.1 <= value <= 16):
        raise ValueError("Scale must be between 0.1 and 16.")

    return value


def fmt_time(seconds):
    seconds = int(max(0, seconds))

    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)

    if h:
        return f"{h:d}:{m:02d}:{s:02d}"

    return f"{m:02d}:{s:02d}"


def natural_sort_key(path, root):
    """
    Stable natural filename sorting.
    """

    rel = str(path.relative_to(root)).lower()

    parts = re.split(r"(\d+)", rel)

    return [
        int(x) if x.isdigit() else x
        for x in parts
    ]


# =============================================================================
# ALPHA BLEED
# =============================================================================

def bleed_colors(rgb_u8, alpha_u8, iterations):
    """
    Push visible RGB colours outward into transparent pixels.

    This is particularly useful for:
        PNG game textures
        decals
        foliage
        character cutouts
        alpha-test materials

    It prevents dark/white fringes after AI upscaling.
    """

    if iterations <= 0:
        return rgb_u8

    filled = alpha_u8 > 0

    if filled.all() or not filled.any():
        return rgb_u8

    h, w = filled.shape

    rgb = rgb_u8.astype(np.float32)

    filled = filled.copy()

    for _ in range(int(iterations)):

        f = filled.astype(np.float32)

        padded_rgb = np.pad(
            rgb * f[..., None],
            ((1, 1), (1, 1), (0, 0)),
            mode="constant",
        )

        padded_f = np.pad(
            f,
            1,
            mode="constant",
        )

        acc = np.zeros_like(rgb)

        count = np.zeros(
            (h, w),
            dtype=np.float32,
        )

        for dy in range(3):
            for dx in range(3):

                if dy == 1 and dx == 1:
                    continue

                acc += padded_rgb[
                    dy:dy + h,
                    dx:dx + w
                ]

                count += padded_f[
                    dy:dy + h,
                    dx:dx + w
                ]

        new_pixels = (~filled) & (count > 0)

        if not new_pixels.any():
            break

        rgb[new_pixels] = (
            acc[new_pixels]
            /
            count[new_pixels, None]
        )

        filled |= new_pixels

    return np.clip(
        rgb + 0.5,
        0,
        255,
    ).astype(np.uint8)


# =============================================================================
# FILE DISCOVERY
# =============================================================================

def gather_files(
    in_dir,
    out_dir,
    recursive,
    name_filter,
    sort_mode,
):

    in_dir = Path(in_dir)
    out_dir = Path(out_dir)

    if recursive:
        iterator = in_dir.rglob("*")
    else:
        iterator = in_dir.iterdir()

    files = []

    for path in iterator:

        try:

            if not path.is_file():
                continue

            if path.suffix.lower() not in EXTS:
                continue

            # Never accidentally process output files when output
            # lives inside input tree.
            if recursive:
                try:
                    path.resolve().relative_to(out_dir.resolve())
                    continue
                except ValueError:
                    pass

            files.append(path)

        except OSError:
            continue

    # -------------------------------------------------------------------------
    # Name filter
    # -------------------------------------------------------------------------

    patterns = [
        x.strip().lower()
        for x in re.split(r"[;,]", name_filter)
        if x.strip()
    ]

    if patterns:

        def matches(path):

            name = path.name.lower()

            for pattern in patterns:

                if any(c in pattern for c in "*?["):
                    candidate = pattern
                else:
                    candidate = f"*{pattern}*"

                if fnmatch.fnmatch(name, candidate):
                    return True

            return False

        files = [
            p for p in files
            if matches(p)
        ]

    # -------------------------------------------------------------------------
    # Sort
    # -------------------------------------------------------------------------

    if sort_mode == "Name (A-Z)":

        files.sort(
            key=lambda p: str(p).lower()
        )

    elif sort_mode == "Size (small first)":

        files.sort(
            key=lambda p: p.stat().st_size
        )

    elif sort_mode == "Size (large first)":

        files.sort(
            key=lambda p: -p.stat().st_size
        )

    elif sort_mode == "Modified (newest first)":

        files.sort(
            key=lambda p: -p.stat().st_mtime
        )

    else:

        files.sort(
            key=lambda p: natural_sort_key(p, in_dir)
        )

    return files


# =============================================================================
# SAVE
# =============================================================================

def save_image(img, path, cfg):

    path = Path(path)

    ext = path.suffix.lower()

    if ext not in FORMATS:
        raise ValueError(
            f"Unsupported output format: {ext}"
        )

    fmt = FORMATS[ext]

    kwargs = {}

    if fmt == "PNG":

        kwargs = {
            "compress_level": int(
                cfg["png_level"]
            )
        }

    elif fmt == "JPEG":

        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        kwargs = {
            "quality": int(
                cfg["jpg_quality"]
            ),
            "subsampling": 0,
        }

    elif fmt == "WEBP":

        kwargs = {
            "lossless": True,
            "quality": 100,
            "method": 4,
        }

    elif fmt == "TIFF":

        kwargs = {
            "compression": "tiff_lzw"
        }

    elif fmt == "BMP":

        if img.mode == "LA":
            img = img.convert("RGBA")

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # -------------------------------------------------------------------------
    # Atomic-ish save
    # -------------------------------------------------------------------------

    tmp = path.with_name(
        path.name + ".part"
    )

    try:

        img.save(
            tmp,
            format=fmt,
            **kwargs,
        )

        os.replace(
            tmp,
            path,
        )

    finally:

        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


# =============================================================================
# ENGINE
# =============================================================================

class Engine:

    def __init__(self, log=print):

        self.log = log

        self.desc = None

        self.builtin = True

        self.scale = 4

        self.in_ch = 3

        self.req = None

        self.dev = None

        self.dtype = None

        self.key = None

        self.tile = 512

        self.overlap = 16

        self.info = ""

    # -------------------------------------------------------------------------
    # UNLOAD
    # -------------------------------------------------------------------------

    def unload(self):

        self.desc = None
        self.key = None
        self.dev = None
        self.dtype = None

        if (
            torch is not None
            and torch.cuda.is_available()
        ):

            try:
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # DEVICE
    # -------------------------------------------------------------------------

    def resolve_device(self, requested):

        if torch is None:
            raise RuntimeError(
                "PyTorch is not installed."
            )

        if requested == "cpu":
            return torch.device("cpu")

        if requested == "auto":

            if torch.cuda.is_available():
                return torch.device("cuda:0")

            return torch.device("cpu")

        if requested.startswith("cuda"):

            if not torch.cuda.is_available():

                self.log(
                    "CUDA requested but unavailable -> CPU"
                )

                return torch.device("cpu")

            return torch.device(requested)

        return torch.device("cpu")

    # -------------------------------------------------------------------------
    # MODEL LOAD
    # -------------------------------------------------------------------------

    def load(
        self,
        path,
        device,
        precision,
        tile,
        overlap,
    ):

        self.tile = max(0, int(tile))
        self.overlap = max(0, int(overlap))

        key = (
            str(path),
            device,
            precision,
        )

        if key == self.key:
            return

        self.unload()

        # ---------------------------------------------------------------------
        # Built-in mode
        # ---------------------------------------------------------------------

        if path in ("", BUILTIN):

            self.builtin = True
            self.scale = 4
            self.desc = None
            self.info = (
                "Built-in Lanczos 4x (no AI)"
            )
            self.key = key

            return

        # ---------------------------------------------------------------------
        # Dependency checks
        # ---------------------------------------------------------------------

        if torch is None:

            raise RuntimeError(
                "PyTorch is not installed:\n"
                + IMPORT_ERR.get(
                    "torch",
                    "",
                )
            )

        if ModelLoader is None:

            raise RuntimeError(
                "Spandrel is not installed:\n"
                "pip install spandrel\n\n"
                + IMPORT_ERR.get(
                    "spandrel",
                    "",
                )
            )

        if not os.path.isfile(path):

            raise FileNotFoundError(path)

        self.log(
            "Loading model: "
            + os.path.basename(path)
            + " ..."
        )

        # ---------------------------------------------------------------------
        # Load model
        # ---------------------------------------------------------------------

        desc = ModelLoader().load_from_file(
            path
        )

        if not isinstance(
            desc,
            ImageModelDescriptor,
        ):

            raise ValueError(
                "This model is not an "
                "image-to-image upscaler model."
            )

        if desc.input_channels not in (1, 3):

            raise ValueError(
                f"Unsupported model input channels: "
                f"{desc.input_channels}"
            )

        # ---------------------------------------------------------------------
        # Device
        # ---------------------------------------------------------------------

        dev = self.resolve_device(device)

        if dev.type == "cuda":

            try:
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
            except Exception:
                pass

        # ---------------------------------------------------------------------
        # Precision
        # ---------------------------------------------------------------------

        requested_precision = precision

        if requested_precision == "auto":

            use_half = False

            if dev.type == "cuda":

                try:

                    major, minor = (
                        torch.cuda.get_device_capability(
                            dev
                        )
                    )

                    # Pascal = 6.x
                    # Deliberately avoid fp16 on Pascal.
                    if major >= 7:
                        use_half = bool(
                            getattr(
                                desc,
                                "supports_half",
                                False,
                            )
                        )

                except Exception:
                    use_half = False

            requested_precision = (
                "fp16"
                if use_half
                else "fp32"
            )

        dtype = torch.float32

        if (
            requested_precision == "fp16"
            and dev.type == "cuda"
            and getattr(
                desc,
                "supports_half",
                False,
            )
        ):

            # Pascal GTX 1060:
            # keep fp32 for reliability/performance.
            try:
                major, _ = (
                    torch.cuda.get_device_capability(
                        dev
                    )
                )

                if major < 7:

                    self.log(
                        "Pascal GPU detected -> "
                        "FP16 disabled; using FP32."
                    )

                    dtype = torch.float32

                else:

                    dtype = torch.float16

            except Exception:

                dtype = torch.float32

        elif (
            requested_precision == "bf16"
            and getattr(
                desc,
                "supports_bfloat16",
                False,
            )
        ):

            dtype = torch.bfloat16

        elif requested_precision != "fp32":

            self.log(
                f"{requested_precision} not supported "
                "by this model/device -> FP32"
            )

            dtype = torch.float32

        # ---------------------------------------------------------------------
        # Move model
        # ---------------------------------------------------------------------

        desc.model.to(
            device=dev,
            dtype=dtype,
        )

        desc.model.eval()

        self.desc = desc
        self.builtin = False
        self.dev = dev
        self.dtype = dtype

        self.scale = max(
            1,
            int(round(float(desc.scale))),
        )

        self.in_ch = int(
            desc.input_channels
        )

        self.req = desc.size_requirements

        try:
            architecture = (
                desc.architecture.name
            )
        except Exception:
            architecture = type(
                desc.model
            ).__name__

        if dtype == torch.float16:
            precision_name = "fp16"
        elif dtype == torch.bfloat16:
            precision_name = "bf16"
        else:
            precision_name = "fp32"

        self.info = (
            f"{os.path.basename(path)} | "
            f"{architecture} | "
            f"{self.scale}x | "
            f"{precision_name} | "
            f"{dev}"
        )

        self.key = key

        self.log(
            "Model ready: "
            + self.info
        )

    # -------------------------------------------------------------------------
    # MODEL INPUT PADDING
    # -------------------------------------------------------------------------

    def _pad_req(self, x):

        _, _, h, w = x.shape

        req = self.req

        if req is None:
            return x, (h, w)

        multiple = max(
            1,
            int(
                getattr(
                    req,
                    "multiple_of",
                    1,
                )
                or 1
            ),
        )

        minimum = int(
            getattr(
                req,
                "minimum",
                0,
            )
            or 0
        )

        square = bool(
            getattr(
                req,
                "square",
                False,
            )
        )

        target_h = max(
            h,
            minimum,
        )

        target_w = max(
            w,
            minimum,
        )

        if square:

            side = max(
                target_h,
                target_w,
            )

            target_h = side
            target_w = side

        target_h = (
            int(
                math.ceil(
                    target_h / multiple
                )
            )
            * multiple
        )

        target_w = (
            int(
                math.ceil(
                    target_w / multiple
                )
            )
            * multiple
        )

        pad_h = target_h - h
        pad_w = target_w - w

        if pad_h == 0 and pad_w == 0:
            return x, (h, w)

        # Reflect requires enough source pixels.
        if (
            pad_h < h
            and pad_w < w
            and h > 1
            and w > 1
        ):

            mode = "reflect"

        else:

            mode = "replicate"

        return (
            F.pad(
                x,
                (
                    0,
                    pad_w,
                    0,
                    pad_h,
                ),
                mode=mode,
            ),
            (h, w),
        )

    # -------------------------------------------------------------------------
    # FORWARD
    # -------------------------------------------------------------------------

    def _forward(self, x):

        padded, (h, w) = self._pad_req(x)

        y = self.desc(padded)

        scale = self.scale

        return y[
            ...,
            :h * scale,
            :w * scale,
        ]

    # -------------------------------------------------------------------------
    # TILED INFERENCE
    # -------------------------------------------------------------------------

    def _infer_tiled(self, x, tile):

        _, _, height, width = x.shape

        if (
            tile <= 0
            or (
                height <= tile
                and width <= tile
            )
        ):

            return self._forward(x)

        scale = self.scale

        overlap = max(
            0,
            min(
                self.overlap,
                tile // 4,
            ),
        )

        # Prevent invalid step.
        step = max(
            1,
            tile - 2 * overlap,
        )

        batch = x.shape[0]

        output = None

        for y0 in range(
            0,
            height,
            step,
        ):

            y1 = min(
                y0 + step,
                height,
            )

            ya = max(
                y0 - overlap,
                0,
            )

            yb = min(
                y1 + overlap,
                height,
            )

            for x0 in range(
                0,
                width,
                step,
            ):

                x1 = min(
                    x0 + step,
                    width,
                )

                xa = max(
                    x0 - overlap,
                    0,
                )

                xb = min(
                    x1 + overlap,
                    width,
                )

                tile_input = x[
                    :,
                    :,
                    ya:yb,
                    xa:xb,
                ]

                tile_output = self._forward(
                    tile_input
                )

                if output is None:

                    output = torch.empty(
                        (
                            batch,
                            tile_output.shape[1],
                            height * scale,
                            width * scale,
                        ),
                        dtype=tile_output.dtype,
                        device="cpu",
                    )

                crop = tile_output[
                    :,
                    :,
                    (y0 - ya) * scale:
                    (y1 - ya) * scale,
                    (x0 - xa) * scale:
                    (x1 - xa) * scale,
                ]

                output[
                    :,
                    :,
                    y0 * scale:
                    y1 * scale,
                    x0 * scale:
                    x1 * scale,
                ] = crop.detach().cpu()

                del tile_output
                del tile_input

        return output

    # -------------------------------------------------------------------------
    # OOM-SAFE INFERENCE
    # -------------------------------------------------------------------------

    def _infer(self, x):

        tile = self.tile

        while True:

            try:

                return self._infer_tiled(
                    x,
                    tile,
                )

            except RuntimeError as e:

                message = str(e).lower()

                if (
                    "out of memory"
                    not in message
                    and "cuda error"
                    not in message
                ):
                    raise

                if (
                    torch is not None
                    and torch.cuda.is_available()
                ):

                    try:
                        torch.cuda.empty_cache()
                    except Exception:
                        pass

                height, width = x.shape[-2:]

                current = (
                    tile
                    if tile > 0
                    else max(
                        height,
                        width,
                    )
                )

                new_tile = max(
                    48,
                    current // 2,
                )

                if new_tile >= current:

                    raise RuntimeError(
                        "GPU ran out of VRAM even "
                        "at the minimum tile size."
                    ) from e

                tile = new_tile

                self.tile = new_tile

                self.log(
                    "Out of VRAM -> tile size "
                    f"reduced to {new_tile}"
                )

    # -------------------------------------------------------------------------
    # RGB INFERENCE
    # -------------------------------------------------------------------------

    def run_rgb(self, arr):

        """
        Input:
            float32 HxWx3, range 0..1

        Output:
            float32 HxWx3, range 0..1
        """

        if self.builtin:

            h, w = arr.shape[:2]

            image = Image.fromarray(
                np.clip(
                    arr * 255.0 + 0.5,
                    0,
                    255,
                ).astype(np.uint8)
            )

            image = image.resize(
                (
                    w * self.scale,
                    h * self.scale,
                ),
                RS.LANCZOS,
            )

            return (
                np.asarray(
                    image,
                    dtype=np.float32,
                )
                / 255.0
            )

        for attempt in range(2):

            with torch.inference_mode():

                tensor = torch.from_numpy(
                    np.ascontiguousarray(
                        arr.transpose(
                            2,
                            0,
                            1,
                        )
                    )
                )[None]

                # 3-channel image -> 3 grayscale batch items
                # for a 1-channel model.
                if self.in_ch == 1:

                    tensor = tensor.permute(
                        1,
                        0,
                        2,
                        3,
                    ).contiguous()

                tensor = tensor.to(
                    self.dev,
                    dtype=self.dtype,
                )

                output = self._infer(
                    tensor
                )

                output = output.float()

                # Detect numerical failure.
                if not bool(
                    torch.isfinite(
                        output
                    ).all()
                ):

                    if (
                        self.dtype != torch.float32
                        and self.desc is not None
                    ):

                        self.log(
                            "NaN/Inf detected -> "
                            "switching model to FP32."
                        )

                        self.desc.model.float()

                        self.dtype = torch.float32

                        del output
                        del tensor

                        if torch.cuda.is_available():

                            try:
                                torch.cuda.empty_cache()
                            except Exception:
                                pass

                        continue

                    raise RuntimeError(
                        "Model produced NaN/Inf "
                        "even in FP32."
                    )

                output = output.clamp(
                    0,
                    1,
                ).cpu()

                if self.in_ch == 1:

                    output = output.permute(
                        1,
                        0,
                        2,
                        3,
                    )

                output = output[0]

                if output.shape[0] == 1:

                    output = output.repeat(
                        3,
                        1,
                        1,
                    )

                elif output.shape[0] > 3:

                    output = output[:3]

                result = output.permute(
                    1,
                    2,
                    0,
                ).contiguous().numpy()

                del output
                del tensor

                return result

        raise RuntimeError(
            "Model produced invalid output."
        )

    # -------------------------------------------------------------------------
    # PROCESS IMAGE
    # -------------------------------------------------------------------------

    def process(self, img, cfg):

        original_mode = img.mode

        # ---------------------------------------------------------------------
        # 16-bit images
        # ---------------------------------------------------------------------

        if original_mode in (
            "I;16",
            "I;16L",
            "I;16B",
        ):

            data = np.asarray(
                img,
                dtype=np.uint16,
            )

            data = (
                data >> 8
            ).astype(
                np.uint8
            )

            img = Image.fromarray(
                data,
                mode="L",
            )

            original_mode = "L"

        bands = img.getbands()

        has_alpha = (
            "A" in bands
            or "a" in bands
            or (
                original_mode == "P"
                and "transparency" in img.info
            )
        )

        gray_source = original_mode in (
            "L",
            "LA",
            "La",
            "1",
        )

        rgba = img.convert(
            "RGBA"
            if has_alpha
            else "RGB"
        )

        width, height = rgba.size

        array = np.asarray(
            rgba
        )

        rgb_u8 = np.ascontiguousarray(
            array[..., :3]
        )

        alpha_u8 = (
            np.ascontiguousarray(
                array[..., 3]
            )
            if has_alpha
            else None
        )

        opaque = (
            has_alpha
            and bool(
                np.all(
                    alpha_u8 == 255
                )
            )
        )

        flat_alpha = False

        if has_alpha and not opaque:

            alpha_min = int(
                alpha_u8.min()
            )

            alpha_max = int(
                alpha_u8.max()
            )

            flat_alpha = (
                alpha_min == alpha_max
            )

        # ---------------------------------------------------------------------
        # Target size
        # ---------------------------------------------------------------------

        native_scale = max(
            1,
            int(self.scale),
        )

        target_scale = cfg[
            "target_scale"
        ]

        effective_scale = (
            float(native_scale)
            if target_scale is None
            else float(target_scale)
        )

        target_width = max(
            1,
            int(
                round(
                    width
                    * effective_scale
                )
            ),
        )

        target_height = max(
            1,
            int(
                round(
                    height
                    * effective_scale
                )
            ),
        )

        # ---------------------------------------------------------------------
        # Maximum side
        # ---------------------------------------------------------------------

        max_side = int(
            cfg["max_side"]
        )

        if (
            max_side > 0
            and max(
                target_width,
                target_height,
            ) > max_side
        ):

            factor = (
                max_side
                /
                max(
                    target_width,
                    target_height,
                )
            )

            target_width = max(
                1,
                int(
                    round(
                        target_width
                        * factor
                    )
                ),
            )

            target_height = max(
                1,
                int(
                    round(
                        target_height
                        * factor
                    )
                ),
            )

        # ---------------------------------------------------------------------
        # Optional rounding
        # ---------------------------------------------------------------------

        round_multiple = max(
            1,
            int(
                cfg["round_mult"]
            ),
        )

        if round_multiple > 1:

            target_width = max(
                round_multiple,
                int(
                    round(
                        target_width
                        / round_multiple
                    )
                    * round_multiple
                ),
            )

            target_height = max(
                round_multiple,
                int(
                    round(
                        target_height
                        / round_multiple
                    )
                    * round_multiple
                ),
            )

        # ---------------------------------------------------------------------
        # Number of model passes
        # ---------------------------------------------------------------------

        effective_output_scale = max(
            target_width / width,
            target_height / height,
        )

        if (
            effective_output_scale
            <= native_scale
            or native_scale <= 1
        ):

            passes = 1

        else:

            passes = max(
                1,
                int(
                    math.ceil(
                        math.log(
                            effective_output_scale
                        )
                        /
                        math.log(
                            native_scale
                        )
                        - 1e-6
                    )
                ),
            )

        seamless_pad = (
            int(cfg["seamless_pad"])
            if cfg["seamless"]
            else 0
        )

        # ---------------------------------------------------------------------
        # Upscale helper
        # ---------------------------------------------------------------------

        def upscale(rgb_float):

            current = rgb_float

            if seamless_pad:

                current = np.pad(
                    current,
                    (
                        (
                            seamless_pad,
                            seamless_pad,
                        ),
                        (
                            seamless_pad,
                            seamless_pad,
                        ),
                        (
                            0,
                            0,
                        ),
                    ),
                    mode="wrap",
                )

            for _ in range(passes):

                current = self.run_rgb(
                    current
                )

            if seamless_pad:

                crop = (
                    seamless_pad
                    * (
                        native_scale
                        ** passes
                    )
                )

                if crop > 0:

                    current = current[
                        crop:-crop,
                        crop:-crop,
                        :,
                    ]

            return current

        # ---------------------------------------------------------------------
        # Alpha colour bleed
        # ---------------------------------------------------------------------

        if (
            cfg["bleed"]
            and has_alpha
            and not opaque
        ):

            rgb_u8 = bleed_colors(
                rgb_u8,
                alpha_u8,
                int(
                    cfg["bleed_iters"]
                ),
            )

        # ---------------------------------------------------------------------
        # RGB AI
        # ---------------------------------------------------------------------

        rgb_float = (
            rgb_u8.astype(
                np.float32
            )
            / 255.0
        )

        upscaled = upscale(
            rgb_float
        )

        rgb_img = Image.fromarray(
            np.clip(
                upscaled * 255.0
                + 0.5,
                0,
                255,
            ).astype(
                np.uint8
            )
        )

        # Final exact target dimensions.
        if rgb_img.size != (
            target_width,
            target_height,
        ):

            rgb_img = rgb_img.resize(
                (
                    target_width,
                    target_height,
                ),
                RS.LANCZOS,
            )

        # ---------------------------------------------------------------------
        # AI strength
        # ---------------------------------------------------------------------

        strength = (
            float(
                cfg["ai_strength"]
            )
            / 100.0
        )

        if strength < 0.999999:

            base = Image.fromarray(
                rgb_u8
            ).resize(
                (
                    target_width,
                    target_height,
                ),
                RS.LANCZOS,
            )

            ai_array = np.asarray(
                rgb_img,
                dtype=np.float32,
            )

            base_array = np.asarray(
                base,
                dtype=np.float32,
            )

            mixed = (
                ai_array * strength
                +
                base_array
                * (
                    1.0
                    - strength
                )
            )

            rgb_img = Image.fromarray(
                np.clip(
                    mixed + 0.5,
                    0,
                    255,
                ).astype(
                    np.uint8
                )
            )

        # ---------------------------------------------------------------------
        # Sharpen
        # ---------------------------------------------------------------------

        sharpen_amount = float(
            cfg["sharpen"]
        )

        if sharpen_amount > 0:

            rgb_img = rgb_img.filter(
                ImageFilter.UnsharpMask(
                    radius=float(
                        cfg[
                            "sharpen_radius"
                        ]
                    ),
                    percent=int(
                        sharpen_amount
                    ),
                    threshold=int(
                        cfg[
                            "sharpen_thr"
                        ]
                    ),
                )
            )

        # ---------------------------------------------------------------------
        # Alpha
        # ---------------------------------------------------------------------

        if has_alpha:

            if opaque:

                alpha_img = Image.new(
                    "L",
                    (
                        target_width,
                        target_height,
                    ),
                    255,
                )

            elif flat_alpha:

                alpha_img = Image.new(
                    "L",
                    (
                        target_width,
                        target_height,
                    ),
                    int(
                        alpha_u8[0, 0]
                    ),
                )

            else:

                alpha_mode = cfg[
                    "alpha_mode"
                ]

                if alpha_mode.startswith(
                    "AI"
                ):

                    alpha_float = (
                        alpha_u8.astype(
                            np.float32
                        )
                        / 255.0
                    )

                    alpha_rgb = np.repeat(
                        alpha_float[
                            ...,
                            None,
                        ],
                        3,
                        axis=2,
                    )

                    alpha_upscaled = (
                        upscale(
                            alpha_rgb
                        )
                    )

                    alpha_channel = (
                        alpha_upscaled.mean(
                            axis=2
                        )
                    )

                    alpha_img = Image.fromarray(
                        np.clip(
                            alpha_channel
                            * 255.0
                            + 0.5,
                            0,
                            255,
                        ).astype(
                            np.uint8
                        )
                    )

                    if alpha_img.size != (
                        target_width,
                        target_height,
                    ):

                        alpha_img = alpha_img.resize(
                            (
                                target_width,
                                target_height,
                            ),
                            RS.LANCZOS,
                        )

                else:

                    alpha_img = (
                        Image.fromarray(
                            alpha_u8,
                            mode="L",
                        ).resize(
                            (
                                target_width,
                                target_height,
                            ),
                            ALPHA_FILTERS.get(
                                alpha_mode,
                                RS.LANCZOS,
                            ),
                        )
                    )

                # -------------------------------------------------------------
                # Optional alpha binarization
                # -------------------------------------------------------------

                if cfg[
                    "alpha_binarize"
                ]:

                    threshold = int(
                        cfg[
                            "alpha_thr"
                        ]
                    )

                    alpha_img = alpha_img.point(
                        lambda value,
                        threshold=threshold:
                        255
                        if value >= threshold
                        else 0
                    )

            output = rgb_img.convert(
                "RGBA"
            )

            output.putalpha(
                alpha_img
            )

        else:

            output = rgb_img

        # ---------------------------------------------------------------------
        # Restore grayscale
        # ---------------------------------------------------------------------

        if gray_source:

            output = output.convert(
                "LA"
                if has_alpha
                else "L"
            )

        info = (
            f"{width}x{height}"
            f" -> "
            f"{target_width}x{target_height}"
        )

        if has_alpha and not opaque:
            info += " +alpha"

        if passes > 1:
            info += (
                f" [{passes} passes]"
            )

        return output, info


# =============================================================================
# GUI
# =============================================================================

class App(tk.Tk):

    def __init__(self):

        super().__init__()

        self.title(
            "GTA Batch AI Upscaler"
        )

        self.geometry(
            "1320x900"
        )

        self.minsize(
            1050,
            720,
        )

        # ---------------------------------------------------------------------
        # DPI
        # ---------------------------------------------------------------------

        try:

            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(
                1
            )

        except Exception:
            pass

        # ---------------------------------------------------------------------
        # Theme
        # ---------------------------------------------------------------------

        try:

            ttk.Style().theme_use(
                "vista"
            )

        except tk.TclError:

            ttk.Style().theme_use(
                "clam"
            )

        # ---------------------------------------------------------------------
        # Runtime state
        # ---------------------------------------------------------------------

        self.q = queue.Queue()

        self.engine = Engine(
            lambda message:
            self.q.put(
                ("log", message)
            )
        )

        self.worker = None

        self.pause_evt = (
            threading.Event()
        )

        self.pause_evt.set()

        self.stop_evt = (
            threading.Event()
        )

        self.skipped = []
        self.failed = []

        self.last_pair = None
        self.last_stats = {}

        self._photos = [
            None,
            None,
        ]

        self._resize_job = None

        self._nvml = None

        self.ready = False

        # ---------------------------------------------------------------------
        # Build
        # ---------------------------------------------------------------------

        self._make_vars()

        self._build_ui()

        self._load_settings()

        self._refresh_models()

        threading.Thread(
            target=self._bg_import,
            daemon=True,
        ).start()

        self.after(
            50,
            self._poll,
        )

        self.after(
            1000,
            self._monitor,
        )

        self.protocol(
            "WM_DELETE_WINDOW",
            self._on_close,
        )

    # =========================================================================
    # VARIABLES
    # =========================================================================

    def _make_vars(self):

        self.v = {}

        for key, default in DEFAULTS.items():

            if isinstance(
                default,
                bool,
            ):

                self.v[key] = (
                    tk.BooleanVar(
                        value=default
                    )
                )

            elif isinstance(
                default,
                int,
            ):

                self.v[key] = (
                    tk.IntVar(
                        value=default
                    )
                )

            elif isinstance(
                default,
                float,
            ):

                self.v[key] = (
                    tk.DoubleVar(
                        value=default
                    )
                )

            else:

                self.v[key] = (
                    tk.StringVar(
                        value=default
                    )
                )

    # =========================================================================
    # CONFIG
    # =========================================================================

    def get_cfg(self):

        cfg = {}

        for key, variable in self.v.items():

            try:

                cfg[key] = variable.get()

            except tk.TclError:

                cfg[key] = DEFAULTS[key]

        try:

            cfg["target_scale"] = (
                parse_scale(
                    cfg["scale"]
                )
            )

        except Exception:

            cfg["target_scale"] = None

        return cfg

    # =========================================================================
    # SETTINGS
    # =========================================================================

    def _load_settings(self):

        try:

            data = json.loads(
                SETTINGS_FILE.read_text(
                    encoding="utf-8"
                )
            )

            for key, value in data.items():

                if key not in self.v:
                    continue

                try:

                    self.v[key].set(
                        value
                    )

                except Exception:
                    pass

        except Exception:
            pass

    def _save_settings(self):

        try:

            data = {}

            for key, variable in self.v.items():

                try:

                    data[key] = (
                        variable.get()
                    )

                except tk.TclError:

                    data[key] = (
                        DEFAULTS[key]
                    )

            SETTINGS_FILE.write_text(
                json.dumps(
                    data,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        except Exception:
            pass

    def _reset_settings(self):

        keep = {
            "input_dir",
            "output_dir",
            "model_path",
        }

        for key, default in DEFAULTS.items():

            if key not in keep:

                self.v[key].set(
                    default
                )

    # =========================================================================
    # UI
    # =========================================================================

    def _build_ui(self):

        notebook = ttk.Notebook(
            self
        )

        notebook.pack(
            fill="both",
            expand=True,
        )

        self.tab_main = ttk.Frame(
            notebook
        )

        self.tab_settings = ttk.Frame(
            notebook
        )

        self.tab_help = ttk.Frame(
            notebook
        )

        notebook.add(
            self.tab_main,
            text="  Main  ",
        )

        notebook.add(
            self.tab_settings,
            text="  Settings  ",
        )

        notebook.add(
            self.tab_help,
            text="  Help  ",
        )

        self.nb = notebook

        self._build_main(
            self.tab_main
        )

        self._build_settings(
            self.tab_settings
        )

        self._build_help(
            self.tab_help
        )

    # =========================================================================
    # MAIN TAB
    # =========================================================================

    def _build_main(self, tab):

        tab.columnconfigure(
            0,
            weight=1,
        )

        tab.rowconfigure(
            4,
            weight=1,
        )

        top = ttk.Frame(tab)

        top.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=8,
            pady=(8, 2),
        )

        top.columnconfigure(
            1,
            weight=1,
        )

        ttk.Label(
            top,
            text="Input:",
        ).grid(
            row=0,
            column=0,
            sticky="e",
            padx=4,
            pady=2,
        )

        ttk.Entry(
            top,
            textvariable=self.v[
                "input_dir"
            ],
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=2,
        )

        ttk.Button(
            top,
            text="...",
            width=4,
            command=lambda:
            self._browse_dir(
                "input_dir"
            ),
        ).grid(
            row=0,
            column=2,
            padx=4,
        )

        ttk.Label(
            top,
            text="Output:",
        ).grid(
            row=1,
            column=0,
            sticky="e",
            padx=4,
            pady=2,
        )

        ttk.Entry(
            top,
            textvariable=self.v[
                "output_dir"
            ],
        ).grid(
            row=1,
            column=1,
            sticky="ew",
            pady=2,
        )

        ttk.Button(
            top,
            text="...",
            width=4,
            command=lambda:
            self._browse_dir(
                "output_dir"
            ),
        ).grid(
            row=1,
            column=2,
            padx=4,
        )

        ttk.Label(
            top,
            text="Model:",
        ).grid(
            row=2,
            column=0,
            sticky="e",
            padx=4,
            pady=2,
        )

        self.cb_model = (
            ttk.Combobox(
                top,
                textvariable=self.v[
                    "model_path"
                ],
            )
        )

        self.cb_model.grid(
            row=2,
            column=1,
            sticky="ew",
            pady=2,
        )

        model_buttons = ttk.Frame(
            top
        )

        model_buttons.grid(
            row=2,
            column=2,
            padx=4,
        )

        ttk.Button(
            model_buttons,
            text="Browse",
            width=8,
            command=self._browse_model,
        ).pack(
            side="left"
        )

        ttk.Button(
            model_buttons,
            text="Reload list",
            width=10,
            command=self._refresh_models,
        ).pack(
            side="left",
            padx=(4, 0),
        )

        # ---------------------------------------------------------------------
        # Row 3
        # ---------------------------------------------------------------------

        row3 = ttk.Frame(tab)

        row3.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=8,
            pady=2,
        )

        ttk.Label(
            row3,
            text="Output scale:",
        ).pack(
            side="left",
            padx=(4, 2),
        )

        ttk.Combobox(
            row3,
            textvariable=self.v[
                "scale"
            ],
            values=SCALE_CHOICES,
            width=14,
        ).pack(
            side="left"
        )

        ttk.Label(
            row3,
            text="  Device:",
        ).pack(
            side="left"
        )

        self.cb_dev = ttk.Combobox(
            row3,
            textvariable=self.v[
                "device"
            ],
            values=[
                "auto",
                "cpu",
            ],
            width=10,
            state="readonly",
        )

        self.cb_dev.pack(
            side="left"
        )

        ttk.Label(
            row3,
            text="  Precision:",
        ).pack(
            side="left"
        )

        ttk.Combobox(
            row3,
            textvariable=self.v[
                "precision"
            ],
            values=[
                "auto",
                "fp16",
                "bf16",
                "fp32",
            ],
            width=7,
            state="readonly",
        ).pack(
            side="left"
        )

        ttk.Label(
            row3,
            text="  Tile:",
        ).pack(
            side="left"
        )

        ttk.Spinbox(
            row3,
            textvariable=self.v[
                "tile"
            ],
            from_=0,
            to=4096,
            increment=64,
            width=6,
        ).pack(
            side="left"
        )

        ttk.Checkbutton(
            row3,
            text="Recursive",
            variable=self.v[
                "recursive"
            ],
        ).pack(
            side="left",
            padx=(12, 0),
        )

        ttk.Checkbutton(
            row3,
            text="Skip existing",
            variable=self.v[
                "skip_existing"
            ],
        ).pack(
            side="left",
            padx=(8, 0),
        )

        self.lbl_model = ttk.Label(
            row3,
            text="",
            foreground="#555",
        )

        self.lbl_model.pack(
            side="left",
            padx=12,
        )

        # ---------------------------------------------------------------------
        # Buttons
        # ---------------------------------------------------------------------

        buttons = ttk.Frame(tab)

        buttons.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=8,
            pady=4,
        )

        self.btn_start = ttk.Button(
            buttons,
            text="\u25B6  Start",
            width=12,
            command=self.start,
            state="disabled",
        )

        self.btn_start.pack(
            side="left",
            padx=2,
        )

        self.btn_pause = ttk.Button(
            buttons,
            text="\u23F8  Pause",
            width=12,
            command=self.toggle_pause,
            state="disabled",
        )

        self.btn_pause.pack(
            side="left",
            padx=2,
        )

        self.btn_stop = ttk.Button(
            buttons,
            text="\u23F9  Stop (after current)",
            command=self.stop,
            state="disabled",
        )

        self.btn_stop.pack(
            side="left",
            padx=2,
        )

        self.btn_requeue = ttk.Button(
            buttons,
            text="Requeue skipped/failed",
            command=self.requeue,
            state="disabled",
        )

        self.btn_requeue.pack(
            side="left",
            padx=(14, 2),
        )

        ttk.Button(
            buttons,
            text="Stats",
            command=self.show_stats,
        ).pack(
            side="left",
            padx=2,
        )

        ttk.Button(
            buttons,
            text="Open output",
            command=self.open_output,
        ).pack(
            side="left",
            padx=2,
        )

        ttk.Button(
            buttons,
            text="Settings",
            command=lambda:
            self.nb.select(
                self.tab_settings
            ),
        ).pack(
            side="left",
            padx=2,
        )

        self.lbl_mon = ttk.Label(
            buttons,
            text="",
        )

        self.lbl_mon.pack(
            side="right",
            padx=6,
        )

        # ---------------------------------------------------------------------
        # Progress
        # ---------------------------------------------------------------------

        progress = ttk.Frame(tab)

        progress.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=8,
            pady=2,
        )

        progress.columnconfigure(
            0,
            weight=1,
        )

        self.pbar = ttk.Progressbar(
            progress,
            mode="determinate",
            maximum=100,
        )

        self.pbar.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=4,
        )

        self.lbl_prog = ttk.Label(
            progress,
            text="Idle",
        )

        self.lbl_prog.grid(
            row=0,
            column=1,
            padx=8,
        )

        self.lbl_cur = ttk.Label(
            progress,
            text="",
            foreground="#0a4a9a",
        )

        self.lbl_cur.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            padx=4,
        )

        # ---------------------------------------------------------------------
        # Preview
        # ---------------------------------------------------------------------

        preview = ttk.LabelFrame(
            tab,
            text=(
                "Preview - last COMPLETED image "
                "(fit to panel, aspect ratio preserved)"
            ),
        )

        preview.grid(
            row=4,
            column=0,
            sticky="nsew",
            padx=8,
            pady=4,
        )

        preview.columnconfigure(
            0,
            weight=1,
            uniform="preview",
        )

        preview.columnconfigure(
            1,
            weight=1,
            uniform="preview",
        )

        preview.rowconfigure(
            1,
            weight=1,
        )

        ttk.Label(
            preview,
            text="Original",
        ).grid(
            row=0,
            column=0,
        )

        ttk.Label(
            preview,
            text="Upscaled",
        ).grid(
            row=0,
            column=1,
        )

        self.cv_o = tk.Canvas(
            preview,
            bg="#1c1c1c",
            highlightthickness=0,
        )

        self.cv_u = tk.Canvas(
            preview,
            bg="#1c1c1c",
            highlightthickness=0,
        )

        self.cv_o.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=(6, 3),
            pady=4,
        )

        self.cv_u.grid(
            row=1,
            column=1,
            sticky="nsew",
            padx=(3, 6),
            pady=4,
        )

        self.lbl_prev = ttk.Label(
            preview,
            text="Nothing finished yet",
        )

        self.lbl_prev.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            padx=6,
            pady=(0, 4),
        )

        self.cv_o.bind(
            "<Configure>",
            self._on_canvas_resize,
        )

        self.cv_u.bind(
            "<Configure>",
            self._on_canvas_resize,
        )

        # ---------------------------------------------------------------------
        # Activity
        # ---------------------------------------------------------------------

        activity = ttk.LabelFrame(
            tab,
            text="Activity",
        )

        activity.grid(
            row=5,
            column=0,
            sticky="ew",
            padx=8,
            pady=(0, 8),
        )

        activity.columnconfigure(
            0,
            weight=1,
        )

        self.txt = tk.Text(
            activity,
            height=9,
            bg="#101010",
            fg="#dcdcdc",
            font=("Consolas", 9),
            wrap="none",
        )

        scrollbar = ttk.Scrollbar(
            activity,
            command=self.txt.yview,
        )

        self.txt.configure(
            yscrollcommand=scrollbar.set
        )

        self.txt.grid(
            row=0,
            column=0,
            sticky="ew",
        )

        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
        )

        self.txt.tag_configure(
            "err",
            foreground="#ff7a7a",
        )

        self.txt.tag_configure(
            "ok",
            foreground="#7ee787",
        )

        self.txt.tag_configure(
            "warn",
            foreground="#ffd479",
        )

    # =========================================================================
    # SETTINGS HELPERS
    # =========================================================================

    def _row(
        self,
        parent,
        row,
        label,
        widget_factory,
        hint="",
    ):

        ttk.Label(
            parent,
            text=label,
        ).grid(
            row=row,
            column=0,
            sticky="w",
            padx=6,
            pady=2,
        )

        widget = widget_factory(
            parent
        )

        widget.grid(
            row=row,
            column=1,
            sticky="w",
            padx=6,
            pady=2,
        )

        if hint:

            ttk.Label(
                parent,
                text=hint,
                foreground="#777",
            ).grid(
                row=row,
                column=2,
                sticky="w",
            )

        return widget

    def _spin(
        self,
        key,
        low,
        high,
        increment=1,
        width=8,
    ):

        return lambda parent: ttk.Spinbox(
            parent,
            textvariable=self.v[key],
            from_=low,
            to=high,
            increment=increment,
            width=width,
        )

    def _combo(
        self,
        key,
        values,
        width=22,
        state="readonly",
    ):

        return lambda parent: ttk.Combobox(
            parent,
            textvariable=self.v[key],
            values=values,
            width=width,
            state=state,
        )

    def _slider(
        self,
        key,
        low,
        high,
        resolution=1,
    ):

        return lambda parent: tk.Scale(
            parent,
            from_=low,
            to=high,
            orient="horizontal",
            resolution=resolution,
            length=220,
            variable=self.v[key],
        )

    def _check(
        self,
        parent,
        row,
        key,
        text,
        hint="",
    ):

        ttk.Checkbutton(
            parent,
            text=text,
            variable=self.v[key],
        ).grid(
            row=row,
            column=0,
            columnspan=2,
            sticky="w",
            padx=6,
            pady=2,
        )

        if hint:

            ttk.Label(
                parent,
                text=hint,
                foreground="#777",
            ).grid(
                row=row,
                column=2,
                sticky="w",
            )

    # =========================================================================
    # SETTINGS TAB
    # =========================================================================

    def _build_settings(self, tab):

        tab.columnconfigure(
            0,
            weight=1,
        )

        tab.columnconfigure(
            1,
            weight=1,
        )

        left = ttk.Frame(tab)
        right = ttk.Frame(tab)

        left.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=6,
            pady=6,
        )

        right.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=6,
            pady=6,
        )

        # ---------------------------------------------------------------------
        # Model / GPU
        # ---------------------------------------------------------------------

        group = ttk.LabelFrame(
            left,
            text="Model / GPU",
        )

        group.pack(
            fill="x",
            pady=4,
        )

        self.cb_dev2 = self._row(
            group,
            0,
            "Device",
            self._combo(
                "device",
                [
                    "auto",
                    "cpu",
                ],
                12,
            ),
        )

        self._row(
            group,
            1,
            "Precision",
            self._combo(
                "precision",
                [
                    "auto",
                    "fp16",
                    "bf16",
                    "fp32",
                ],
                12,
            ),
        )

        self._row(
            group,
            2,
            "Tile size",
            self._spin(
                "tile",
                0,
                4096,
                64,
            ),
            "0 = whole image; auto-halves on VRAM error",
        )

        self._row(
            group,
            3,
            "Tile overlap",
            self._spin(
                "overlap",
                0,
                128,
                2,
            ),
        )

        self._check(
            group,
            4,
            "free_vram",
            "Free VRAM cache after every image",
        )

        # ---------------------------------------------------------------------
        # Size
        # ---------------------------------------------------------------------

        group = ttk.LabelFrame(
            left,
            text="Size / filters",
        )

        group.pack(
            fill="x",
            pady=4,
        )

        self._row(
            group,
            0,
            "Max output side (px)",
            self._spin(
                "max_side",
                0,
                16384,
                256,
            ),
            "0 = no limit",
        )

        self._row(
            group,
            1,
            "Round output size to multiple of",
            self._spin(
                "round_mult",
                1,
                64,
                1,
            ),
            "1 = OFF; keep 1 for exact texture geometry",
        )

        self._row(
            group,
            2,
            "Skip if longer side >=",
            self._spin(
                "skip_over",
                0,
                16384,
                128,
            ),
            "0 = off",
        )

        self._row(
            group,
            3,
            "Skip if longer side <=",
            self._spin(
                "skip_under",
                0,
                16384,
                8,
            ),
            "0 = off",
        )

        self._check(
            group,
            4,
            "copy_skipped",
            "Copy size-skipped files unchanged",
        )

        # ---------------------------------------------------------------------
        # Alpha
        # ---------------------------------------------------------------------

        group = ttk.LabelFrame(
            left,
            text="Alpha / transparency",
        )

        group.pack(
            fill="x",
            pady=4,
        )

        self._row(
            group,
            0,
            "Alpha upscale",
            self._combo(
                "alpha_mode",
                ALPHA_MODES,
                14,
            ),
            "AI or conventional resize",
        )

        self._check(
            group,
            1,
            "bleed",
            "Bleed colours into transparent pixels",
        )

        self._row(
            group,
            2,
            "Bleed iterations",
            self._spin(
                "bleed_iters",
                0,
                64,
                1,
            ),
        )

        self._check(
            group,
            3,
            "alpha_binarize",
            "Binarize alpha",
        )

        self._row(
            group,
            4,
            "Binarize threshold",
            self._slider(
                "alpha_thr",
                1,
                255,
            ),
        )

        # ---------------------------------------------------------------------
        # Look
        # ---------------------------------------------------------------------

        group = ttk.LabelFrame(
            right,
            text="Look / post-processing",
        )

        group.pack(
            fill="x",
            pady=4,
        )

        self._row(
            group,
            0,
            "AI strength %",
            self._slider(
                "ai_strength",
                0,
                100,
            ),
            "<100 blends AI with Lanczos",
        )

        self._row(
            group,
            1,
            "Sharpen amount %",
            self._slider(
                "sharpen",
                0,
                300,
            ),
        )

        self._row(
            group,
            2,
            "Sharpen radius",
            self._slider(
                "sharpen_radius",
                0.3,
                5.0,
                0.1,
            ),
        )

        self._row(
            group,
            3,
            "Sharpen threshold",
            self._slider(
                "sharpen_thr",
                0,
                50,
            ),
        )

        self._check(
            group,
            4,
            "seamless",
            "Seamless wrap padding",
        )

        self._row(
            group,
            5,
            "Wrap padding (px)",
            self._spin(
                "seamless_pad",
                1,
                64,
                1,
            ),
        )

        # ---------------------------------------------------------------------
        # Files
        # ---------------------------------------------------------------------

        group = ttk.LabelFrame(
            right,
            text="Files / output",
        )

        group.pack(
            fill="x",
            pady=4,
        )

        self._check(
            group,
            0,
            "recursive",
            "Include sub-folders",
        )

        self._check(
            group,
            1,
            "skip_existing",
            "Skip files already in output",
        )

        self._row(
            group,
            2,
            "Name filter",
            lambda p:
            ttk.Entry(
                p,
                textvariable=self.v[
                    "name_filter"
                ],
                width=26,
            ),
            "e.g. *sign*;logo",
        )

        self._row(
            group,
            3,
            "Order",
            self._combo(
                "sort_mode",
                SORT_MODES,
                22,
            ),
        )

        self._row(
            group,
            4,
            "Output extension",
            self._combo(
                "out_ext",
                [
                    "Same as source",
                    ".png",
                ],
                16,
            ),
        )

        self._row(
            group,
            5,
            "PNG compression",
            self._spin(
                "png_level",
                0,
                9,
                1,
            ),
            "0 = fastest",
        )

        self._row(
            group,
            6,
            "JPG quality",
            self._spin(
                "jpg_quality",
                50,
                100,
                1,
            ),
        )

        # ---------------------------------------------------------------------
        # Performance
        # ---------------------------------------------------------------------

        group = ttk.LabelFrame(
            right,
            text="Performance / UI",
        )

        group.pack(
            fill="x",
            pady=4,
        )

        self._row(
            group,
            0,
            "Prefetch queue",
            self._spin(
                "prefetch",
                1,
                8,
                1,
            ),
            "keep low on 16 GB RAM",
        )

        self._row(
            group,
            1,
            "Save threads",
            self._spin(
                "save_threads",
                1,
                4,
                1,
            ),
        )

        self._check(
            group,
            2,
            "show_preview",
            "Update before/after preview",
        )

        self._check(
            group,
            3,
            "checker",
            "Checkerboard behind transparency",
        )

        self._check(
            group,
            4,
            "open_when_done",
            "Open output folder when finished",
        )

        buttons = ttk.Frame(
            right
        )

        buttons.pack(
            fill="x",
            pady=8,
        )

        ttk.Button(
            buttons,
            text="Reset to defaults",
            command=self._reset_settings,
        ).pack(
            side="left",
            padx=4,
        )

        ttk.Button(
            buttons,
            text="Save settings now",
            command=self._save_settings,
        ).pack(
            side="left",
            padx=4,
        )

        ttk.Label(
            buttons,
            text=(
                "Settings are also saved automatically."
            ),
            foreground="#777",
        ).pack(
            side="left"
        )

    # =========================================================================
    # HELP
    # =========================================================================

    def _build_help(self, tab):

        text = tk.Text(
            tab,
            wrap="word",
            font=("Segoe UI", 10),
            padx=12,
            pady=10,
        )

        text.pack(
            fill="both",
            expand=True,
        )

        text.insert(
            "1.0",
            (
                "GTA BATCH AI UPSCALER\n\n"

                "1. Choose Input and Output folders.\n"
                "   They must be different.\n\n"

                "2. Put your AI model inside the models folder.\n"
                "   Supported: PTH / PT / SAFETENSORS / CKPT.\n\n"

                "3. Native (model) uses the model's native scale.\n"
                "   Example: a 4x model turns 512x512 into 2048x2048.\n\n"

                "4. For GTA SA textures keep:\n"
                "   Round output size = 1\n"
                "   AI strength = 100%\n"
                "   This avoids unnecessary geometric modification.\n\n"

                "5. Transparent textures preserve their alpha channel.\n"
                "   RGB colours can be bled into transparent pixels before\n"
                "   AI processing to reduce edge halos.\n\n"

                "6. GTX 1060 / Pascal:\n"
                "   FP32 is recommended. Auto precision deliberately avoids\n"
                "   FP16 on Pascal because FP16 performance is poor.\n\n"

                "7. If VRAM is exhausted, tile size is automatically reduced.\n\n"

                "8. Stop finishes the current image, then stops.\n"
                "   Pause waits before the next image.\n\n"

                "9. Skip existing allows interrupted batches to resume.\n"
            ),
        )

        text.configure(
            state="disabled"
        )

    # =========================================================================
    # BROWSE
    # =========================================================================

    def _browse_dir(self, key):

        current = (
            self.v[key].get()
            or None
        )

        directory = filedialog.askdirectory(
            initialdir=current
        )

        if directory:

            self.v[key].set(
                os.path.normpath(
                    directory
                )
            )

    def _browse_model(self):

        initial = (
            str(MODELS_DIR)
            if MODELS_DIR.exists()
            else None
        )

        filename = (
            filedialog.askopenfilename(
                initialdir=initial,
                filetypes=[
                    (
                        "Model files",
                        "*.pth *.pt *.safetensors *.ckpt",
                    ),
                    (
                        "All files",
                        "*.*",
                    ),
                ],
            )
        )

        if filename:

            self.v[
                "model_path"
            ].set(
                os.path.normpath(
                    filename
                )
            )

    # =========================================================================
    # MODELS
    # =========================================================================

    def _refresh_models(self):

        try:

            MODELS_DIR.mkdir(
                exist_ok=True
            )

        except OSError:
            pass

        found = []

        if MODELS_DIR.exists():

            found = sorted(
                str(path)
                for path in MODELS_DIR.iterdir()
                if (
                    path.is_file()
                    and
                    path.suffix.lower()
                    in MODEL_EXTS
                )
            )

        self.cb_model[
            "values"
        ] = [
            BUILTIN
        ] + found

        current = (
            self.v[
                "model_path"
            ].get()
        )

        if (
            current in (
                "",
                BUILTIN,
            )
            and found
        ):

            self.v[
                "model_path"
            ].set(
                found[0]
            )

    # =========================================================================
    # OUTPUT
    # =========================================================================

    def open_output(self):

        directory = (
            self.v[
                "output_dir"
            ].get()
        )

        if (
            directory
            and
            os.path.isdir(directory)
        ):

            try:

                os.startfile(
                    directory
                )

            except AttributeError:

                import subprocess

                subprocess.Popen(
                    [
                        "xdg-open",
                        directory,
                    ]
                )

        else:

            messagebox.showinfo(
                "Output",
                "Output folder does not exist yet.",
            )

    # =========================================================================
    # STATS
    # =========================================================================

    def show_stats(self):

        stats = self.last_stats

        if not stats:

            messagebox.showinfo(
                "Stats",
                "No run yet.",
            )

            return

        messagebox.showinfo(
            "Stats",
            (
                f"Total: {stats.get('total', 0)}\n"
                f"Upscaled: {stats.get('ok', 0)}\n"
                f"Skipped: {stats.get('skip', 0)}\n"
                f"Failed: {stats.get('fail', 0)}\n"
                f"Elapsed: "
                f"{fmt_time(stats.get('elapsed', 0))}\n"
                f"Average per upscaled image: "
                f"{stats.get('avg', 0):.2f}s"
            ),
        )

    # =========================================================================
    # LOG
    # =========================================================================

    def log(self, message, tag=None):

        self.txt.insert(
            "end",
            time.strftime(
                "[%H:%M:%S] "
            )
            + str(message)
            + "\n",
            tag,
        )

        lines = int(
            self.txt.index(
                "end-1c"
            ).split(".")[0]
        )

        if lines > 3000:

            self.txt.delete(
                "1.0",
                f"{lines - 2500}.0",
            )

        self.txt.see(
            "end"
        )

    # =========================================================================
    # IMPORT
    # =========================================================================

    def _bg_import(self):

        import_heavy()

        self.q.put(
            ("ready",)
        )

    # =========================================================================
    # START
    # =========================================================================

    def start(
        self,
        files=None,
        requeue=False,
    ):

        if (
            self.worker
            and
            self.worker.is_alive()
        ):

            return

        cfg = self.get_cfg()

        input_dir = Path(
            cfg["input_dir"]
        )

        output_dir = Path(
            cfg["output_dir"]
        )

        if not input_dir.is_dir():

            messagebox.showerror(
                "Input",
                "Input folder does not exist.",
            )

            return

        if not cfg[
            "output_dir"
        ]:

            messagebox.showerror(
                "Output",
                "Choose an output folder.",
            )

            return

        try:

            if (
                input_dir.resolve()
                ==
                output_dir.resolve()
            ):

                messagebox.showerror(
                    "Output",
                    "Output folder must be "
                    "different from input.",
                )

                return

        except OSError:
            pass

        try:

            parse_scale(
                cfg["scale"]
            )

        except Exception:

            messagebox.showerror(
                "Scale",
                (
                    "Invalid output scale.\n"
                    "Use Native, 2x, 4x, 2.5, etc."
                ),
            )

            return

        if (
            cfg["model_path"]
            not in (
                "",
                BUILTIN,
            )
            and
            torch is None
        ):

            messagebox.showerror(
                "PyTorch",
                (
                    "PyTorch is not available:\n"
                    +
                    IMPORT_ERR.get(
                        "torch",
                        "",
                    )
                ),
            )

            return

        if requeue:

            cfg["skip_existing"] = False

        else:

            files = gather_files(
                input_dir,
                output_dir,
                cfg["recursive"],
                cfg["name_filter"],
                cfg["sort_mode"],
            )

        if not files:

            messagebox.showinfo(
                "Files",
                "No images to process.",
            )

            return

        self._save_settings()

        self.stop_evt.clear()

        self.pause_evt.set()

        self.btn_start.config(
            state="disabled"
        )

        self.btn_requeue.config(
            state="disabled"
        )

        self.btn_pause.config(
            state="normal",
            text="\u23F8  Pause",
        )

        self.btn_stop.config(
            state="normal"
        )

        self.pbar["value"] = 0

        self.log(
            f"Starting: {len(files)} image(s)"
        )

        self.worker = threading.Thread(
            target=self._run,
            args=(
                files,
                cfg,
            ),
            daemon=True,
        )

        self.worker.start()

    # =========================================================================
    # REQUEUE
    # =========================================================================

    def requeue(self):

        files = list(
            dict.fromkeys(
                self.skipped
                +
                self.failed
            )
        )

        if files:

            self.start(
                files=files,
                requeue=True,
            )

    # =========================================================================
    # PAUSE
    # =========================================================================

    def toggle_pause(self):

        if self.pause_evt.is_set():

            self.pause_evt.clear()

            self.btn_pause.config(
                text="\u25B6  Resume"
            )

            self.log(
                "Paused. Will hold before the next image.",
                "warn",
            )

        else:

            self.pause_evt.set()

            self.btn_pause.config(
                text="\u23F8  Pause"
            )

            self.log(
                "Resumed."
            )

    # =========================================================================
    # STOP
    # =========================================================================

    def stop(self):

        self.stop_evt.set()

        self.pause_evt.set()

        self.btn_stop.config(
            state="disabled"
        )

        self.log(
            "Stopping after the current image...",
            "warn",
        )

    # =========================================================================
    # CLOSE
    # =========================================================================

    def _on_close(self):

        if (
            self.worker
            and
            self.worker.is_alive()
        ):

            if not messagebox.askyesno(
                "Exit",
                (
                    "A batch is running.\n"
                    "Stop and exit?"
                ),
            ):

                return

            self.stop_evt.set()

            self.pause_evt.set()

        self._save_settings()

        self.destroy()

    # =========================================================================
    # WORKER
    # =========================================================================

    def _run(
        self,
        files,
        cfg,
    ):

        event_queue = self.q

        engine = self.engine

        skipped = []
        failed = []

        total = len(files)

        state = {
            "done": 0,
            "ok": 0,
            "skip": 0,
            "fail": 0,
        }

        lock = threading.Lock()

        recent_times = deque(
            maxlen=12
        )

        total_times = []

        start_time = time.time()

        input_dir = Path(
            cfg["input_dir"]
        )

        output_dir = Path(
            cfg["output_dir"]
        )

        # ---------------------------------------------------------------------
        # Finish
        # ---------------------------------------------------------------------

        def finish(fatal=None, user_stopped=False):

            with lock:

                stats = {
                    "total": total,
                    "ok": state["ok"],
                    "skip": state["skip"],
                    "fail": state["fail"],
                    "elapsed": (
                        time.time()
                        -
                        start_time
                    ),
                    "avg": (
                        sum(
                            total_times
                        )
                        /
                        len(
                            total_times
                        )
                        if total_times
                        else 0.0
                    ),
                    "user_stopped": user_stopped,
                }

            event_queue.put(
                (
                    "done",
                    stats,
                    skipped,
                    failed,
                    fatal,
                )
            )

        # ---------------------------------------------------------------------
        # Model
        # ---------------------------------------------------------------------

        try:

            event_queue.put(
                (
                    "status",
                    "Loading model...",
                )
            )

            engine.load(
                cfg["model_path"],
                cfg["device"],
                cfg["precision"],
                cfg["tile"],
                cfg["overlap"],
            )

            event_queue.put(
                (
                    "model",
                    engine.info,
                )
            )

        except Exception as e:

            event_queue.put(
                (
                    "log",
                    f"Model load failed: {e}",
                    "err",
                )
            )

            event_queue.put(
                (
                    "log",
                    traceback.format_exc(
                        limit=5
                    ),
                    "err",
                )
            )

            finish(
                fatal=str(e)
            )

            return

        # ---------------------------------------------------------------------
        # Output path
        # ---------------------------------------------------------------------

        def output_path(source):

            if cfg["recursive"]:

                relative = (
                    source.relative_to(
                        input_dir
                    )
                )

            else:

                relative = Path(
                    source.name
                )

            if (
                cfg["out_ext"]
                == ".png"
            ):

                relative = (
                    relative.with_suffix(
                        ".png"
                    )
                )

            return (
                output_dir
                /
                relative
            )

        # ---------------------------------------------------------------------
        # Progress
        # ---------------------------------------------------------------------

        def post_progress():

            with lock:

                done = state["done"]

                average = (
                    sum(
                        recent_times
                    )
                    /
                    len(
                        recent_times
                    )
                    if recent_times
                    else 0.0
                )

                remaining = max(
                    0,
                    total - done,
                )

                eta = (
                    average
                    * remaining
                )

                snapshot = dict(
                    state
                )

            event_queue.put(
                (
                    "progress",
                    done,
                    total,
                    time.time()
                    - start_time,
                    eta,
                    average,
                    snapshot,
                )
            )

        # ---------------------------------------------------------------------
        # Existing output
        # ---------------------------------------------------------------------

        todo = []

        for source in files:

            destination = output_path(
                source
            )

            if (
                cfg["skip_existing"]
                and
                destination.exists()
            ):

                skipped.append(
                    source
                )

                state["skip"] += 1
                state["done"] += 1

            else:

                todo.append(
                    (
                        source,
                        destination,
                    )
                )

        if state["skip"]:

            event_queue.put(
                (
                    "log",
                    (
                        f"{state['skip']} "
                        "file(s) already exist "
                        "-> skipped"
                    ),
                    "warn",
                )
            )

        post_progress()

        # ---------------------------------------------------------------------
        # Prefetch
        # ---------------------------------------------------------------------

        prefetch_size = max(
            1,
            min(
                8,
                int(
                    cfg["prefetch"]
                ),
            ),
        )

        load_queue = queue.Queue(
            maxsize=prefetch_size
        )

        loader_done = threading.Event()

        def loader():

            try:

                for source, destination in todo:

                    if self.stop_evt.is_set():
                        break

                    try:

                        image = Image.open(
                            source
                        )

                        image_size = image.size

                        longer_side = max(
                            image_size
                        )

                        # -----------------------------------------------------
                        # Size filters
                        # -----------------------------------------------------

                        if (
                            cfg["skip_over"]
                            > 0
                            and
                            longer_side
                            >=
                            cfg["skip_over"]
                        ):

                            image.close()

                            item = (
                                source,
                                destination,
                                None,
                                "size>=",
                            )

                        elif (
                            cfg["skip_under"]
                            > 0
                            and
                            longer_side
                            <=
                            cfg["skip_under"]
                        ):

                            image.close()

                            item = (
                                source,
                                destination,
                                None,
                                "size<=",
                            )

                        else:

                            # Force decode while loader owns the file.
                            image.load()

                            item = (
                                source,
                                destination,
                                image,
                                None,
                            )

                    except Exception as e:

                        item = (
                            source,
                            destination,
                            None,
                            e,
                        )

                    # ---------------------------------------------------------
                    # Queue
                    # ---------------------------------------------------------

                    while not self.stop_evt.is_set():

                        try:

                            load_queue.put(
                                item,
                                timeout=0.2,
                            )

                            break

                        except queue.Full:
                            pass

            finally:

                # Always terminate consumer cleanly.
                while True:

                    try:

                        load_queue.put(
                            None,
                            timeout=0.2,
                        )

                        break

                    except queue.Full:

                        if self.stop_evt.is_set():

                            # Consumer will eventually drain/break.
                            continue

                loader_done.set()

        loader_thread = threading.Thread(
            target=loader,
            daemon=True,
        )

        loader_thread.start()

        # ---------------------------------------------------------------------
        # Save pool
        # ---------------------------------------------------------------------

        save_workers = max(
            1,
            min(
                4,
                int(
                    cfg["save_threads"]
                ),
            ),
        )

        pool = ThreadPoolExecutor(
            max_workers=save_workers
        )

        # ---------------------------------------------------------------------
        # Save callback
        # ---------------------------------------------------------------------

        def on_saved(
            future,
            source,
            original,
            output_image,
            started,
            info,
        ):

            try:

                future.result()

            except Exception as e:

                with lock:

                    state["fail"] += 1
                    state["done"] += 1

                failed.append(
                    source
                )

                event_queue.put(
                    (
                        "log",
                        (
                            f"\u2717 {source.name}: "
                            f"save failed: {e}"
                        ),
                        "err",
                    )
                )

            else:

                elapsed = (
                    time.time()
                    -
                    started
                )

                with lock:

                    state["ok"] += 1
                    state["done"] += 1

                    recent_times.append(
                        elapsed
                    )

                    total_times.append(
                        elapsed
                    )

                event_queue.put(
                    (
                        "log",
                        (
                            f"\u2713 {source.name} "
                            f"({elapsed:.1f}s) "
                            f"{info}"
                        ),
                        "ok",
                    )
                )

                if cfg[
                    "show_preview"
                ]:
                    # Copy BEFORE original can be closed (avoids closed-image crash).
                    try:
                        orig_preview = original.copy()
                        out_preview = output_image.copy()
                        event_queue.put(
                            (
                                "preview",
                                orig_preview,
                                out_preview,
                                (
                                    "Showing last finished: "
                                    f"{source.name}   "
                                    f"{info}   "
                                    f"({elapsed:.1f}s)"
                                ),
                            )
                        )
                    except Exception:
                        pass

            finally:

                try:
                    original.close()
                except Exception:
                    pass

                post_progress()

        # ---------------------------------------------------------------------
        # Main processing loop
        # ---------------------------------------------------------------------

        fatal_error = None

        try:

            while True:

                # -------------------------------------------------------------
                # Pause
                # -------------------------------------------------------------

                while not self.pause_evt.wait(
                    0.2
                ):

                    if self.stop_evt.is_set():
                        break

                if self.stop_evt.is_set():
                    break

                # -------------------------------------------------------------
                # Fetch
                # -------------------------------------------------------------

                try:

                    item = load_queue.get(
                        timeout=0.2
                    )

                except queue.Empty:

                    continue

                if item is None:
                    break

                source, destination, image, error = item

                # -------------------------------------------------------------
                # Size skip
                # -------------------------------------------------------------

                if isinstance(
                    error,
                    str,
                ):

                    with lock:

                        state["skip"] += 1
                        state["done"] += 1

                    skipped.append(
                        source
                    )

                    message = (
                        f"\u23ED {source.name}: "
                        f"skipped ({error} filter)"
                    )

                    if cfg[
                        "copy_skipped"
                    ]:

                        try:

                            destination.parent.mkdir(
                                parents=True,
                                exist_ok=True,
                            )

                            shutil.copy2(
                                source,
                                destination,
                            )

                            message += (
                                " - copied unchanged"
                            )

                        except Exception as e:

                            # Copy failure is a real failure.
                            with lock:

                                state["skip"] -= 1
                                state["fail"] += 1

                            try:
                                skipped.remove(
                                    source
                                )
                            except ValueError:
                                pass

                            failed.append(
                                source
                            )

                            message += (
                                f" - copy failed: {e}"
                            )

                    event_queue.put(
                        (
                            "log",
                            message,
                            (
                                "err"
                                if
                                state["fail"]
                                and
                                "copy failed"
                                in message
                                else
                                "warn"
                            ),
                        )
                    )

                    post_progress()

                    continue

                # -------------------------------------------------------------
                # Read failure
                # -------------------------------------------------------------

                if isinstance(
                    error,
                    Exception,
                ):

                    with lock:

                        state["fail"] += 1
                        state["done"] += 1

                    failed.append(
                        source
                    )

                    event_queue.put(
                        (
                            "log",
                            (
                                f"\u2717 {source.name}: "
                                f"cannot read: {error}"
                            ),
                            "err",
                        )
                    )

                    post_progress()

                    continue

                # -------------------------------------------------------------
                # Processing
                # -------------------------------------------------------------

                event_queue.put(
                    (
                        "current",
                        (
                            f"Processing: "
                            f"{source.name} "
                            f"({image.size[0]}x"
                            f"{image.size[1]}, "
                            f"{image.mode})"
                        ),
                    )
                )

                started = time.time()

                try:

                    output_image, info = (
                        engine.process(
                            image,
                            cfg,
                        )
                    )

                except Exception as e:

                    with lock:

                        state["fail"] += 1
                        state["done"] += 1

                    failed.append(
                        source
                    )

                    event_queue.put(
                        (
                            "log",
                            (
                                f"\u2717 {source.name}: "
                                f"{type(e).__name__}: {e}"
                            ),
                            "err",
                        )
                    )

                    event_queue.put(
                        (
                            "log",
                            traceback.format_exc(
                                limit=5
                            ),
                            "err",
                        )
                    )

                    try:
                        image.close()
                    except Exception:
                        pass

                    post_progress()

                    continue

                # -------------------------------------------------------------
                # Save
                # -------------------------------------------------------------

                future = pool.submit(
                    save_image,
                    output_image,
                    destination,
                    cfg,
                )

                future.add_done_callback(
                    partial(
                        on_saved,
                        source=source,
                        original=image,
                        output_image=output_image,
                        started=started,
                        info=info,
                    )
                )

                # -------------------------------------------------------------
                # VRAM cleanup
                # -------------------------------------------------------------

                if (
                    cfg["free_vram"]
                    and
                    torch is not None
                    and
                    torch.cuda.is_available()
                    and
                    not engine.builtin
                ):

                    try:

                        torch.cuda.empty_cache()

                    except Exception:
                        pass

        except Exception as e:

            fatal_error = str(e)

            event_queue.put(
                (
                    "log",
                    f"Worker crashed: {e}",
                    "err",
                )
            )

            event_queue.put(
                (
                    "log",
                    traceback.format_exc(
                        limit=6
                    ),
                    "err",
                )
            )

        finally:

            user_stopped = self.stop_evt.is_set()

            # Stop loader from opening more files.
            self.stop_evt.set()

            # Allow currently queued saves to finish.
            pool.shutdown(
                wait=True
            )

            if fatal_error is not None:

                event_queue.put(
                    (
                        "log",
                        "Worker terminated because of an error.",
                        "err",
                    )
                )

            elif user_stopped:

                event_queue.put(
                    (
                        "log",
                        "Stopped by user.",
                        "warn",
                    )
                )

            finish(
                fatal=fatal_error,
                user_stopped=user_stopped,
            )

    # =========================================================================
    # EVENT PUMP
    # =========================================================================

    def _poll(self):

        latest_preview = None

        try:

            for _ in range(300):

                event = self.q.get_nowait()

                kind = event[0]

                if kind == "log":

                    self.log(
                        event[1],
                        event[2]
                        if len(event) > 2
                        else None,
                    )

                elif kind == "ready":

                    self._on_ready()

                elif kind == "model":

                    self.lbl_model.config(
                        text=event[1]
                    )

                elif kind == "status":

                    self.lbl_cur.config(
                        text=event[1]
                    )

                elif kind == "current":

                    self.lbl_cur.config(
                        text=event[1]
                    )

                elif kind == "progress":

                    (
                        _,
                        done,
                        total,
                        elapsed,
                        eta,
                        average,
                        snapshot,
                    ) = event

                    self.pbar[
                        "maximum"
                    ] = max(
                        1,
                        total,
                    )

                    self.pbar[
                        "value"
                    ] = done

                    percent = (
                        100.0
                        * done
                        /
                        max(
                            1,
                            total,
                        )
                    )

                    self.lbl_prog.config(
                        text=(
                            f"{done}/{total} "
                            f"({percent:.1f}%) | "
                            f"ok {snapshot['ok']} "
                            f"skip {snapshot['skip']} "
                            f"fail {snapshot['fail']} | "
                            f"{fmt_time(elapsed)} elapsed | "
                            f"ETA {fmt_time(eta)} | "
                            f"{average:.1f}s/img"
                        )
                    )

                elif kind == "preview":

                    latest_preview = event

                elif kind == "done":

                    self._on_done(
                        *event[1:]
                    )

        except queue.Empty:
            pass

        if latest_preview is not None:

            (
                _,
                original,
                upscaled,
                caption,
            ) = latest_preview

            # Worker already sent independent copies.
            self.last_pair = (original, upscaled)

            self.lbl_prev.config(
                text=caption
            )

            try:
                self._render_previews()
            except Exception as e:
                self.log(f"Preview render failed: {e}", "err")

        self.after(
            50,
            self._poll,
        )

    # =========================================================================
    # READY
    # =========================================================================

    def _on_ready(self):

        self.ready = True

        devices = [
            "auto",
            "cpu",
        ]

        if torch is not None:

            try:

                for index in range(
                    torch.cuda.device_count()
                ):

                    devices.append(
                        f"cuda:{index}"
                    )

                    self.log(
                        f"GPU {index}: "
                        f"{torch.cuda.get_device_name(index)}"
                    )

            except Exception:
                pass

            self.log(
                f"PyTorch {torch.__version__} | "
                f"CUDA available: "
                f"{torch.cuda.is_available()}"
            )

            # -----------------------------------------------------------------
            # CUDA build check
            # -----------------------------------------------------------------

            if torch.version.cuda is None:

                self.log(
                    (
                        "CPU-only PyTorch detected.\n"
                        "For GTX 1060/Pascal use a CUDA 12.6 "
                        "PyTorch build."
                    ),
                    "err",
                )

            elif not torch.cuda.is_available():

                self.log(
                    (
                        "CUDA build installed but no GPU "
                        "is available. Check NVIDIA driver."
                    ),
                    "err",
                )

            else:

                try:

                    capability = (
                        torch.cuda.get_device_capability(
                            0
                        )
                    )

                    major, minor = capability

                    self.log(
                        f"GPU compute capability: "
                        f"sm_{major}{minor}"
                    )

                    if major == 6:

                        self.log(
                            (
                                "Pascal GPU detected. "
                                "FP32 is recommended."
                            )
                        )

                    # ---------------------------------------------------------
                    # Check compiled architecture
                    # ---------------------------------------------------------

                    arch_list = []

                    try:

                        arch_list = (
                            torch.cuda.get_arch_list()
                        )

                    except Exception:
                        pass

                    if arch_list:

                        supported = False

                        target_sm = (
                            f"sm_{major}{minor}"
                        )

                        for arch in arch_list:

                            if arch == target_sm:
                                supported = True
                                break

                            if arch.startswith(
                                "compute_"
                            ):

                                try:

                                    value = arch.replace(
                                        "compute_",
                                        "",
                                    )

                                    a = int(
                                        value[0]
                                    )

                                    b = int(
                                        value[1:]
                                    )

                                    if (
                                        (a, b)
                                        <=
                                        (major, minor)
                                    ):

                                        supported = True

                                except Exception:
                                    pass

                        if not supported:

                            self.log(
                                (
                                    "WARNING: this PyTorch "
                                    "build does not report a "
                                    f"compatible kernel for "
                                    f"sm_{major}{minor}."
                                ),
                                "err",
                            )

                except Exception:
                    pass

        else:

            self.log(
                (
                    "PyTorch not found. "
                    "Only Lanczos test mode is available."
                ),
                "err",
            )

        if "spandrel" in IMPORT_ERR:

            self.log(
                (
                    "Spandrel not found: "
                    "pip install spandrel"
                ),
                "err",
            )

        self.cb_dev[
            "values"
        ] = devices

        try:

            self.cb_dev2[
                "values"
            ] = devices

        except Exception:
            pass

        self.btn_start.config(
            state="normal"
        )

        self.lbl_cur.config(
            text="Ready."
        )

    # =========================================================================
    # DONE
    # =========================================================================

    def _on_done(
        self,
        stats,
        skipped,
        failed,
        fatal,
    ):

        self.last_stats = stats

        self.skipped = list(
            skipped
        )

        self.failed = list(
            failed
        )

        self.btn_start.config(
            state="normal"
        )

        self.btn_pause.config(
            state="disabled",
            text="\u23F8  Pause",
        )

        self.btn_stop.config(
            state="disabled"
        )

        self.btn_requeue.config(
            state=(
                "normal"
                if (
                    self.skipped
                    or
                    self.failed
                )
                else
                "disabled"
            )
        )

        self.lbl_cur.config(
            text=(
                "Finished."
                if not fatal
                else
                "Failed: "
                + str(fatal)
            )
        )

        self.log(
            (
                f"Done: {stats['ok']} upscaled, "
                f"{stats['skip']} skipped, "
                f"{stats['fail']} failed, "
                f"{fmt_time(stats['elapsed'])} total, "
                f"{stats['avg']:.2f}s avg"
            ),
            "ok"
            if not fatal
            else "err",
        )

        cfg = self.get_cfg()

        user_stopped = bool(
            stats.get("user_stopped", False)
        )

        # Do not automatically open after a user stop.
        if (
            cfg["open_when_done"]
            and
            not fatal
            and
            not user_stopped
        ):

            self.open_output()

        self.worker = None

    # =========================================================================
    # PREVIEW
    # =========================================================================

    def _on_canvas_resize(
        self,
        _event=None,
    ):

        if self._resize_job:

            try:

                self.after_cancel(
                    self._resize_job
                )

            except Exception:
                pass

        self._resize_job = (
            self.after(
                120,
                self._render_previews,
            )
        )

    def _render_previews(self):

        self._resize_job = None

        if not self.last_pair:
            return

        pairs = (
            (
                self.cv_o,
                self.last_pair[0],
            ),
            (
                self.cv_u,
                self.last_pair[1],
            ),
        )

        for index, (
            canvas,
            image,
        ) in enumerate(pairs):

            canvas_width = (
                canvas.winfo_width()
            )

            canvas_height = (
                canvas.winfo_height()
            )

            if (
                canvas_width < 20
                or
                canvas_height < 20
            ):

                continue

            try:
                image.load()
                width, height = image.size
            except Exception:
                continue

            scale = min(
                (
                    canvas_width - 4
                )
                /
                width,

                (
                    canvas_height - 4
                )
                /
                height,
            )

            draw_width = max(
                1,
                int(
                    width * scale
                ),
            )

            draw_height = max(
                1,
                int(
                    height * scale
                ),
            )

            has_alpha = (
                image.mode
                in (
                    "RGBA",
                    "LA",
                    "La",
                    "P",
                )
                and
                (
                    "A"
                    in
                    image.getbands()
                    or
                    "transparency"
                    in
                    image.info
                )
            )

            preview = (
                image.convert(
                    "RGBA"
                ).resize(
                    (
                        draw_width,
                        draw_height,
                    ),
                    (
                        RS.NEAREST
                        if scale >= 1
                        else RS.LANCZOS
                    ),
                )
            )

            if has_alpha:

                if self.v[
                    "checker"
                ].get():

                    yy, xx = np.indices(
                        (
                            draw_height,
                            draw_width,
                        )
                    )

                    mask = (
                        (
                            yy // 8
                            +
                            xx // 8
                        )
                        % 2
                        == 0
                    )[
                        ...,
                        None,
                    ]

                    checker = np.where(
                        mask,
                        np.uint8(205),
                        np.uint8(150),
                    )

                    background = np.repeat(
                        checker,
                        3,
                        axis=2,
                    )

                    background = np.dstack(
                        [
                            background,
                            np.full(
                                (
                                    draw_height,
                                    draw_width,
                                    1,
                                ),
                                255,
                                np.uint8,
                            ),
                        ]
                    )

                    base = Image.fromarray(
                        background
                    )

                else:

                    base = Image.new(
                        "RGBA",
                        (
                            draw_width,
                            draw_height,
                        ),
                        (
                            28,
                            28,
                            28,
                            255,
                        ),
                    )

                preview = (
                    Image.alpha_composite(
                        base,
                        preview,
                    )
                )

            preview = preview.convert(
                "RGB"
            )

            photo = ImageTk.PhotoImage(
                preview
            )

            self._photos[index] = photo

            canvas.delete(
                "all"
            )

            canvas.create_image(
                canvas_width // 2,
                canvas_height // 2,
                image=photo,
            )

    # =========================================================================
    # MONITOR
    # =========================================================================

    def _monitor(self):

        parts = []

        try:

            if (
                torch is not None
                and
                self.v[
                    "device"
                ].get()
                !=
                "cpu"
                and
                torch.cuda.is_available()
            ):

                free, total = (
                    torch.cuda.mem_get_info()
                )

                temperature = (
                    self._gpu_temp()
                )

                if temperature is not None:

                    parts.append(
                        (
                            f"GPU: "
                            f"{temperature}\u00B0C"
                        )
                    )

                used = (
                    total - free
                ) // 2**20

                total_mb = (
                    total // 2**20
                )

                parts.append(
                    (
                        f"VRAM: "
                        f"{used}/{total_mb} MB"
                    )
                )

        except Exception:
            pass

        try:

            import psutil

            parts.append(
                (
                    f"RAM: "
                    f"{psutil.virtual_memory().percent:.0f}%"
                )
            )

        except Exception:
            pass

        self.lbl_mon.config(
            text=" | ".join(parts)
        )

        self.after(
            1000,
            self._monitor,
        )

    # =========================================================================
    # GPU TEMPERATURE
    # =========================================================================

    def _gpu_temp(self):

        if self._nvml is False:
            return None

        try:

            import pynvml

            if self._nvml is None:

                pynvml.nvmlInit()

                device = (
                    self.v[
                        "device"
                    ].get()
                )

                if ":" in device:

                    index = int(
                        device.split(":")[1]
                    )

                else:

                    index = 0

                self._nvml = (
                    pynvml,
                    pynvml.nvmlDeviceGetHandleByIndex(
                        index
                    ),
                )

            module, handle = (
                self._nvml
            )

            return (
                module.nvmlDeviceGetTemperature(
                    handle,
                    module.NVML_TEMPERATURE_GPU,
                )
            )

        except Exception:

            self._nvml = False

            return None


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":

    app = App()

    app.mainloop()

