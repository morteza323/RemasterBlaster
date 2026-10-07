"""
stable-diffusion.cpp backend – v3.5
- Exact output filename
- Atomic write
- Dynamic width/height (aspect preserved)
- Filename-aware dynamic prompt (locks material type, blocks faces/animals/green tint)
- Alpha / transparency preservation
- Optional RealESRGAN / ESRGAN post-upscale via --upscale-model
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from ..core.config import AppConfig
from ..core.logger import get_logger
from ..utils.validation import run_full_validation
from ..utils.material_hint import build_dynamic_prompt, infer_material
from ..utils.fidelity import fuse_texture, enforce_source_color_fidelity
from .base import IInferenceBackend

logger = get_logger("backend.sdcpp")


def compute_target_size(
    src_w: int,
    src_h: int,
    target_long: int = 512,
    max_side: int = 1024,
    min_side: int = 64,
    multiple: int = 8,
) -> Tuple[int, int]:
    if src_w <= 0 or src_h <= 0:
        return 512, 512

    ratio = max(src_w, src_h) / max(1, min(src_w, src_h))
    is_square = ratio < 1.02
    long_side = max(src_w, src_h)

    if is_square:
        side = target_long
        if long_side > target_long:
            side = min(long_side, max_side)
            side = max(multiple, int(round(side / multiple) * multiple))
        return side, side

    if long_side >= target_long:
        scale = min(1.0, float(max_side) / float(long_side))
    else:
        scale = float(target_long) / float(long_side)

    new_w = src_w * scale
    new_h = src_h * scale

    if max(new_w, new_h) > max_side:
        scale = float(max_side) / float(long_side)
        new_w = src_w * scale
        new_h = src_h * scale

    def _round(v: float) -> int:
        r = int(round(v / multiple) * multiple)
        return max(multiple, r)

    new_w = _round(new_w)
    new_h = _round(new_h)

    if max(new_w, new_h) < min_side:
        scale2 = float(min_side) / float(long_side)
        new_w = _round(src_w * scale2)
        new_h = _round(src_h * scale2)

    return new_w, new_h


def get_image_size(path: Path) -> Tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return 0, 0


def extract_alpha(path: Path) -> Optional["Image.Image"]:
    """Return alpha channel as L image, or None if opaque / no alpha."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                alpha = rgba.split()[-1]
                extrema = alpha.getextrema()
                if extrema[0] < 255:
                    return alpha.copy()
    except Exception:
        pass
    return None


def apply_alpha(rgb_path: Path, alpha_img, out_w: int, out_h: int, compress_level: int = 6) -> None:
    """
    Paste the ORIGINAL alpha (upscaled with NEAREST) onto the generated RGB.
    Where alpha is fully transparent, force RGB to black so edges do not look
    like a Photoshop cut-out of invented background.
    """
    try:
        from PIL import Image
        import numpy as np
        with Image.open(rgb_path) as im:
            rgb = im.convert("RGB")
            actual_w, actual_h = rgb.size
            tw, th = (actual_w, actual_h) if actual_w > 0 and actual_h > 0 else (out_w, out_h)
            if rgb.size != (tw, th):
                rgb = rgb.resize((tw, th), Image.Resampling.LANCZOS)
            # NEAREST keeps hard game-texture silhouettes (same as classic pipeline)
            a = alpha_img.resize((tw, th), Image.Resampling.NEAREST)
            arr = np.asarray(rgb, dtype=np.uint8).copy()
            aa = np.asarray(a, dtype=np.uint8)
            # fully transparent → pure black RGB (no fringe / invented plate)
            transparent = aa < 8
            if transparent.any():
                arr[transparent] = (0, 0, 0)
            out_im = Image.fromarray(arr, mode="RGB")
            out_im.putalpha(Image.fromarray(aa, mode="L"))
            out = rgb_path if rgb_path.suffix.lower() == ".png" else rgb_path.with_suffix(".png")
            out_im.save(out, "PNG", compress_level=max(0, min(9, int(compress_level))), optimize=False)
            if rgb_path.suffix.lower() != ".png" and out != rgb_path and rgb_path.exists():
                rgb_path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Alpha restore failed: {e}")




