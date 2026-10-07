"""V5 source-anchored fidelity fusion for GTA texture remastering.

The diffusion model is allowed to reconstruct realistic detail, but the final
image is anchored to the original texture's geometry and low-frequency color.
This prevents the common Flux failure mode where the material looks realistic
but the palette, broad shapes, or UV islands drift.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional


def _split_alpha(im):
    from PIL import Image
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        return rgba.convert("RGB"), rgba.getchannel("A")
    return im.convert("RGB"), None


def _clamp_u8(arr):
    import numpy as np
    return np.clip(arr, 0.0, 255.0).astype(np.uint8)


def _match_luminance_and_chroma(src, gen, strength=0.90):
    """Gently transfer source color statistics to generated pixels.

    Done in YCbCr-like channels rather than per RGB channel so hue is not
    independently warped. The source remains the anchor; generated detail is
    retained.
    """
    import numpy as np
    src = src.astype(np.float32)
    gen = gen.astype(np.float32)
    # Y, Cb, Cr transforms (BT.601; sufficient for a conservative game texture pass)
    def ycc(x):
        r,g,b=x[...,0],x[...,1],x[...,2]
        y=0.299*r+0.587*g+0.114*b
        cb=-0.168736*r-0.331264*g+0.5*b+128.0
        cr=0.5*r-0.418688*g-0.081312*b+128.0
        return np.stack([y,cb,cr],axis=-1)
    def rgb(x):
        y,cb,cr=x[...,0],x[...,1]-128.0,x[...,2]-128.0
        r=y+1.402*cr
        g=y-0.344136*cb-0.714136*cr
        b=y+1.772*cb
        return np.stack([r,g,b],axis=-1)
    s=ycc(src); g=ycc(gen)
    # Match global mean/std only; never remap individual pixels to source colors.
    for c in range(3):
        sm=float(s[...,c].mean()); ss=float(s[...,c].std())
        gm=float(g[...,c].mean()); gs=float(g[...,c].std())
        if gs > 1e-4:
            mapped=(g[...,c]-gm)*(ss/max(gs,1e-4))+sm
        else:
            mapped=g[...,c]
        g[...,c]=g[...,c]*(1.0-strength)+mapped*strength
    return rgb(g)


def fuse_texture(source_path: Path, generated_path: Path, cfg) -> None:
    """Fuse generated detail into a source-anchored 512px texture.

    Low frequency = original (composition/palette/large shapes).
    Mid/high frequency = generated reconstruction with controlled source
    anchoring, so the result visibly benefits from the AI pass instead of
    looking like a conventional resize.
    """
    if not getattr(cfg.inference, "fidelity_fusion", True):
        return
    from PIL import Image, ImageFilter
    import numpy as np
    source_path=Path(source_path); generated_path=Path(generated_path)
    if not source_path.is_file() or not generated_path.is_file():
        return
    try:
        with Image.open(source_path) as si, Image.open(generated_path) as gi:
            src_rgb, src_a=_split_alpha(si)
            gen_rgb, gen_a=_split_alpha(gi)
            size=gen_rgb.size
            src_rgb=src_rgb.resize(size, Image.Resampling.LANCZOS)
            # Correct global color drift before extracting detail.
            gen_rgb=Image.fromarray(_clamp_u8(_match_luminance_and_chroma(
                np.asarray(src_rgb), np.asarray(gen_rgb),
                float(getattr(cfg.inference,'fidelity_color_strength',0.90))
            )), 'RGB')
            src=np.asarray(src_rgb,dtype=np.float32)
            gen=np.asarray(gen_rgb,dtype=np.float32)
            bs=float(getattr(cfg.inference,'fidelity_blur_small',1.6))
            bl=float(getattr(cfg.inference,'fidelity_blur_large',5.0))
            src_im=src_rgb
            gen_im=gen_rgb
            src_b1=np.asarray(src_im.filter(ImageFilter.GaussianBlur(bs)),dtype=np.float32)
            src_b2=np.asarray(src_im.filter(ImageFilter.GaussianBlur(bl)),dtype=np.float32)
            gen_b1=np.asarray(gen_im.filter(ImageFilter.GaussianBlur(bs)),dtype=np.float32)
            gen_b2=np.asarray(gen_im.filter(ImageFilter.GaussianBlur(bl)),dtype=np.float32)
            src_mid=src_b1-src_b2
            gen_mid=gen_b1-gen_b2
            gen_high=gen-gen_b1
            mid=float(getattr(cfg.inference,'fidelity_mid_strength',0.28))
            high=float(getattr(cfg.inference,'fidelity_high_strength',0.72))
            # Keep the source as the low-frequency geometry/color anchor, but
            # let Flux contribute substantially more of the reconstructed
            # mid/high-frequency surface detail. The previous V5 formula
            # effectively reconstituted the old pixel-art image and made the
            # AI pass look almost identical to a normal upscale.
            #
            # We preserve broad shapes through src_b2, while replacing the
            # higher-frequency bands with controlled Flux detail.
            out = src_b2 + (1.0 - mid) * src_mid + mid * gen_mid + high * gen_high
            out=np.clip(out,0,255)
            # Keep exact source alpha and exact output dimensions.
            result=Image.fromarray(out.astype(np.uint8),'RGB')
            if src_a is not None:
                result.putalpha(src_a.resize(size,Image.Resampling.NEAREST))
            elif gen_a is not None:
                # If source was opaque but generator unexpectedly created alpha,
                # do not allow the model to alter the asset's opacity.
                pass
            result.save(generated_path,'PNG',compress_level=int(getattr(cfg.inference,'png_compress_level',6)),optimize=False)
    except Exception as e:
        # Never fail a batch because of post-fusion. The original generated file
        # remains usable if this safeguard trips.
        try:
            from ..core.logger import get_logger
            get_logger('fidelity').warning(f'V5 fidelity fusion skipped on {source_path.name}: {e}')
        except Exception:
            pass


def enforce_source_color_fidelity(source_path: Path, generated_path: Path, cfg) -> None:
    """Hard color-family guard: source chroma is authoritative; generated luminance adds detail.

    This is intentionally separate from fidelity_fusion. It prevents catastrophic cases such as
    a red source region becoming white/gray while still allowing Flux to contribute realistic
    luminance, highlights and microcontrast.
    """
    if not getattr(cfg.inference, 'preserve_source_colors', True):
        return
    from PIL import Image
    import numpy as np
    try:
        with Image.open(source_path) as si, Image.open(generated_path) as gi:
            src_rgb, src_a = _split_alpha(si)
            gen_rgb, gen_a = _split_alpha(gi)
            size=gen_rgb.size
            src=np.asarray(src_rgb.resize(size, Image.Resampling.LANCZOS), dtype=np.float32)/255.0
            gen=np.asarray(gen_rgb, dtype=np.float32)/255.0
            # RGB -> HSV, preserving source hue/saturation where source is chromatic.
            import colorsys
            def rgb_to_hsv_arr(a):
                flat=a.reshape(-1,3); out=np.empty_like(flat)
                for i,(r,g,b) in enumerate(flat): out[i]=colorsys.rgb_to_hsv(float(r),float(g),float(b))
                return out.reshape(a.shape)
            def hsv_to_rgb_arr(a):
                flat=a.reshape(-1,3); out=np.empty_like(flat)
                for i,(h,sv,v) in enumerate(flat): out[i]=colorsys.hsv_to_rgb(float(h),float(sv),float(v))
                return out.reshape(a.shape)
            sh=rgb_to_hsv_arr(src); gh=rgb_to_hsv_arr(gen)
            sat=sh[...,1]
            chromatic=sat > 0.10
            hue_strength=float(getattr(cfg.inference,'color_hue_strength',0.98))
            sat_strength=float(getattr(cfg.inference,'color_saturation_strength',0.92))
            drift=float(getattr(cfg.inference,'color_value_drift_limit',0.22))
            detail=float(getattr(cfg.inference,'color_detail_strength',0.55))
            out=gh.copy()
            # Hue/saturation from source for colored regions; preserve neutral grayscale regions.
            dh=((gh[...,0]-sh[...,0]+0.5)%1.0)-0.5
            out[...,0]=np.where(chromatic, (sh[...,0] + dh*(1.0-hue_strength))%1.0, gh[...,0])
            target_sat=sh[...,1]*sat_strength + gh[...,1]*(1.0-sat_strength)
            out[...,1]=np.where(chromatic, target_sat, gh[...,1])
            # Generated value may add detail, but is not allowed to jump wildly away from source.
            sv=sh[...,2]; gv=gh[...,2]
            delta=np.clip(gv-sv, -drift, drift)
            out[...,2]=np.clip(sv + delta*detail + (gv-sv)*(1.0 if not chromatic.any() else 0.0),0,1)
            # Neutral source pixels use generated luminance but keep grayscale character.
            neutral=~chromatic
            out[...,2]=np.where(neutral, np.clip(gv,0,1), out[...,2])
            rgb=np.clip(hsv_to_rgb_arr(out)*255.0,0,255).astype(np.uint8)
            result=Image.fromarray(rgb,'RGB')
            if src_a is not None:
                result.putalpha(src_a.resize(size,Image.Resampling.NEAREST))
            result.save(generated_path,'PNG',compress_level=int(getattr(cfg.inference,'png_compress_level',6)), optimize=False)
    except Exception as e:
        try:
            from ..core.logger import get_logger
            get_logger('fidelity').warning(f'V5.5 color fidelity skipped on {source_path.name}: {e}')
        except Exception:
            pass
