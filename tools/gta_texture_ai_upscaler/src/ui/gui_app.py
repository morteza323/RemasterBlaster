"""
Optional multi-tab Tkinter GUI for GTA Texture AI Upscaler.
- Main: lag-behind before/after preview + Start/Stop
- Settings: full inference / batch / rest controls
- Monitor: live GPU/RAM + job stats
Pipeline core is untouched; engine runs in a worker thread.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import yaml
from typing import Optional

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


class UpscalerGUI:
    def __init__(self, cfg, engine, backend):
        self.cfg = cfg
        self.engine = engine
        self.backend = backend
        self.root = tk.Tk()
        self.root.title(f"GTA Texture AI Upscaler v{getattr(cfg, 'version', '4.1')} — GUI")
        self.root.geometry("1180x780")
        self.root.minsize(960, 640)

        self._worker: Optional[threading.Thread] = None
        self._ui_queue: queue.Queue = queue.Queue()
        self._photo_orig = None
        self._photo_out = None
        # Lag-behind: show last COMPLETED pair while current job runs
        self._last_orig: Optional[str] = None
        self._last_out: Optional[str] = None
        self._current_name: str = ""

        self._build()
        self._wire_engine_hooks()
        self.root.after(200, self._drain_queue)
        self.root.after(1500, self._tick_temps)
        self.root.after(3000, self._tick_stats)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.tab_main = ttk.Frame(self.nb)
        self.tab_settings = ttk.Frame(self.nb)
        self.tab_monitor = ttk.Frame(self.nb)
        self.tab_about = ttk.Frame(self.nb)
        self.nb.add(self.tab_main, text="  Main  ")
        self.nb.add(self.tab_settings, text="  Settings  ")
        self.nb.add(self.tab_monitor, text="  Monitor  ")
        self.nb.add(self.tab_about, text="  About  ")

        self._build_main()
        self._build_settings()
        self._build_monitor()
        self._build_about()

    def _build_main(self) -> None:
        top = ttk.Frame(self.tab_main, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Input:").grid(row=0, column=0, sticky="w")
        self.var_input = tk.StringVar(value=str(self.cfg.input_dir))
        ttk.Entry(top, textvariable=self.var_input, width=60).grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(top, text="…", width=3, command=self._browse_input).grid(row=0, column=2)

        ttk.Label(top, text="Output:").grid(row=1, column=0, sticky="w")
        self.var_output = tk.StringVar(value=str(self.cfg.output_dir))
        ttk.Entry(top, textvariable=self.var_output, width=60).grid(row=1, column=1, sticky="we", padx=4)
        ttk.Button(top, text="…", width=3, command=self._browse_output).grid(row=1, column=2)
        top.columnconfigure(1, weight=1)

        ctrl = ttk.Frame(self.tab_main, padding=8)
        ctrl.pack(fill=tk.X)
        self.btn_start = ttk.Button(ctrl, text="▶ Start / Resume", command=self._on_start)
        self.btn_start.pack(side=tk.LEFT, padx=4)
        self.btn_stop = ttk.Button(ctrl, text="⏹ Stop (after current)", command=self._on_stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=4)
        ttk.Button(ctrl, text="Stats", command=self._on_stats).pack(side=tk.LEFT, padx=4)
        ttk.Button(ctrl, text="Open Settings tab", command=lambda: self.nb.select(self.tab_settings)).pack(
            side=tk.LEFT, padx=4
        )

        self.lbl_gpu = ttk.Label(ctrl, text="GPU: —")
        self.lbl_gpu.pack(side=tk.LEFT, padx=12)
        self.lbl_status = ttk.Label(ctrl, text="Idle — preview shows last finished pair (one step behind).")
        self.lbl_status.pack(side=tk.LEFT, padx=8)

        prev = ttk.LabelFrame(
            self.tab_main,
            text="Preview — last COMPLETED pair (stays until next job finishes)  |  aspect ratio preserved",
            padding=6,
        )
        prev.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        left = ttk.Frame(prev)
        right = ttk.Frame(prev)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))
        ttk.Label(left, text="Original", anchor="center").pack(fill=tk.X)
        ttk.Label(right, text="Upscaled", anchor="center").pack(fill=tk.X)
        self.canvas_orig = tk.Canvas(left, bg="#1a1a1a", highlightthickness=1, highlightbackground="#333")
        self.canvas_out = tk.Canvas(right, bg="#1a1a1a", highlightthickness=1, highlightbackground="#333")
        self.canvas_orig.pack(fill=tk.BOTH, expand=True)
        self.canvas_out.pack(fill=tk.BOTH, expand=True)

        self.lbl_names = ttk.Label(self.tab_main, text="No completed job yet — preview updates after first finish.", anchor="w")
        self.lbl_names.pack(fill=tk.X, padx=10, pady=(0, 4))

        logf = ttk.LabelFrame(self.tab_main, text="Activity", padding=4)
        logf.pack(fill=tk.BOTH, expand=False, padx=8, pady=(0, 8))
        self.txt_log = tk.Text(logf, height=7, wrap=tk.WORD, bg="#111", fg="#d4d4d4", insertbackground="#fff")
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        self.txt_log.configure(state=tk.DISABLED)

    def _build_settings(self) -> None:
        outer = ttk.Frame(self.tab_settings, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        paths = ttk.LabelFrame(outer, text="Paths", padding=10)
        paths.pack(fill=tk.X, pady=6)
        ttk.Label(paths, text="Handmade / fixed-with-ai folder").grid(row=0, column=0, sticky="w")
        self.var_handmade = tk.StringVar(
            value=str(getattr(self.cfg, "handmade_dir", r"H:\gta sa textures\fucked up fixed with ai"))
        )
        ttk.Entry(paths, textvariable=self.var_handmade, width=70).grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(paths, text="…", width=3, command=self._browse_handmade).grid(row=0, column=2)
        paths.columnconfigure(1, weight=1)
        ttk.Label(
            paths,
            text="Files here are auto-SKIPPED on run start so Flux will not re-upscale them.",
            foreground="#666",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=4)

        inf = ttk.LabelFrame(outer, text="Inference", padding=10)
        inf.pack(fill=tk.X, pady=6)
        self.var_steps = tk.IntVar(value=int(self.cfg.inference.steps))
        self.var_strength = tk.DoubleVar(value=float(self.cfg.inference.strength))
        self.var_cfg = tk.DoubleVar(value=float(getattr(self.cfg.inference, "cfg_scale", 1.0)))
        self.var_target = tk.IntVar(value=int(getattr(self.cfg.inference, "target_long_side", 512)))
        self.var_maxside = tk.IntVar(value=int(getattr(self.cfg.inference, "max_side", 1024)))
        self.var_minside = tk.IntVar(value=int(getattr(self.cfg.inference, "min_side", 64)))
        self.var_pre_upscale = tk.BooleanVar(value=bool(getattr(self.cfg.inference, "pre_upscale_source", True)))
        self.var_post_sharpen = tk.DoubleVar(value=float(getattr(self.cfg.inference, "post_sharpen", 1.10)))
        self.var_detail_radius = tk.DoubleVar(value=float(getattr(self.cfg.inference, "post_detail_radius", 0.7)))

        def row(parent, r, label, var, frm, to, inc=1, is_float=False):
            ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=3)
            if is_float:
                ttk.Spinbox(parent, textvariable=var, from_=frm, to=to, increment=inc, width=10).grid(
                    row=r, column=1, sticky="w", padx=8
                )
            else:
                ttk.Spinbox(parent, textvariable=var, from_=frm, to=to, width=10).grid(
                    row=r, column=1, sticky="w", padx=8
                )

        row(inf, 0, "Steps", self.var_steps, 1, 40)
        row(inf, 1, "Strength (img2img)", self.var_strength, 0.05, 1.0, 0.01, True)
        row(inf, 2, "CFG scale", self.var_cfg, 0.5, 8.0, 0.1, True)
        row(inf, 3, "Target long side", self.var_target, 128, 2048)
        row(inf, 4, "Max side", self.var_maxside, 256, 2048)
        row(inf, 5, "Min side", self.var_minside, 32, 512)
        ttk.Checkbutton(inf, text="Pre-upscale source before Flux (anti-pixelation)", variable=self.var_pre_upscale, command=self._on_reconstruction_toggle).grid(row=6,column=0,columnspan=2,sticky='w',padx=4,pady=2)
        row(inf, 7, "Post detail sharpness", self.var_post_sharpen, 1.0, 1.5, 0.01, True)
        row(inf, 8, "Detail radius", self.var_detail_radius, 0.3, 1.5, 0.1, True)
        ttk.Label(inf, text="V6: Flux reconstructs detail on a pre-upscaled canvas instead of starting from blocky source pixels.", foreground="#666").grid(
            row=9, column=0, columnspan=3, sticky="w", pady=6
        )

        srcf = ttk.LabelFrame(outer, text="Context sources / AI guidance", padding=10)
        srcf.pack(fill=tk.X, pady=6)
        ii = self.cfg.inference
        self.var_use_filename = tk.BooleanVar(value=bool(getattr(ii, "filename_material_hints", False)))
        self.var_use_image = tk.BooleanVar(value=bool(getattr(ii, "image_analysis_hints", True)))
        self.var_use_kb = tk.BooleanVar(value=bool(getattr(ii, "gta_knowledge_base", True)))
        self.var_use_uv = tk.BooleanVar(value=bool(getattr(ii, "uv_context_hints", True)))
        self.var_color_lock = tk.BooleanVar(value=bool(getattr(ii, "preserve_source_colors", True)))
        ttk.Checkbutton(srcf, text="Use filename semantic hints (OFF = pixels only)", variable=self.var_use_filename, command=self._on_context_toggle).grid(row=0,column=0,sticky='w',padx=4,pady=2)
        ttk.Checkbutton(srcf, text="Use image/color analysis", variable=self.var_use_image, command=self._on_context_toggle).grid(row=0,column=1,sticky='w',padx=4,pady=2)
        ttk.Checkbutton(srcf, text="Use built-in GTA SA keyword database", variable=self.var_use_kb, command=self._on_context_toggle).grid(row=1,column=0,sticky='w',padx=4,pady=2)
        ttk.Checkbutton(srcf, text="Use UV-specific context (interior/wheel/body)", variable=self.var_use_uv, command=self._on_context_toggle).grid(row=1,column=1,sticky='w',padx=4,pady=2)
        ttk.Checkbutton(srcf, text="Hard source color fidelity (red stays red, etc.)", variable=self.var_color_lock, command=self._on_context_toggle).grid(row=2,column=0,columnspan=2,sticky='w',padx=4,pady=2)
        self.var_custom_kb = tk.StringVar(value=str(getattr(ii, 'custom_knowledge_txt', '') or getattr(getattr(self.cfg,'knowledge',None),'custom_txt','')))
        ttk.Label(srcf,text="Custom TXT knowledge:").grid(row=3,column=0,sticky='w',padx=4,pady=4)
        ttk.Entry(srcf,textvariable=self.var_custom_kb,width=62).grid(row=3,column=1,sticky='we',padx=4)
        ttk.Button(srcf,text="…",width=3,command=self._browse_custom_kb).grid(row=3,column=2)
        ttk.Label(srcf,text="TXT format: keyword<TAB>description<TAB>category  (or keyword = description)",foreground='#666').grid(row=4,column=0,columnspan=3,sticky='w',padx=4)
        srcf.columnconfigure(1,weight=1)

        bat = ttk.LabelFrame(outer, text="Batch / cooldown", padding=10)
        bat.pack(fill=tk.X, pady=6)
        self.var_cooldown = tk.IntVar(value=int(self.cfg.batch.cooldown_seconds))
        self.var_jobs_cd = tk.IntVar(value=int(self.cfg.batch.jobs_before_cooldown))
        self.var_retries = tk.IntVar(value=int(getattr(self.cfg.batch, "max_retries", 2)))
        row(bat, 0, "Cooldown seconds", self.var_cooldown, 5, 300)
        row(bat, 1, "Jobs before cooldown", self.var_jobs_cd, 5, 200)
        row(bat, 2, "Max retries", self.var_retries, 0, 10)

        hw = ttk.LabelFrame(outer, text="Hardware rest / thermal", padding=10)
        hw.pack(fill=tk.X, pady=6)
        self.var_rest_h = tk.DoubleVar(value=float(self.cfg.hardware.rest_every_hours))
        self.var_rest_m = tk.IntVar(value=int(self.cfg.hardware.rest_duration_minutes))
        self.var_gpu_pause = tk.IntVar(value=int(getattr(self.cfg.hardware, "gpu_pause_temp_c", 75)))
        self.var_gpu_resume = tk.IntVar(value=int(getattr(self.cfg.hardware, "gpu_resume_temp_c", 62)))
        row(hw, 0, "Rest every (hours)", self.var_rest_h, 0.5, 12, 0.5, True)
        row(hw, 1, "Rest duration (min)", self.var_rest_m, 1, 60)
        row(hw, 2, "GPU pause temp °C", self.var_gpu_pause, 50, 95)
        row(hw, 3, "GPU resume temp °C", self.var_gpu_resume, 40, 90)

        btns = ttk.Frame(outer)
        btns.pack(fill=tk.X, pady=12)
        ttk.Button(btns, text="Apply settings", command=self._on_apply).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Reset to config defaults", command=self._on_reset_settings).pack(side=tk.LEFT, padx=4)
        self.lbl_apply = ttk.Label(btns, text="")
        self.lbl_apply.pack(side=tk.LEFT, padx=12)
        self._autosave_after = None
        # Settings are persisted automatically as soon as a control changes.
        for _v in (self.var_steps, self.var_strength, self.var_cfg, self.var_target, self.var_maxside, self.var_minside,
                   self.var_cooldown, self.var_jobs_cd, self.var_retries, self.var_rest_h, self.var_rest_m,
                   self.var_gpu_pause, self.var_gpu_resume, self.var_handmade):
            try:
                _v.trace_add("write", lambda *_args: self._schedule_autosave())
            except Exception:
                pass

    def _build_monitor(self) -> None:
        f = ttk.Frame(self.tab_monitor, padding=12)
        f.pack(fill=tk.BOTH, expand=True)
        self.lbl_mon_gpu = ttk.Label(f, text="GPU temperature: —", font=("Segoe UI", 12))
        self.lbl_mon_gpu.pack(anchor="w", pady=4)
        self.lbl_mon_ram = ttk.Label(f, text="RAM: —", font=("Segoe UI", 12))
        self.lbl_mon_ram.pack(anchor="w", pady=4)
        self.lbl_mon_vram = ttk.Label(f, text="VRAM: —", font=("Segoe UI", 12))
        self.lbl_mon_vram.pack(anchor="w", pady=4)
        self.lbl_mon_job = ttk.Label(f, text="Current job: —", font=("Segoe UI", 11))
        self.lbl_mon_job.pack(anchor="w", pady=8)
        ttk.Separator(f).pack(fill=tk.X, pady=8)
        self.lbl_mon_stats = ttk.Label(f, text="Job stats: (click Refresh)", justify=tk.LEFT, font=("Consolas", 10))
        self.lbl_mon_stats.pack(anchor="w", pady=4)
        ttk.Button(f, text="Refresh stats", command=self._refresh_monitor_stats).pack(anchor="w", pady=8)
        ttk.Label(
            f,
            text="Safety pauses (thermal / RAM / mandatory rest) are handled by the engine automatically.",
            foreground="#666",
        ).pack(anchor="w", pady=12)

    def _build_about(self) -> None:
        f = ttk.Frame(self.tab_about, padding=16)
        f.pack(fill=tk.BOTH, expand=True)
        ttk.Label(f, text=f"GTA Texture AI Upscaler v{getattr(self.cfg, 'version', '4.1')}", font=("Segoe UI", 14, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            f,
            text=(
                "Main tab preview is one step behind on purpose:\n"
                "while job N is processing, you still see job N-1 original vs upscaled.\n"
                "When N finishes, preview switches to N and stays until N+1 finishes.\n\n"
                "UV-critical textures (character body, wheels, skin atlases) use strength ≤ 0.30; normal textures respect the exact strength value.\n"
                "CLI (start.bat / python main.py --start) is unchanged and fully supported.\n"
                "Console window keeps the full detailed log."
            ),
            justify=tk.LEFT,
        ).pack(anchor="w", pady=12)

    # ------------------------------------------------------------------ helpers
    def _browse_input(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_input.get() or None)
        if d:
            self.var_input.set(d)

    def _browse_output(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_output.get() or None)
        if d:
            self.var_output.set(d)

    def _browse_handmade(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_handmade.get() or None)
        if d:
            self.var_handmade.set(d)

    def _log(self, msg: str) -> None:
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.insert(tk.END, msg.rstrip() + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.configure(state=tk.DISABLED)

    def _browse_custom_kb(self) -> None:
        f = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if f:
            self.var_custom_kb.set(f)
            self._on_context_toggle()

    def _schedule_autosave(self) -> None:
        try:
            if self._autosave_after is not None:
                self.root.after_cancel(self._autosave_after)
            self._autosave_after = self.root.after(350, self._autosave_settings_now)
        except Exception:
            pass

    def _autosave_settings_now(self) -> None:
        self._autosave_after = None
        try:
            self._apply_settings_to_cfg(save_file=False)
            self._save_settings_file()
            self.lbl_apply.configure(text="Settings saved automatically.")
        except Exception as e:
            self._log(f"Realtime settings save warning: {e}")

    def _save_settings_file(self) -> None:
        """Persist current GUI settings to config/settings.yaml immediately."""
        path = self.cfg.resolve_path("config/settings.yaml")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        inf = raw.setdefault("inference", {})
        inf.update({
            "steps": int(self.cfg.inference.steps), "strength": float(self.cfg.inference.strength),
            "cfg_scale": float(self.cfg.inference.cfg_scale), "target_long_side": int(self.cfg.inference.target_long_side),
            "max_side": int(self.cfg.inference.max_side), "min_side": int(self.cfg.inference.min_side),
            "filename_material_hints": bool(self.cfg.inference.filename_material_hints),
            "image_analysis_hints": bool(self.cfg.inference.image_analysis_hints),
            "gta_knowledge_base": bool(self.cfg.inference.gta_knowledge_base),
            "custom_knowledge_txt": str(self.cfg.inference.custom_knowledge_txt or ""),
            "uv_context_hints": bool(self.cfg.inference.uv_context_hints),
            "preserve_source_colors": bool(self.cfg.inference.preserve_source_colors),
            "pre_upscale_source": bool(self.cfg.inference.pre_upscale_source),
            "pre_upscale_filter": str(getattr(self.cfg.inference, "pre_upscale_filter", "LANCZOS")),
            "post_sharpen": float(self.cfg.inference.post_sharpen),
            "post_detail_radius": float(self.cfg.inference.post_detail_radius),
        })
        raw.setdefault("knowledge", {})["custom_txt"] = str(self.cfg.inference.custom_knowledge_txt or "")
        path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def _on_context_toggle(self) -> None:
        try:
            self.cfg.inference.filename_material_hints = bool(self.var_use_filename.get())
            self.cfg.inference.image_analysis_hints = bool(self.var_use_image.get())
            self.cfg.inference.gta_knowledge_base = bool(self.var_use_kb.get())
            self.cfg.inference.uv_context_hints = bool(self.var_use_uv.get())
            self.cfg.inference.preserve_source_colors = bool(self.var_color_lock.get())
            self.cfg.inference.custom_knowledge_txt = self.var_custom_kb.get().strip()
            if hasattr(self.cfg, 'knowledge'):
                self.cfg.knowledge.custom_txt = self.cfg.inference.custom_knowledge_txt
            self._save_settings_file()
            self.lbl_apply.configure(text="Context settings saved in real-time.")
        except Exception as e:
            self._log(f"Settings save warning: {e}")

    def _on_reconstruction_toggle(self) -> None:
        try:
            self.cfg.inference.pre_upscale_source = bool(self.var_pre_upscale.get())
            self.cfg.inference.post_sharpen = float(self.var_post_sharpen.get())
            self.cfg.inference.post_detail_radius = float(self.var_detail_radius.get())
            self._save_settings_file()
            self.lbl_apply.configure(text="V6 reconstruction settings saved in real-time.")
        except Exception as e:
            self._log(f"Settings save warning: {e}")

    def _apply_settings_to_cfg(self, save_file: bool = True) -> None:
        if hasattr(self, "var_handmade"):
            self.cfg.handmade_dir = self.var_handmade.get().strip()
        self.cfg.inference.steps = int(self.var_steps.get())
        self.cfg.inference.strength = float(self.var_strength.get())
        if hasattr(self.cfg.inference, "cfg_scale"):
            self.cfg.inference.cfg_scale = float(self.var_cfg.get())
        if hasattr(self.cfg.inference, "target_long_side"):
            self.cfg.inference.target_long_side = int(self.var_target.get())
        if hasattr(self.cfg.inference, "max_side"):
            self.cfg.inference.max_side = int(self.var_maxside.get())
        if hasattr(self.cfg.inference, "min_side"):
            self.cfg.inference.min_side = int(self.var_minside.get())
        self.cfg.inference.pre_upscale_source = bool(self.var_pre_upscale.get())
        self.cfg.inference.post_sharpen = float(self.var_post_sharpen.get())
        self.cfg.inference.post_detail_radius = float(self.var_detail_radius.get())
        self.cfg.inference.filename_material_hints = bool(self.var_use_filename.get())
        self.cfg.inference.image_analysis_hints = bool(self.var_use_image.get())
        self.cfg.inference.gta_knowledge_base = bool(self.var_use_kb.get())
        self.cfg.inference.uv_context_hints = bool(self.var_use_uv.get())
        self.cfg.inference.preserve_source_colors = bool(self.var_color_lock.get())
        self.cfg.inference.custom_knowledge_txt = self.var_custom_kb.get().strip()
        if hasattr(self.cfg, 'knowledge'):
            self.cfg.knowledge.custom_txt = self.cfg.inference.custom_knowledge_txt
        self.cfg.batch.cooldown_seconds = int(self.var_cooldown.get())
        self.cfg.batch.jobs_before_cooldown = int(self.var_jobs_cd.get())
        if hasattr(self.cfg.batch, "max_retries"):
            self.cfg.batch.max_retries = int(self.var_retries.get())
        self.cfg.hardware.rest_every_hours = float(self.var_rest_h.get())
        self.cfg.hardware.rest_duration_minutes = int(self.var_rest_m.get())
        if hasattr(self.cfg.hardware, "gpu_pause_temp_c"):
            self.cfg.hardware.gpu_pause_temp_c = int(self.var_gpu_pause.get())
        if hasattr(self.cfg.hardware, "gpu_resume_temp_c"):
            self.cfg.hardware.gpu_resume_temp_c = int(self.var_gpu_resume.get())
        if save_file:
            try:
                self._save_settings_file()
            except Exception as e:
                self._log(f"Settings file save warning: {e}")
        try:
            self.engine.safety.rest_every_sec = float(self.cfg.hardware.rest_every_hours) * 3600.0
            self.engine.safety.rest_duration_sec = int(self.cfg.hardware.rest_duration_minutes) * 60
            if hasattr(self.engine.safety, "gpu_pause"):
                self.engine.safety.gpu_pause = int(self.var_gpu_pause.get())
            if hasattr(self.engine.safety, "gpu_resume"):
                self.engine.safety.gpu_resume = int(self.var_gpu_resume.get())
        except Exception:
            pass

    def _on_apply(self) -> None:
        try:
            self._apply_settings_to_cfg()
            msg = (
                f"Applied: steps={self.cfg.inference.steps} strength={self.cfg.inference.strength} "
                f"target={getattr(self.cfg.inference, 'target_long_side', '?')} "
                f"cooldown={self.cfg.batch.cooldown_seconds}s"
            )
            self.lbl_apply.configure(text=msg)
            self._log(msg)
            self.lbl_status.configure(text="Settings applied — next jobs use these values.")
        except Exception as e:
            messagebox.showerror("Apply settings", str(e))

    def _on_reset_settings(self) -> None:
        self.var_steps.set(int(self.cfg.inference.steps))
        self.var_strength.set(float(self.cfg.inference.strength))
        self.lbl_apply.configure(text="Spinboxes reloaded from current cfg (Apply to push).")

    def _wire_engine_hooks(self) -> None:
        def on_start(source: Path, output: Path, label: str) -> None:
            self._ui_queue.put(("start", str(source), str(output), label))

        def on_done(source: Path, output: Path, ok: bool, ms: int) -> None:
            self._ui_queue.put(("done", str(source), str(output), ok, ms))

        self.engine.on_job_start = on_start
        self.engine.on_job_done = on_done

    def _fit_image(self, path: str, box_w: int, box_h: int):
        """Preserve aspect ratio; letterbox inside box. CPU only, max 512."""
        im = Image.open(path)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")
        # limit decode work
        im.thumbnail((512, 512), Image.Resampling.BILINEAR)
        iw, ih = im.size
        if iw < 1 or ih < 1 or box_w < 1 or box_h < 1:
            return im
        scale = min(box_w / iw, box_h / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        return im.resize((nw, nh), Image.Resampling.NEAREST)

    def _draw_on_canvas(self, canvas: tk.Canvas, path: str, keep_attr: str) -> None:
        canvas.update_idletasks()
        cw = max(canvas.winfo_width(), 80)
        ch = max(canvas.winfo_height(), 80)
        fitted = self._fit_image(path, cw, ch)
        photo = ImageTk.PhotoImage(fitted)
        setattr(self, keep_attr, photo)
        canvas.delete("all")
        # center
        x = (cw - fitted.size[0]) // 2
        y = (ch - fitted.size[1]) // 2
        canvas.create_image(x, y, anchor="nw", image=photo)

    def _show_last_pair(self) -> None:
        if Image is None or not self._last_orig:
            return
        try:
            self._draw_on_canvas(self.canvas_orig, self._last_orig, "_photo_orig")
            if self._last_out and Path(self._last_out).is_file():
                self._draw_on_canvas(self.canvas_out, self._last_out, "_photo_out")
        except Exception as e:
            self._log(f"preview error: {e}")

    def _tick_temps(self) -> None:
        try:
            from src.core.hardware import get_gpu_temperature, get_ram_percent, get_vram_usage

            gpu_t = get_gpu_temperature()
            ram = get_ram_percent()
            used, total = get_vram_usage()
            parts = []
            if gpu_t is not None:
                parts.append(f"GPU: {gpu_t:.0f}°C")
                self.lbl_mon_gpu.configure(text=f"GPU temperature: {gpu_t:.0f}°C")
            else:
                parts.append("GPU: —")
                self.lbl_mon_gpu.configure(text="GPU temperature: —")
            if ram is not None:
                parts.append(f"RAM: {ram:.0f}%")
                self.lbl_mon_ram.configure(text=f"RAM: {ram:.0f}%")
            if used is not None and total:
                parts.append(f"VRAM: {used}/{total}MB")
                self.lbl_mon_vram.configure(text=f"VRAM: {used} / {total} MB")
            self.lbl_gpu.configure(text="  |  ".join(parts))
        except Exception:
            pass
        self.root.after(2000, self._tick_temps)

    def _tick_stats(self) -> None:
        try:
            if self._current_name:
                self.lbl_mon_job.configure(text=f"Current job: {self._current_name}")
        except Exception:
            pass
        self.root.after(3000, self._tick_stats)

    def _refresh_monitor_stats(self) -> None:
        try:
            stats = self.engine.db.stats()
            lines = [f"  {k}: {v}" for k, v in stats.items()]
            self.lbl_mon_stats.configure(text="Job stats:\n" + "\n".join(lines))
        except Exception as e:
            self.lbl_mon_stats.configure(text=f"Error: {e}")

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self._ui_queue.get_nowait()
                kind = item[0]
                if kind == "start":
                    # Do NOT change preview — keep showing previous completed pair
                    _, src, out, label = item
                    self._current_name = Path(src).name
                    self.lbl_status.configure(
                        text=f"Processing: {self._current_name} [{label or '…'}]  —  preview = previous finished"
                    )
                    self.lbl_mon_job.configure(text=f"Current job: {self._current_name} [{label}]")
                    self._log(f"▶ {self._current_name} [{label}]")
                elif kind == "done":
                    _, src, out, ok, ms = item
                    if ok and Path(out).is_file():
                        # Now switch preview to this completed pair; stays until next done
                        self._last_orig = src
                        self._last_out = out
                        self._show_last_pair()
                        name = Path(out).name
                        self.lbl_names.configure(
                            text=f"Showing last finished: {Path(src).name}  →  {name}  ({ms/1000:.1f}s)"
                        )
                        self.lbl_status.configure(text=f"Last finished: {name} in {ms/1000:.1f}s")
                        self._log(f"✓ {name} ({ms/1000:.1f}s) — preview updated")
                    else:
                        self._log(f"✗ {Path(src).name}")
                elif kind == "finished":
                    self.btn_start.configure(state=tk.NORMAL)
                    self.btn_stop.configure(state=tk.DISABLED)
                    self._current_name = ""
                    self.lbl_status.configure(text=item[1])
                    self.lbl_mon_job.configure(text="Current job: —")
                    self._log(item[1])
        except queue.Empty:
            pass
        self.root.after(200, self._drain_queue)

    def _on_start(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Busy", "Already running.")
            return
        self._apply_settings_to_cfg()
        import src.pipeline.engine as eng_mod

        eng_mod._INTERRUPT_REQUESTED = False
        self.btn_start.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self.lbl_status.configure(text="Starting…")

        def run():
            try:
                self.engine.set_roots(self.var_input.get(), self.var_output.get())
                self.engine.run(dry_run=False, max_jobs=None)
                if eng_mod._INTERRUPT_REQUESTED:
                    self._ui_queue.put(("finished", "Stopped — progress saved. Start again to resume."))
                else:
                    self._ui_queue.put(("finished", "All pending jobs finished."))
            except Exception as e:
                self._ui_queue.put(("finished", f"Error: {e}"))

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()
        self._log("Batch started (console shows full logs).")

    def _on_stop(self) -> None:
        import src.pipeline.engine as eng_mod

        eng_mod._INTERRUPT_REQUESTED = True
        self.lbl_status.configure(text="Stop requested — finishing current job…")
        self._log("Stop requested…")

    def _on_stats(self) -> None:
        try:
            stats = self.engine.db.stats()
            messagebox.showinfo("Job stats", "\n".join(f"{k}: {v}" for k, v in stats.items()))
            self._log("STATS " + str(stats))
            self._refresh_monitor_stats()
        except Exception as e:
            messagebox.showerror("Stats", str(e))

    def run(self) -> None:
        self.root.mainloop()


def launch_gui(cfg, engine, backend) -> None:
    app = UpscalerGUI(cfg, engine, backend)
    app.run()