def restore_flat_background(src_path: Path, out_path: Path) -> None:
    """
    ONLY for true solid plate backgrounds (neon/logo on pure black or pure white).

    Strict rules so dark teal / dark metal vehicle interiors are NEVER forced to #000:
      - pure black pixel: ALL channels < 10 AND chroma < 6
      - pure white pixel: ALL channels > 248 AND chroma < 8
      - must cover >= 30% of image
      - non-background region must have real content (mean > 40 or sat content)
    Dark materials (dashboards, car paint, interiors) fail these checks and are left alone.
    """
    from PIL import Image
    import numpy as np
    src_path = Path(src_path)
    out_path = Path(out_path)
    if not src_path.is_file() or not out_path.is_file():
        return
    try:
        with Image.open(src_path) as s_im, Image.open(out_path) as o_im:
            o_has_alpha = o_im.mode in ("RGBA", "LA") or ("transparency" in o_im.info)
            o_alpha = None
            if o_has_alpha:
                o_rgba = o_im.convert("RGBA")
                o_alpha = o_rgba.split()[-1]
                o_rgb = Image.merge("RGB", o_rgba.split()[:3])
            else:
                o_rgb = o_im.convert("RGB")

            s_rgb = s_im.convert("RGB").resize(o_rgb.size, Image.Resampling.NEAREST)
            s = np.asarray(s_rgb, dtype=np.float32)
            o = np.asarray(o_rgb, dtype=np.float32).copy()
            s_mx = s.max(axis=2)
            s_mn = s.min(axis=2)
            chroma = s_mx - s_mn

            # STRICT pure black / pure white only (not dark teal, not charcoal metal)
            black_m = (s_mx < 10.0) & (chroma < 6.0)
            white_m = (s_mn > 248.0) & (chroma < 8.0)
            black_r = float(black_m.mean())
            white_r = float(white_m.mean())

            def _content_ok(mask) -> bool:
                # remaining pixels should not be almost-empty (avoid wiping whole dark textures)
                inv = ~mask
                if inv.mean() < 0.02:
                    return False
                rest = s[inv]
                # real subject: either brighter or chromatic
                return float(rest.max(axis=1).mean()) > 35.0 or float((rest.max(axis=1) - rest.min(axis=1)).mean()) > 12.0

            changed = False
            if black_r >= 0.30 and _content_ok(black_m):
                o[black_m] = (0.0, 0.0, 0.0)
                changed = True
            if white_r >= 0.30 and _content_ok(white_m):
                o[white_m] = (255.0, 255.0, 255.0)
                changed = True
            if not changed:
                return
            out = Image.fromarray(np.clip(o, 0, 255).astype(np.uint8), mode="RGB")
            if o_alpha is not None:
                out = out.convert("RGBA")
                out.putalpha(o_alpha)
            out.save(out_path, "PNG", compress_level=3, optimize=False)
    except Exception as e:
        logger.warning(f"flat background restore skipped on {out_path.name}: {e}")


def apply_final_sauce(
    path: Path,
    sharpness: float = 1.03,
    saturation: float = 0.97,
    contrast: float = 1.02,
) -> None:
    """
    Light post-process on Flux output only (layout/resolution/alpha unchanged):
      - +3% sharpness
      - -3% saturation
      - +2% contrast
    """
    from PIL import Image, ImageEnhance
    path = Path(path)
    if not path.is_file():
        return
    try:
        with Image.open(path) as im:
            has_alpha = im.mode in ("RGBA", "LA") or ("transparency" in im.info)
            alpha = None
            if has_alpha:
                rgba = im.convert("RGBA")
                alpha = rgba.split()[-1]
                rgb = Image.merge("RGB", rgba.split()[:3])
            else:
                rgb = im.convert("RGB")
            rgb = ImageEnhance.Sharpness(rgb).enhance(float(sharpness))
            rgb = ImageEnhance.Contrast(rgb).enhance(float(contrast))
            rgb = ImageEnhance.Color(rgb).enhance(float(saturation))
            if alpha is not None:
                out = rgb.convert("RGBA")
                out.putalpha(alpha)
            else:
                out = rgb
            out.save(path, "PNG", compress_level=3, optimize=False)
    except Exception as e:
        logger.warning(f"final sauce skipped on {path.name}: {e}")


def prepare_flux_init_image(input_path: Path, out_path: Path, width: int, height: int, filter_name: str = "LANCZOS") -> Path:
    """V6: feed Flux a clean, high-resolution reconstruction canvas instead of raw tiny pixels.

    This is deliberately a conventional resize only; it does not invent content. Flux then
    adds detail on top of this canvas at a controlled img2img strength.
    """
    from PIL import Image
    im = Image.open(input_path)
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if has_alpha:
        rgba = im.convert("RGBA")
        # Keep the RGB information underneath transparency; alpha is restored separately later.
        rgb = Image.merge("RGB", rgba.split()[:3])
    else:
        rgb = im.convert("RGB")
    filt = getattr(Image.Resampling, str(filter_name).upper(), Image.Resampling.LANCZOS)
    if rgb.size != (width, height):
        rgb = rgb.resize((width, height), filt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rgb.save(out_path, "PNG", compress_level=1, optimize=False)
    return out_path


def apply_v6_detail_finish(path: Path, sharpness: float = 1.10, radius: float = 0.7) -> None:
    """Small detail finish after Flux; never changes geometry or alpha."""
    from PIL import Image, ImageEnhance, ImageFilter
    try:
        with Image.open(path) as im:
            has_alpha = im.mode in ("RGBA", "LA") or ("transparency" in im.info)
            alpha = im.convert("RGBA").split()[-1] if has_alpha else None
            rgb = im.convert("RGB")
            # Unsharp mask is safer than aggressive global sharpening for game textures.
            r = max(0.1, float(radius))
            rgb = rgb.filter(ImageFilter.UnsharpMask(radius=r, percent=max(0, int((float(sharpness)-1.0)*1000)), threshold=2))
            if alpha is not None:
                out = rgb.convert("RGBA"); out.putalpha(alpha)
            else:
                out = rgb
            out.save(path, "PNG", compress_level=3, optimize=False)
    except Exception as e:
        logger.warning(f"V6 detail finish skipped on {path.name}: {e}")


class SDCppBackend(IInferenceBackend):
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._ready = False
        self._embedding_cached = False
        self._cached_prompt: str = ""
        self._sd_cli: Optional[Path] = None
        self._upscale_model: Optional[Path] = None

    def initialize(self) -> None:
        logger.info("Initializing SDCppBackend...")
        result = run_full_validation(self.cfg)
        if not result.ok:
            raise RuntimeError(
                "Pre-flight validation failed:\n  - " + "\n  - ".join(result.errors)
            )

        cli = self.cfg.models.sd_cli
        p = Path(cli)
        if p.is_file():
            self._sd_cli = p
        else:
            found = shutil.which(cli)
            if found:
                self._sd_cli = Path(found)
            else:
                raise RuntimeError(f"sd-cli not found: {cli}")

        # Optional RealESRGAN / ESRGAN
        up_path = getattr(self.cfg.models, "upscale_model", "") or ""
        if up_path.strip():
            up = Path(up_path)
            if up.is_file():
                self._upscale_model = up
                logger.info(f"RealESRGAN/ESRGAN enabled: {up}")
            else:
                logger.warning(f"upscale_model not found (skipping ESRGAN): {up_path}")
                self._upscale_model = None
        else:
            self._upscale_model = None

        self._ready = True
        logger.info(f"Backend ready. sd-cli = {self._sd_cli}")

    def shutdown(self) -> None:
        logger.info("SDCppBackend.shutdown()")
        self._ready = False

    def is_ready(self) -> bool:
        return self._ready

    def encode_prompt_once(self, prompt: str, cache_path: Path) -> None:
        """Cache the MASTER prompt only (dynamic part is added per-image)."""
        logger.info("Caching master prompt...")
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Master prompt is empty")

        cache_path = Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "version": 8,
            "prompt": prompt,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "created_for": "FLUX.2-klein-4B",
        }
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._cached_prompt = prompt
        self._embedding_cached = True
        logger.info(f"Prompt cached → {cache_path}")

    def _load_cached_prompt(self) -> str:
        if self._cached_prompt:
            return self._cached_prompt
        cache_path = self.cfg.resolve_path(self.cfg.prompt.embedding_cache)
        if not cache_path.exists():
            raise RuntimeError(
                f"Prompt cache missing: {cache_path}. Run: python main.py --encode-prompt"
            )
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        self._cached_prompt = data["prompt"]
        self._embedding_cached = True
        return self._cached_prompt

    def process_image(
        self,
        input_path: Path,
        output_path: Path,
        seed: int,
        strength: Optional[float] = None,
    ) -> None:
        if not self._ready or self._sd_cli is None:
            raise RuntimeError("Backend not initialized.")

        input_path = Path(input_path)
        output_path = Path(output_path)
        if not input_path.is_file():
            raise FileNotFoundError(f"Input texture not found: {input_path}")

        strength = strength if strength is not None else self.cfg.inference.strength
        master = self._load_cached_prompt()

        # Dynamic per-file prompt (locks material; image pixels override bad filename hints)
        prompt = build_dynamic_prompt(master, input_path.name, image_path=input_path, allow_filename_hints=getattr(self.cfg.inference, "filename_material_hints", False), cfg=self.cfg)
        label, _ = infer_material(input_path.name, image_path=input_path, allow_filename_hints=False)

        if self.cfg.inference.force_png_extension:
            output_path = output_path.with_suffix(".png")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Capture alpha before generation
        alpha = extract_alpha(input_path)

        tmp_out = output_path.parent / f".{output_path.stem}.tmp.png"
        if tmp_out.exists():
            tmp_out.unlink()

        src_w, src_h = get_image_size(input_path)
        inf = self.cfg.inference
        if src_w > 0 and src_h > 0:
            out_w, out_h = compute_target_size(
                src_w, src_h,
                target_long=getattr(inf, "target_long_side", 512),
                max_side=getattr(inf, "max_side", 1024),
                min_side=getattr(inf, "min_side", 64),
                multiple=getattr(inf, "size_multiple", 8),
            )
        else:
            out_w, out_h = inf.width, inf.height
            logger.warning(f"Could not read size of {input_path.name}, fallback {out_w}x{out_h}")

        # V6: do not feed tiny/blocky source pixels directly to Flux.
        # First create a clean 512-ish canvas, then let Flux reconstruct detail on it.
        flux_input = input_path
        flux_init = output_path.parent / f".{output_path.stem}.flux_init.png"
        if bool(getattr(inf, "pre_upscale_source", True)):
            try:
                prepare_flux_init_image(
                    input_path, flux_init, out_w, out_h,
                    getattr(inf, "pre_upscale_filter", "LANCZOS")
                )
                flux_input = flux_init
            except Exception as e:
                logger.warning(f"V6 pre-upscale failed; using original input: {e}")

        # If ESRGAN x4 is active, final size will be ~4x the base size
        scale_note = ""
        if self._upscale_model is not None:
            scale_note = f" + RealESRGAN x4 → ~{out_w*4}x{out_h*4}"

        cmd = self._build_command(
            input_path=flux_input,
            output_path=tmp_out,
            prompt=prompt,
            seed=seed,
            strength=strength,
            width=out_w,
            height=out_h,
        )

        logger.info(
            f"sd-cli | seed={seed} strength={strength} | "
            f"{input_path.name} ({src_w}x{src_h} → {out_w}x{out_h}{scale_note}) [{label}] → {output_path.name}"
        )
        logger.debug("PROMPT: " + prompt[:220] + "...")
        logger.debug(f"Flux init image: {flux_input}")
        logger.debug("CMD: " + " ".join(str(c) for c in cmd))

        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60 * 45,  # ESRGAN can take longer
            )
        except subprocess.TimeoutExpired as e:
            tmp_out.unlink(missing_ok=True)
            flux_init.unlink(missing_ok=True)
            raise RuntimeError(f"sd-cli timed out (45min) on {input_path.name}") from e
        except FileNotFoundError as e:
            raise RuntimeError(f"Failed to execute sd-cli: {self._sd_cli}") from e

        elapsed = time.perf_counter() - t0

        if proc.returncode != 0:
            tmp_out.unlink(missing_ok=True)
            flux_init.unlink(missing_ok=True)
            stderr = (proc.stderr or "")[-2000:]
            stdout = (proc.stdout or "")[-1000:]
            raise RuntimeError(
                f"sd-cli failed (code {proc.returncode}) on {input_path.name}\n"
                f"stderr: {stderr}\nstdout: {stdout}"
            )

        if not tmp_out.exists():
            flux_init.unlink(missing_ok=True)
            raise RuntimeError(
                f"sd-cli success but output missing: {tmp_out}\n"
                f"stdout: {(proc.stdout or '')[-1500:]}"
            )

        # Remove the temporary pre-upscaled init; original source remains untouched.
        if flux_init.exists():
            flux_init.unlink(missing_ok=True)

        # Final size after possible ESRGAN
        final_w, final_h = get_image_size(tmp_out)
        if final_w <= 0:
            final_w, final_h = out_w, out_h
            if self._upscale_model is not None:
                final_w, final_h = out_w * 4, out_h * 4

        # Restore transparency if input had alpha (to final resolution)
        compress = int(getattr(self.cfg.inference, "png_compress_level", 6) or 6)
        if alpha is not None:
            apply_alpha(tmp_out, alpha, final_w, final_h, compress_level=compress)
        else:
            # Re-save with compress to keep file size reasonable
            try:
                from PIL import Image
                with Image.open(tmp_out) as im:
                    im.save(tmp_out, "PNG", compress_level=max(0, min(9, compress)), optimize=True)
            except Exception as e:
                logger.debug(f"PNG recompress skipped: {e}")
            if output_path.suffix.lower() != ".png":
                output_path = output_path.with_suffix(".png")

        # V5.2: source fusion is OFF by default for normal textures. The previous
        # source-anchored blend was suppressing the actual Flux reconstruction and
        # making results look like a lightly sharpened/pastel resize. If explicitly
        # enabled, apply it here; otherwise keep the genuine Flux output untouched.
        # V5.5 color guard: keep source hue/chroma so Flux cannot turn red/green/blue assets white/gray.
        if getattr(self.cfg.inference, "preserve_source_colors", True):
            enforce_source_color_fidelity(input_path, tmp_out, self.cfg)

        if getattr(self.cfg.inference, "fidelity_fusion", False):
            try:
                fuse_texture(input_path, tmp_out, self.cfg)
            except Exception as e:
                logger.warning(f"Fidelity fusion failed (keeping raw Flux output): {e}")

        # V6: controlled micro-detail finish. This is intentionally mild.
        apply_v6_detail_finish(
            tmp_out,
            sharpness=float(getattr(self.cfg.inference, "post_sharpen", 1.10)),
            radius=float(getattr(self.cfg.inference, "post_detail_radius", 0.7)),
        )

        # Atomic move
        final_tmp = output_path.parent / f".{output_path.name}.atomic"
        try:
            if final_tmp.exists():
                final_tmp.unlink()
            shutil.move(str(tmp_out), str(final_tmp))
            final_tmp.replace(output_path)
        except Exception:
            final_tmp.unlink(missing_ok=True)
            tmp_out.unlink(missing_ok=True)
            raise

        logger.info(f"  OK {elapsed:.1f}s → {output_path} ({final_w}x{final_h})")

    def _build_command(
        self,
        input_path: Path,
        output_path: Path,
        prompt: str,
        seed: int,
        strength: float,
        width: int,
        height: int,
    ) -> List[str]:
        cfg = self.cfg
        m = cfg.models
        inf = cfg.inference

        cmd: List[str] = [
            str(self._sd_cli),
            "--diffusion-model", str(cfg.resolve_path(m.diffusion_gguf)),
            "--vae", str(cfg.resolve_path(m.vae)),
            "--llm", str(cfg.resolve_path(m.text_encoder)),
            "-r", str(input_path),
            "-p", prompt,
            "-o", str(output_path),
            "--cfg-scale", str(inf.cfg_scale),
            "--steps", str(inf.steps),
            "--sampling-method", inf.sampling_method,
            "--seed", str(seed),
            "--width", str(width),
            "--height", str(height),
            "--strength", str(strength),
        ]
        if inf.diffusion_fa:
            cmd.append("--diffusion-fa")
        if inf.offload_to_cpu:
            cmd.append("--offload-to-cpu")

        # RealESRGAN / ESRGAN post-upscale (x4)
        if self._upscale_model is not None:
            cmd.extend(["--upscale-model", str(self._upscale_model)])
            repeats = getattr(inf, "upscale_repeats", 1) or 1
            tile = getattr(inf, "upscale_tile_size", 128) or 128
            cmd.extend(["--upscale-repeats", str(repeats)])
            cmd.extend(["--upscale-tile-size", str(tile)])

        return cmd
