#!/usr/bin/env python3
"""
Win11 Magic Upgrade - portable GUI/CLI.
Pure Python engine: NO .NET Framework 4.x, NO PowerShell, NO FlyOOBE.
"""
from __future__ import annotations

import ctypes
import json
import locale
import os
import sys

import stdio_safe

stdio_safe.ensure_stdio()
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", here))
        for candidate in (here, meipass, here / "_internal"):
            if (candidate / "python" / "engine").exists() or (candidate / "engine").exists():
                return candidate
            if (candidate / "i18n" / "strings.json").exists():
                return candidate
        return here
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path(root: Path) -> None:
    for p in (root / "python", root, Path(__file__).resolve().parent):
        s = str(p)
        if p.exists() and s not in sys.path:
            sys.path.insert(0, s)


def load_strings(root: Path) -> dict:
    lang = (locale.getdefaultlocale()[0] or "en").lower()
    if lang.startswith("fr"):
        code = "fr"
    elif lang.startswith("de"):
        code = "de"
    elif lang.startswith("es"):
        code = "es"
    elif lang.startswith("it"):
        code = "it"
    else:
        code = "en"
    path = root / "i18n" / "strings.json"
    data: dict = {"en": {}, "fr": {}}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    base = data.get("en", {})
    chosen = data.get(code, base)
    merged = dict(base)
    merged.update(chosen or {})
    return merged


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: list[str] | None = None) -> bool:
    """
    Elevate via UAC. Returns True if ShellExecute accepted the request (>32).
    Caller must exit the non-elevated process on success.
    """
    args = list(extra_args or [])
    if getattr(sys, "frozen", False):
        params = " ".join(f'"{a}"' if " " in a else a for a in args)
        rc = int(ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1))
    else:
        script = str(Path(__file__).resolve())
        params = " ".join([f'"{script}"'] + [f'"{a}"' if " " in a else a for a in args])
        rc = int(ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1))
    return rc > 32


class App(tk.Tk):
    def __init__(self, auto_action: str | None = None) -> None:
        super().__init__()
        self.root_dir = app_root()
        _ensure_sys_path(self.root_dir)
        self.t = load_strings(self.root_dir)
        self.title(self.t.get("app_title", "Win11 Magic Upgrade"))
        self.configure(bg="#0f172a")
        self._busy = False
        self._elevating = False
        self._auto_action = auto_action
        self._resume_chain = False

        # Autonomous resume after reboot (RunOnce / logon scheduled task)
        if not self._auto_action:
            try:
                from engine.autonomy import should_auto_resume_on_startup  # type: ignore

                if should_auto_resume_on_startup() and is_admin():
                    self._auto_action = "oneclick"
                    self._resume_chain = True
                    self.append(
                        "Migration in progress — auto-resuming One-Click after reboot…\n"
                    )
            except Exception:
                pass

        # Tk callback crashes → sanitized GitHub issue
        def _tk_exc(exc, val, tb):  # type: ignore[no-untyped-def]
            try:
                from engine.gh_report import report_unhandled_exception  # type: ignore

                report_unhandled_exception(exc, val, tb, kind="tk-callback-exception")
            except Exception:
                pass
            try:
                tk.Tk.report_callback_exception(self, exc, val, tb)
            except Exception:
                pass

        self.report_callback_exception = _tk_exc  # type: ignore[method-assign]

        self.geometry("980x760")
        self.minsize(860, 640)
        self.resizable(True, True)
        self._action_buttons: list[tk.Button] = []
        self._opt_vars: dict[str, tk.BooleanVar] = {}
        lang = (locale.getdefaultlocale()[0] or "en").lower()
        self._lang = lang

        # --- Header ---
        header = tk.Frame(self, bg="#0f172a")
        header.pack(fill="x", padx=20, pady=(16, 6))
        tk.Label(
            header,
            text=self.t.get("app_title", "Win11 Magic Upgrade"),
            font=("Segoe UI Semibold", 20),
            fg="#38bdf8",
            bg="#0f172a",
        ).pack(anchor="w")
        admin_txt = (
            self.t.get("admin_ok", "Administrator — ready")
            if is_admin()
            else self.t.get("admin_need", "Will auto-elevate on One-Click")
        )
        tk.Label(
            header,
            text=self.t.get("app_sub", "") + "  ·  " + admin_txt,
            font=("Segoe UI", 10),
            fg="#94a3b8",
            bg="#0f172a",
        ).pack(anchor="w")
        tk.Label(
            header,
            text=self.t.get(
                "app_publisher",
                "Publisher: dlnraja · https://github.com/dlnraja/win11-magic-upgrade",
            ),
            font=("Segoe UI", 9),
            fg="#64748b",
            bg="#0f172a",
        ).pack(anchor="w", pady=(2, 0))

        # --- One-Click hero ---
        hero = tk.Frame(self, bg="#0f172a")
        hero.pack(fill="x", padx=20, pady=(8, 4))
        self.btn_go = tk.Button(
            hero,
            text=self.t.get("btn_oneclick", "ONE-CLICK — Full migration"),
            font=("Segoe UI Semibold", 14),
            bg="#0284c7",
            fg="white",
            activebackground="#0369a1",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=18,
            pady=14,
            command=self._startup_button,
        )
        self.btn_go.pack(fill="x")
        self._action_buttons.append(self.btn_go)
        self._startup_cancel_btn = tk.Button(
            hero,
            text="Cancel",
            font=("Segoe UI", 11),
            bg="#7f1d1d",
            fg="white",
            activebackground="#991b1b",
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=8,
            command=self._startup_cancel,
        )

        # --- Secondary actions ---
        btns = tk.Frame(self, bg="#0f172a")
        btns.pack(fill="x", padx=20, pady=(6, 4))
        secondary = [
            ("btn_diagnose", "Diagnose", "diagnose", "#1e293b", "#e2e8f0"),
            ("btn_bypass", "Bypass", "bypass", "#1e293b", "#e2e8f0"),
            ("btn_mbr", "MBR→GPT", "mbr", "#1e293b", "#e2e8f0"),
            ("btn_srp", "ESP/SRP", "srp", "#1e293b", "#e2e8f0"),
            ("btn_boot_fix", "Boot Fix", "boot-fix", "#9a3412", "#ffedd5"),
            ("btn_patch", "Patch", "patch", "#334155", "#e2e8f0"),
            ("btn_install_patches", "Preventives", "install-patches", "#14532d", "#e2e8f0"),
            ("btn_frameworks", "Frameworks", "frameworks", "#7c2d12", "#ffedd5"),
            ("btn_dryrun", "Dry-run", "dryrun", "#422006", "#fde68a"),
            ("btn_support_zip", "Support ZIP", "support-zip", "#1e293b", "#e2e8f0"),
        ]
        for key, fallback, action, bg, fg in secondary:
            b = tk.Button(
                btns,
                text=self.t.get(key, fallback),
                font=("Segoe UI", 9),
                bg=bg,
                fg=fg,
                activebackground=bg,
                relief="flat",
                cursor="hand2",
                padx=8,
                pady=6,
                command=lambda a=action: self.start(a),
            )
            b.pack(side="left", padx=(0, 6), pady=2)
            self._action_buttons.append(b)

        self.chain_var = tk.StringVar(value=self.t.get("chain_idle", "Chain: (run Diagnose / One-Click)"))
        self.conf_var = tk.StringVar(value="")
        status_row = tk.Frame(self, bg="#0f172a")
        status_row.pack(fill="x", padx=20, pady=(2, 4))
        tk.Label(
            status_row,
            textvariable=self.chain_var,
            font=("Segoe UI", 9),
            fg="#7dd3fc",
            bg="#0f172a",
            wraplength=900,
            justify="left",
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            status_row,
            textvariable=self.conf_var,
            font=("Segoe UI", 9),
            fg="#a3e635",
            bg="#0f172a",
            anchor="w",
        ).pack(anchor="w")

        # --- Options above the log, with a draggable sash between them ---
        self._paned = tk.PanedWindow(
            self,
            orient=tk.VERTICAL,
            sashwidth=8,
            sashrelief="raised",
            bg="#334155",
            opaqueresize=True,
            bd=0,
        )
        self._paned.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        top = tk.Frame(self._paned, bg="#0f172a")
        bottom = tk.Frame(self._paned, bg="#0f172a")
        self._paned.add(top, stretch="always", minsize=160)
        self._paned.add(bottom, stretch="always", minsize=140)
        self._build_options_panel(top)

        # --- Progress panel ---
        prog = tk.Frame(top, bg="#0f172a")
        prog.pack(fill="x", padx=20, pady=(4, 6))
        self.phase_var = tk.StringVar(value=self.t.get("progress_idle", "Idle"))
        self.detail_var = tk.StringVar(value="")
        self.pct_var = tk.StringVar(value="0%")
        self.step_pct_var = tk.StringVar(value="")
        self.eta_var = tk.StringVar(value="ETA --:--")
        self.elapsed_var = tk.StringVar(value="Elapsed 00:00")
        self.alive_var = tk.StringVar(value="")
        tk.Label(
            prog,
            textvariable=self.phase_var,
            font=("Segoe UI Semibold", 10),
            fg="#e2e8f0",
            bg="#0f172a",
            anchor="w",
        ).pack(fill="x")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Magic.Horizontal.TProgressbar",
            troughcolor="#1e293b",
            background="#38bdf8",
            bordercolor="#0f172a",
            lightcolor="#38bdf8",
            darkcolor="#0284c7",
            thickness=14,
        )
        style.configure(
            "MagicStep.Horizontal.TProgressbar",
            troughcolor="#1e293b",
            background="#4ade80",
            bordercolor="#0f172a",
            lightcolor="#4ade80",
            darkcolor="#16a34a",
            thickness=10,
        )
        style.configure(
            "Magic.TCheckbutton",
            background="#111827",
            foreground="#e2e8f0",
            focuscolor="#111827",
        )
        style.map("Magic.TCheckbutton", background=[("active", "#1e293b")])
        tk.Label(
            prog,
            text=self.t.get("progress_overall", "Overall"),
            font=("Segoe UI", 8),
            fg="#64748b",
            bg="#0f172a",
            anchor="w",
        ).pack(fill="x")
        self.bar = ttk.Progressbar(
            prog,
            style="Magic.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self.bar.pack(fill="x", pady=(0, 2))
        tk.Label(
            prog,
            text=self.t.get("progress_step", "Current step"),
            font=("Segoe UI", 8),
            fg="#64748b",
            bg="#0f172a",
            anchor="w",
        ).pack(fill="x")
        self.step_bar = ttk.Progressbar(
            prog,
            style="MagicStep.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self.step_bar.pack(fill="x", pady=(0, 2))
        meta = tk.Frame(prog, bg="#0f172a")
        meta.pack(fill="x")
        tk.Label(
            meta,
            textvariable=self.detail_var,
            font=("Segoe UI", 9),
            fg="#94a3b8",
            bg="#0f172a",
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        right = tk.Frame(meta, bg="#0f172a")
        right.pack(side="right")
        tk.Label(right, textvariable=self.alive_var, font=("Consolas", 9), fg="#4ade80", bg="#0f172a").pack(
            side="right", padx=(6, 0)
        )
        tk.Label(right, textvariable=self.pct_var, font=("Consolas", 9), fg="#38bdf8", bg="#0f172a").pack(
            side="right", padx=(6, 0)
        )
        tk.Label(right, textvariable=self.step_pct_var, font=("Consolas", 9), fg="#86efac", bg="#0f172a").pack(
            side="right", padx=(6, 0)
        )
        tk.Label(right, textvariable=self.eta_var, font=("Consolas", 9), fg="#7dd3fc", bg="#0f172a").pack(
            side="right", padx=(6, 0)
        )
        tk.Label(right, textvariable=self.elapsed_var, font=("Consolas", 9), fg="#94a3b8", bg="#0f172a").pack(
            side="right", padx=(6, 0)
        )
        self._progress_job = None
        self._last_progress: dict = {}

        tools = tk.Frame(bottom, bg="#0f172a")
        tools.pack(fill="x", padx=8, pady=(4, 2))
        for label, command in (
            ("Open log in large window", self._open_log_window),
            ("Open log folder", self._open_log_folder),
        ):
            tk.Button(
                tools,
                text=label,
                font=("Segoe UI", 9),
                bg="#1e293b",
                fg="#e2e8f0",
                activebackground="#334155",
                relief="flat",
                cursor="hand2",
                padx=8,
                pady=4,
                command=command,
            ).pack(side="left", padx=(0, 6))
        self._log_viewers: list = []
        self.log = scrolledtext.ScrolledText(
            bottom,
            font=("Consolas", 9),
            bg="#020617",
            fg="#cbd5e1",
            insertbackground="#cbd5e1",
            relief="flat",
            height=12,
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._layout_file = self._gui_layout_file()
        self._paned.bind("<ButtonRelease-1>", self._remember_sash)
        self.bind("<Configure>", self._remember_sash_later)
        self.after(250, self._restore_sash)
        self.append(f"Root: {self.root_dir}\n")
        self.append("GUI: check options below, then ONE-CLICK. Engine is pure Python.\n")
        if is_admin():
            self.append("Running as Administrator.\n")
        self.append(self.t.get("ready", "Ready.") + "\n")
        self._count_alive = False
        self._count_job = None
        if not self._auto_action:
            from engine.win11_inplace_policy import startup_countdown_seconds  # type: ignore

            self._count_left = startup_countdown_seconds()
            self._count_alive = True
            self._startup_cancel_btn.pack(fill="x", pady=(6, 0))
            self._paint_startup_button()
            self._count_job = self.after(1000, self._startup_tick)
        else:
            self.btn_go.configure(command=lambda: self.start("oneclick"))

        if self._auto_action:
            self.after(350, lambda: self.start(self._auto_action or "oneclick"))

    def _build_options_panel(self, parent: tk.Misc) -> None:
        from gui_panel import (  # type: ignore
            OPTIONS,
            apply_options_to_environ,
            group_label,
            load_option_values,
            option_label,
            save_option_values,
            snapshot_for_log,
        )

        self._gui_panel = {
            "apply": apply_options_to_environ,
            "save": save_option_values,
            "snapshot": snapshot_for_log,
            "OPTIONS": OPTIONS,
        }
        saved = load_option_values()
        wrap = tk.LabelFrame(
            parent,
            text=self.t.get("opts_title", "Options (check / uncheck)"),
            font=("Segoe UI Semibold", 10),
            fg="#e2e8f0",
            bg="#111827",
            bd=1,
            relief="groove",
            labelanchor="nw",
        )
        wrap.pack(fill="x", padx=20, pady=(4, 6))

        body = tk.Frame(wrap, bg="#111827")
        body.pack(fill="x", padx=8, pady=6)
        cols = {
            "migration": tk.Frame(body, bg="#111827"),
            "frameworks": tk.Frame(body, bg="#111827"),
        }
        cols["migration"].pack(side="left", fill="both", expand=True, padx=(0, 8))
        cols["frameworks"].pack(side="left", fill="both", expand=True)

        for group, frame in cols.items():
            tk.Label(
                frame,
                text=group_label(group, self._lang),
                font=("Segoe UI Semibold", 9),
                fg="#38bdf8",
                bg="#111827",
                anchor="w",
            ).pack(fill="x", pady=(0, 4))

        for opt in OPTIONS:
            var = tk.BooleanVar(value=bool(saved.get(opt.key, opt.default)))
            self._opt_vars[opt.key] = var
            parent = cols.get(opt.group, cols["migration"])
            cb = ttk.Checkbutton(
                parent,
                text=option_label(opt, self._lang),
                variable=var,
                style="Magic.TCheckbutton",
                command=self._on_option_changed,
            )
            cb.pack(anchor="w", pady=1)

        tip = tk.Label(
            wrap,
            text=self.t.get(
                "opts_tip",
                "Checked options are applied on One-Click / Frameworks. Preferences are saved automatically.",
            ),
            font=("Segoe UI", 8),
            fg="#64748b",
            bg="#111827",
            wraplength=920,
            justify="left",
            anchor="w",
        )
        tip.pack(fill="x", padx=8, pady=(0, 6))

    def _option_values(self) -> dict:
        return {k: bool(v.get()) for k, v in self._opt_vars.items()}

    def _on_option_changed(self) -> None:
        try:
            self._gui_panel["save"](self._option_values())
        except Exception:
            pass

    def _apply_gui_options(self) -> None:
        vals = self._option_values()
        try:
            self._gui_panel["save"](vals)
            self._gui_panel["apply"](vals)
            self.append(self._gui_panel["snapshot"](vals) + "\n")
        except Exception as e:
            self.append(f"Options apply: {e}\n")

    def append(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")
        for viewer in list(getattr(self, "_log_viewers", ())):
            try:
                viewer.add_text(text)
            except Exception:
                try:
                    self._log_viewers.remove(viewer)
                except ValueError:
                    pass

    def _gui_layout_file(self) -> Path:
        from gui_log import layout_file

        base = Path(os.environ.get("LOCALAPPDATA", ".")) / "Win11MagicUpgrade"
        return layout_file(base)

    def _remember_sash_later(self, _event: object | None = None) -> None:
        if getattr(self, "_sash_job", None):
            return
        self._sash_job = self.after(400, self._remember_sash)

    def _remember_sash(self, _event: object | None = None) -> None:
        self._sash_job = None
        try:
            _x, pos = self._paned.sash_coord(0)
        except Exception:
            return
        from gui_log import clamp_sash, save_sash

        total = max(self._paned.winfo_height(), 1)
        if total < 200:
            return
        save_sash(self._layout_file, clamp_sash(pos, total))

    def _restore_sash(self) -> None:
        from gui_log import clamp_sash, load_sash

        total = max(self._paned.winfo_height(), 1)
        pos = clamp_sash(load_sash(self._layout_file), total)
        try:
            self._paned.sash_place(0, 1, pos)
        except Exception:
            pass

    def _open_log_folder(self) -> None:
        from gui_log import log_folder

        folder = log_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(folder)  # type: ignore[attr-defined]
            else:
                self.append(f"Log folder: {folder}\n")
        except OSError as exc:
            self.append(f"Could not open log folder: {exc}\n")

    def _open_log_window(self) -> None:
        LogWindow(self)

    def _apply_progress_ui(self, info: dict) -> None:
        from engine.progress import format_elapsed, format_eta  # type: ignore

        self._last_progress = dict(info)
        phase = info.get("phase") or ""
        detail = info.get("detail") or ""
        overall = info.get("percent")
        step = info.get("step_percent")
        eta = info.get("eta_seconds")
        elapsed = info.get("elapsed_seconds")
        indeterminate = bool(info.get("indeterminate"))
        alive = info.get("alive") or ""
        stale = float(info.get("stale_seconds") or 0)

        if phase:
            self.phase_var.set(phase)
        # Show working pulse when stale so user knows process is alive
        suffix = ""
        if self._busy and stale > 2.5:
            suffix = f"  (working{alive})"
        self.detail_var.set((detail or "") + suffix)

        if overall is not None:
            val = max(0.0, min(100.0, float(overall)))
            if str(self.bar["mode"]) != "determinate":
                try:
                    self.bar.stop()
                except Exception:
                    pass
                self.bar.configure(mode="determinate")
            self.bar["value"] = val
            self.pct_var.set(f"{val:.0f}%")

        if indeterminate and (step is None or float(step or 0) <= 0):
            if str(self.step_bar["mode"]) != "indeterminate":
                self.step_bar.configure(mode="indeterminate")
                self.step_bar.start(14)
            self.step_pct_var.set("…")
        else:
            if str(self.step_bar["mode"]) != "determinate":
                try:
                    self.step_bar.stop()
                except Exception:
                    pass
                self.step_bar.configure(mode="determinate")
            svaluable = 35.0 if step is None and indeterminate else float(step or 0)
            svaluable = max(0.0, min(100.0, svaluable))
            self.step_bar["value"] = svaluable
            self.step_pct_var.set(f"step {svaluable:.0f}%")

        if elapsed is not None:
            self.elapsed_var.set(f"Elapsed {format_elapsed(float(elapsed))}")
        if eta is not None:
            self.eta_var.set(f"Left {format_eta(float(eta))}")
        elif self._busy:
            self.eta_var.set("Left --:--")
        self.alive_var.set(alive if self._busy else "")

    def _on_progress(self, info: dict) -> None:
        self.after(0, self._apply_progress_ui, info)

    def _poll_progress(self) -> None:
        if not self._busy:
            self._progress_job = None
            return
        try:
            from engine.progress import heartbeat, snapshot  # type: ignore

            # Soft heartbeat even when engine is quiet (long sc/stop etc.)
            heartbeat()
            self._apply_progress_ui(snapshot())
        except Exception:
            pass
        self._progress_job = self.after(500, self._poll_progress)

    def _paint_startup_button(self) -> None:
        self.btn_go.configure(
            text=f"Start boot check and official update ({self._count_left})",
            command=self._startup_now,
        )

    def _startup_tick(self) -> None:
        from engine.win11_inplace_policy import next_countdown  # type: ignore

        if not self._count_alive:
            return
        self._count_left, action = next_countdown(self._count_left)
        if action == "run":
            self._begin_startup_chain()
            return
        self._paint_startup_button()
        self._count_job = self.after(1000, self._startup_tick)

    def _startup_button(self) -> None:
        self.start("oneclick")

    def _startup_now(self) -> None:
        self._count_alive = False
        if self._count_job is not None:
            try:
                self.after_cancel(self._count_job)
            except Exception:
                pass
            self._count_job = None
        self._begin_startup_chain()

    def _startup_cancel(self) -> None:
        if not self._count_alive:
            return
        self._count_alive = False
        if self._count_job is not None:
            try:
                self.after_cancel(self._count_job)
            except Exception:
                pass
            self._count_job = None
        self.btn_go.configure(text="Start boot check and official update", command=self._startup_now)
        try:
            self._startup_cancel_btn.pack_forget()
        except Exception:
            pass
        self.append("Startup countdown cancelled.\n")

    def _begin_startup_chain(self) -> None:
        self._count_alive = False
        try:
            self._startup_cancel_btn.pack_forget()
        except Exception:
            pass
        self.btn_go.configure(state="disabled", text="Boot check and official update running")
        self._noargs_boot_policy()

    def _noargs_boot_policy(self) -> None:
        """Boot guard, repair, then the official update only on a clean GO."""
        if self._busy or self._auto_action:
            return
        self.append("Startup: boot guard (MBR/EFI/BCD/ESP) before any update.\n")

        def work() -> None:
            try:
                from engine.detect import collect_report  # type: ignore
                from engine.win11_inplace_policy import decide_noargs_startup, decide_oneclick  # type: ignore
                from engine.win11_release import floor_target, refresh_latest_win11  # type: ignore
                from gui_panel import apply_options_to_environ, load_option_values  # type: ignore

                apply_options_to_environ(load_option_values())
                opt_out = os.environ.get("MAGIC_SKIP_STARTUP_UPDATE", "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                )
                report = collect_report()
                guard = self._startup_guard()
                repaired = False
                if guard.get("verdict") != "GO" or int(guard.get("errors") or 0) != 0:
                    repaired = True
                    guard = self._startup_repair(guard)
                try:
                    target = refresh_latest_win11(host_build=int(getattr(report, "build", 0) or 0))
                except Exception:
                    target = floor_target()
                plan = decide_oneclick(
                    build=int(getattr(report, "build", 0) or 0),
                    ubr=int(getattr(report, "ubr", 0) or 0),
                    display_version=str(getattr(report, "display_version", "") or ""),
                    target_version=str(getattr(target, "version", "") or ""),
                    target_build=int(getattr(target, "major", 0) or 0),
                    target_ubr=int(getattr(target, "ubr", 0) or 0),
                )
                decision = decide_noargs_startup(
                    is_win11=bool(getattr(report, "is_win11", False)),
                    guard_go=guard.get("verdict") == "GO",
                    issues=list(guard.get("issues") or []),
                    opt_out=opt_out,
                    update_path=plan.path,
                    repaired=repaired,
                    error_count=int(guard.get("errors") or 0),
                )
            except Exception as exc:
                self.after(0, lambda: self.append(f"Startup boot policy skipped: {exc}\n"))
                return
            self.after(0, lambda: self._present_startup(decision))

        threading.Thread(target=work, daemon=True).start()

    def _startup_guard(self) -> dict:
        try:
            from engine.boot_safe import postflight_boot_edit  # type: ignore

            post = postflight_boot_edit()
            issues = [str(item) for item in (post.get("issues") or []) if str(item)]
            clean = bool(post.get("ok")) and not issues and post.get("verdict", "GO") != "NO-GO"
            return {"verdict": "GO" if clean else "NO-GO", "issues": issues, "errors": 0 if clean else max(1, len(issues))}
        except Exception as exc:
            return {"verdict": "NO-GO", "issues": [type(exc).__name__]}

    def _startup_repair(self, guard: dict) -> dict:
        self.after(0, lambda: self.append("Boot guard is not GO. Repairing before any update.\n"))
        try:
            from engine.boot_guard import verify_and_heal_boot  # type: ignore

            verify_and_heal_boot(force=True)
        except Exception as exc:
            self.after(0, lambda e=exc: self.append(f"Startup repair skipped: {e}\n"))
            return guard
        return self._startup_guard()

    def _present_startup(self, decision) -> None:
        self.append(decision.reason + "\n")
        if decision.action == "official_update":
            self.append("Boot guard is GO. Starting the official in-place update.\n")
            self.btn_go.configure(state="normal", command=lambda: self.start("oneclick"))
            self.start("oneclick")
            return
        if decision.action in ("repair", "nogo"):
            self.append("Update stays off until every boot verification passes with zero errors.\n")

    def start(self, action: str) -> None:
        if self._busy or self._elevating:
            return
        try:
            from engine.pre_reboot import guard_blocks_action, last_boot_guard, verdict_message  # type: ignore

            prior = last_boot_guard()
            if prior and guard_blocks_action(action, str(prior.get("verdict") or "")):
                notice = verdict_message(str(prior.get("verdict") or ""), str(prior.get("bundle") or ""))
                self.append(notice + "\n")
                messagebox.showerror(self.t.get("app_title", "Win11 Magic Upgrade"), notice)
                return
        except Exception:
            pass
        title = self.t.get("app_title", "Win11 Magic Upgrade")
        # Persist + apply checkbox options before elevate / run
        try:
            self._apply_gui_options()
        except Exception as e:
            self.append(f"Options: {e}\n")

        # Elevate INTO GUI (not invisible --cli) so progress bars stay visible
        if action not in ("diagnose", "dryrun", "support-zip") and not is_admin():
            self._elevating = True
            self.append(self.t.get("elevating", "Elevation required — relaunching as Administrator...\n"))
            self.phase_var.set(self.t.get("elevating_phase", "Waiting for UAC…"))
            self.detail_var.set(self.t.get("elevating_detail", "Accept the Windows security prompt"))
            self.step_bar.configure(mode="indeterminate")
            self.step_bar.start(12)
            ok = relaunch_as_admin(["--auto", action])
            if ok:
                self.append(self.t.get("elevated_ok", "Elevated window starting — closing this one.\n"))
                self.after(500, self.destroy)
            else:
                self._elevating = False
                try:
                    self.step_bar.stop()
                except Exception:
                    pass
                self.step_bar.configure(mode="determinate", value=0)
                self.phase_var.set(self.t.get("progress_idle", "Idle"))
                self.append(self.t.get("elevated_fail", "UAC cancelled or elevation failed.\n"))
                messagebox.showerror(
                    title,
                    self.t.get(
                        "elevated_fail",
                        "UAC cancelled or elevation failed. Antivirus may be blocking elevation.",
                    ),
                )
            return

        if action == "oneclick" and os.environ.get("MAGIC_CONFIRM", "").strip() == "1":
            if not messagebox.askyesno(title, self.t.get("confirm_upgrade", "Continue?")):
                return

        # UIA / automation / AI-agent guard (defense-in-depth)
        if action in (
            "oneclick",
            "mbr",
            "srp",
            "boot-fix",
            "bypass",
            "install-patches",
            "dryrun",
            "frameworks",
        ):
            try:
                from engine.uia_guard import require_human_confirm  # type: ignore

                ok_human = require_human_confirm(
                    lambda t, m: messagebox.askyesno(t, m),
                    title=title,
                    message=self.t.get(
                        "confirm_uia",
                        "Confirm this upgrade action is started by you (not an automated / AI agent).",
                    ),
                    action=action,
                )
                if not ok_human:
                    self.append(self.t.get("uia_blocked", "Blocked by UIA/automation guard.\n"))
                    return
            except Exception as e:
                self.append(f"UIA guard skip: {e}\n")

        self._busy = True
        for b in self._action_buttons:
            try:
                b.configure(state="disabled")
            except Exception:
                pass
        self.phase_var.set(self.t.get("progress_running", "Running…"))
        self.detail_var.set(self.t.get("progress_starting", "Starting…"))
        self.pct_var.set("0%")
        self.step_pct_var.set("…")
        self.eta_var.set("Left --:--")
        self.elapsed_var.set("Elapsed 00:00")
        self.bar.configure(mode="determinate", value=0)
        self.step_bar.configure(mode="indeterminate")
        self.step_bar.start(14)
        self._poll_progress()

        def worker() -> None:
            ok = False
            try:
                from engine import (  # type: ignore
                    apply_bypass_only,
                    convert_mbr_only,
                    fix_system_reserved_only,
                    install_preventive_only,
                    run_diagnose,
                    run_patch_enrichment,
                    run_pipeline,
                )
                from engine.progress import set_progress_callback, start_session  # type: ignore

                set_progress_callback(self._on_progress)
                start_session(action)
                sink = lambda s: self.after(0, self.append, s)
                if action == "diagnose":
                    run_diagnose(sink)
                    code = 0
                elif action == "bypass":
                    apply_bypass_only(sink)
                    code = 0
                elif action == "mbr":
                    convert_mbr_only(sink)
                    code = 0
                elif action == "srp":
                    fix_system_reserved_only(sink)
                    code = 0
                elif action == "boot-fix":
                    from engine.boot_guard import arm_boot_safety_net, verify_and_heal_boot  # type: ignore
                    from engine.boot_remediation import run_boot_auto_remediate  # type: ignore
                    from engine.detect import collect_report  # type: ignore

                    arm_boot_safety_net(before_setup=False)
                    bot = run_boot_auto_remediate(
                        collect_report(),
                        force_esp=True,
                        try_mbr2gpt=False,
                        try_hybrid=True,
                        sink=sink,
                    )
                    heal = verify_and_heal_boot(force=True)
                    sink(
                        f"Boot Fix: ok={bot.get('ok')} bootable={bot.get('bootable')} "
                        f"heal={heal.get('ok')} grub={((heal.get('grub') or {}).get('vendors'))} "
                        f"scripts={bot.get('scripts', {}).get('guide', '')}\n"
                    )
                    code = 0 if bot.get("ok") or bot.get("bootable") or heal.get("ok") else 2
                elif action == "patch":
                    run_patch_enrichment(sink, deep_heal=False)
                    code = 0
                elif action == "install-patches":
                    install_preventive_only(sink)
                    code = 0
                elif action == "frameworks":
                    from engine.frameworks import install_frameworks  # type: ignore

                    # Force selected (or defaults) even if master was the only switch
                    r = install_frameworks(force_all_defaults=True)
                    sink(f"Frameworks: ok={r.get('ok')} failed={r.get('failed')}\n")
                    code = 0 if not r.get("failed") else 1
                elif action == "support-zip":
                    from engine.support_zip import export_support_zip  # type: ignore

                    z = export_support_zip()
                    sink(f"Support ZIP: {z}\n")
                    code = 0
                elif action == "dryrun":
                    code = run_pipeline(sink, quiet=True, dry_run=True)
                else:
                    code = None
                    if action == "oneclick":
                        from engine.pre_reboot import (  # type: ignore
                            default_uefi,
                            guard_blocks_action,
                            last_boot_guard,
                            open_repair_issue,
                            repair_before_reboot,
                            upgrade_allowed,
                            verdict_message,
                        )

                        repair = last_boot_guard()
                        if repair is None:
                            repair = repair_before_reboot(uefi=default_uefi())
                            open_repair_issue(repair)
                        verdict = str(repair.get("verdict") or "NO-GO")
                        summary = str(repair.get("summary_text") or verdict)
                        bundle = str(repair.get("bundle") or "")
                        sink(summary + "\n")
                        sink(verdict_message(verdict, bundle) + "\n")
                        notice = verdict_message(verdict, bundle)
                        sink(notice + "\n")
                        try:
                            issue = open_repair_issue(repair)
                            sink(f"GitHub: {issue}\n")
                        except Exception as issue_error:
                            sink(f"GitHub issue skipped: {issue_error}\n")
                        if guard_blocks_action("oneclick", verdict):
                            code = 2
                    if code is None:
                        code = run_pipeline(
                            sink,
                            quiet=True,
                            resume=getattr(self, "_resume_chain", False),
                        )
                try:
                    from engine.logutil import load_state  # type: ignore

                    st = load_state()
                    chain = st.get("ChainLabel") or ""
                    conf = st.get("Confidence") or {}
                    if chain:
                        self.after(0, self.chain_var.set, f"Chain: {chain}")
                    if conf:
                        self.after(
                            0,
                            self.conf_var.set,
                            f"Confidence: {conf.get('score', '?')}/100 ({conf.get('level', '?')})",
                        )
                except Exception:
                    pass
                self.after(0, self.append, f"\n--- exit {code} ---\n")
                SETUP_OK = {0, 3010, 3011}
                try:
                    from engine.errors import EXIT_BLOCKED, EXIT_FAILED  # type: ignore
                except Exception:
                    EXIT_BLOCKED, EXIT_FAILED = 2, 3
                if code == 3010:
                    msg = self.t.get(
                        "done_reboot",
                        "Reboot scheduled. Chain resumes automatically (RunOnce).",
                    )
                    self.after(0, lambda: messagebox.showinfo(title, msg))
                elif action == "oneclick" and code in SETUP_OK:
                    msg = self.t.get(
                        "done_setup",
                        "Windows Setup launched. PC will reboot; upgrade continues automatically.",
                    )
                    self.after(0, lambda: messagebox.showinfo(title, msg))
                elif code in (EXIT_BLOCKED, EXIT_FAILED):
                    kind, detail = _failure_detail(code=code)
                    body = _format_user_error(self.t, kind, detail)
                    self.after(0, self.append, body + "\n")
                    self.after(0, lambda b=body: messagebox.showerror(title, b))
                elif code == 0:
                    self.after(
                        0,
                        lambda: messagebox.showinfo(title, self.t.get("done_ok", "Done")),
                    )
                else:
                    msg = self.t.get("done_warn", "Code {code}").replace("{code}", str(code))
                    self.after(0, lambda: messagebox.showwarning(title, msg))
                ok = code in SETUP_OK or code == 0
            except Exception as ex:
                hint = ""
                try:
                    from engine.errors import UpgradeBlockedError, remember_failure  # type: ignore
                    from engine.gh_report import report_unhandled_exception  # type: ignore

                    if isinstance(ex, UpgradeBlockedError):
                        remember_failure(str(ex), kind=ex.kind, links=ex.links)
                        if ex.links.get("issue"):
                            hint = f"\n\nGitHub: {ex.links['issue']}"
                    elif "Issue:" not in str(ex):
                        links = report_unhandled_exception(
                            type(ex),
                            ex,
                            ex.__traceback__,
                            kind=f"gui-{action}-exception",
                        )
                        if links.get("issue"):
                            hint = f"\n\nGitHub issue (sanitized): {links['issue']}"
                        elif links.get("local_md"):
                            hint = "\n\nSanitized autodiag saved locally."
                except Exception:
                    pass
                kind, detail = _failure_detail(ex)
                body = _format_user_error(self.t, kind, (detail or str(ex)) + hint)
                self.after(0, self.append, f"\nERROR:\n{body}\n")
                self.after(0, lambda b=body: messagebox.showerror(title, b))
            finally:
                try:
                    from engine.progress import end_session, set_progress_callback  # type: ignore

                    end_session(success=ok)
                    set_progress_callback(None)
                except Exception:
                    pass
                self.after(0, self._done)

        threading.Thread(target=worker, daemon=True).start()

    def _done(self) -> None:
        self._busy = False
        for b in getattr(self, "_action_buttons", []) or [self.btn_go]:
            try:
                b.configure(state="normal")
            except Exception:
                pass
        if self._progress_job is not None:
            try:
                self.after_cancel(self._progress_job)
            except Exception:
                pass
            self._progress_job = None
        try:
            self.step_bar.stop()
        except Exception:
            pass
        self.step_bar.configure(mode="determinate", value=100)
        self.bar.configure(mode="determinate")
        if float(self.bar["value"] or 0) < 100:
            self.bar["value"] = 100
        self.pct_var.set("100%")
        self.step_pct_var.set("step 100%")
        self.eta_var.set("Left 00:00")
        self.alive_var.set("")
        self.phase_var.set(self.t.get("progress_done", "Finished"))


def run_console_startup() -> int:
    """No-args console path: boot guard, repair, then the official update only on GO."""
    from engine.detect import collect_report  # type: ignore
    from engine.win11_inplace_policy import decide_noargs_startup, decide_oneclick  # type: ignore
    from engine.win11_release import floor_target, refresh_latest_win11  # type: ignore

    print("Startup: boot guard (MBR/EFI/BCD/ESP) before any update.")
    report = collect_report()
    guard = _guard_snapshot()
    repaired = False
    if guard.get("verdict") != "GO" or int(guard.get("errors") or 0) != 0:
        repaired = True
        print("Boot guard is not GO. Repairing before any update.")
        try:
            from engine.boot_guard import verify_and_heal_boot  # type: ignore

            verify_and_heal_boot(force=True)
        except Exception as exc:
            print(f"Startup repair skipped: {exc}")
        guard = _guard_snapshot()
    try:
        target = refresh_latest_win11(host_build=int(getattr(report, "build", 0) or 0))
    except Exception:
        target = floor_target()
    plan = decide_oneclick(
        build=int(getattr(report, "build", 0) or 0),
        ubr=int(getattr(report, "ubr", 0) or 0),
        display_version=str(getattr(report, "display_version", "") or ""),
        target_version=str(getattr(target, "version", "") or ""),
        target_build=int(getattr(target, "major", 0) or 0),
        target_ubr=int(getattr(target, "ubr", 0) or 0),
    )
    opt_out = os.environ.get("MAGIC_SKIP_STARTUP_UPDATE", "").strip().lower() in ("1", "true", "yes")
    decision = decide_noargs_startup(
        is_win11=bool(getattr(report, "is_win11", False)),
        guard_go=guard.get("verdict") == "GO",
        issues=list(guard.get("issues") or []),
        opt_out=opt_out,
        update_path=plan.path,
        repaired=repaired,
        error_count=int(guard.get("errors") or 0),
    )
    print(decision.reason)
    if decision.action != "official_update":
        return 0 if decision.action in ("health", "idle") else 2
    return int(run_pipeline())


def _guard_snapshot() -> dict:
    try:
        from engine.boot_safe import postflight_boot_edit  # type: ignore

        post = postflight_boot_edit()
        issues = [str(item) for item in (post.get("issues") or []) if str(item)]
        clean = bool(post.get("ok")) and not issues and post.get("verdict", "GO") != "NO-GO"
        return {"verdict": "GO" if clean else "NO-GO", "issues": issues, "errors": 0 if clean else max(1, len(issues))}
    except Exception as exc:
        return {"verdict": "NO-GO", "issues": [type(exc).__name__], "errors": 1}


def console_countdown(seconds: int, *, wait, key_pressed) -> bool:
    """Print a countdown. Return False when a key cancels it."""
    from engine.win11_inplace_policy import next_countdown

    print(f"Boot check and official update start in {seconds} s. Press a key to cancel.", flush=True)
    left = seconds
    while True:
        print(f"\r{left} s remaining. Press a key to cancel.", end="", flush=True)
        if key_pressed(wait):
            print("\nStartup countdown cancelled.")
            return False
        left, action = next_countdown(left)
        if action == "run":
            print("\nStarting boot check.")
            return True


def _console_key_pressed(wait) -> bool:
    import select

    wait(0.1)
    for _ in range(9):
        if os.name == "nt":
            import msvcrt

            if msvcrt.kbhit():
                msvcrt.getwch()
                return True
        else:
            if not stdio_safe.stream_isatty(sys.stdin):
                return False
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if ready:
                sys.stdin.read(1)
                return True
        wait(0.1)
    return False


class LogWindow(tk.Toplevel):
    """Resizable live view of the session log, with search and copy."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.title("Win11 Magic Upgrade — log")
        self.geometry("900x640")
        self.minsize(640, 400)
        self.resizable(True, True)
        self.configure(bg="#0f172a")
        self._app = app
        self._file_pos = 0
        self._search_from = "1.0"
        bar = tk.Frame(self, bg="#0f172a")
        bar.pack(fill="x", padx=10, pady=8)
        self._query = tk.StringVar()
        tk.Entry(bar, textvariable=self._query, font=("Segoe UI", 10), width=32).pack(side="left")
        tk.Button(bar, text="Search", command=self._search, relief="flat", bg="#1e293b", fg="#e2e8f0").pack(
            side="left", padx=6
        )
        tk.Button(bar, text="Copy", command=self._copy, relief="flat", bg="#1e293b", fg="#e2e8f0").pack(side="left")
        self.text = scrolledtext.ScrolledText(
            self,
            font=("Consolas", 10),
            bg="#020617",
            fg="#e2e8f0",
            insertbackground="#e2e8f0",
            relief="flat",
        )
        self.text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.text.insert("1.0", app.log.get("1.0", "end"))
        self.text.see("end")
        app._log_viewers.append(self)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(500, self._tick)

    def add_text(self, text: str) -> None:
        self.text.insert("end", text)
        self.text.see("end")

    def _tick(self) -> None:
        from gui_log import log_folder, newest_log

        path = newest_log(log_folder())
        if path is not None:
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            if len(data) < self._file_pos:
                self._file_pos = 0
            if len(data) > self._file_pos:
                chunk = data[self._file_pos :].decode("utf-8", "replace")
                self._file_pos = len(data)
                tail = self.text.get("end-1500c", "end")
                if chunk and chunk not in tail:
                    self.add_text(chunk if chunk.endswith("\n") else chunk + "\n")
        if self.winfo_exists():
            self.after(700, self._tick)

    def _search(self) -> None:
        needle = self._query.get()
        if not needle:
            return
        start = self.text.search(needle, self._search_from, nocase=True, stopindex="end")
        if not start:
            start = self.text.search(needle, "1.0", nocase=True, stopindex="end")
        if not start:
            return
        end = f"{start}+{len(needle)}c"
        self.text.tag_remove("sel", "1.0", "end")
        self.text.tag_add("sel", start, end)
        self.text.see(start)
        self._search_from = end

    def _copy(self) -> None:
        try:
            payload = self.text.get("sel.first", "sel.last")
        except tk.TclError:
            payload = self.text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(payload)

    def _close(self) -> None:
        try:
            self._app._log_viewers.remove(self)
        except ValueError:
            pass
        self.destroy()


def _parse_auto_action(argv: list[str]) -> str | None:
    lower = [a.lower() for a in argv]
    if "--auto" in lower:
        i = lower.index("--auto")
        if i + 1 < len(argv):
            return argv[i + 1].lower().lstrip("-")
    return None


def _message_box(title: str, message: str, *, error: bool = True) -> None:
    """Non-blocking notice. A modal is used only when MAGIC_BLOCKING_DIALOG=1."""
    text = f"{title}: {message}"
    try:
        from engine.logutil import log  # type: ignore

        log(text[:1500], "WARN" if error else "INFO")
    except Exception:
        pass
    try:
        print(text, file=sys.stderr)
    except Exception:
        pass
    if os.environ.get("MAGIC_BLOCKING_DIALOG", "").strip().lower() not in ("1", "true", "yes"):
        return
    try:
        flags = 0x00000010 if error else 0x00000040  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, str(message)[:1500], str(title)[:120], flags)
    except Exception:
        pass


def _failure_detail(exc: BaseException | None = None, code: int | None = None) -> tuple[str, str]:
    """Return (kind, detail_text) for user dialogs."""
    kind = ""
    detail = ""
    links: dict = {}
    try:
        from engine.errors import last_failure  # type: ignore

        info = last_failure()
        kind = str(info.get("kind") or "")
        detail = str(info.get("message") or "")
        links = dict(info.get("links") or {})
    except Exception:
        pass
    if not detail and exc is not None:
        detail = str(exc)
    if not detail and code is not None:
        detail = f"exit code {code}"
    issue = (links or {}).get("issue") or ""
    if issue and issue not in detail:
        detail = (detail + f"\n\nGitHub: {issue}").strip()
    if not kind and detail and "ESP/SRP" in detail:
        kind = "esp-srp-failed"
    return kind, detail


def _format_user_error(t: dict, kind: str, detail: str) -> str:
    key = "error_esp_srp" if "esp-srp" in (kind or "") or "ESP/SRP" in detail else "error_blocked"
    if kind in ("oneclick-failed", "unhandled-exception", "main-unhandled-exception", "gui-oneclick-exception"):
        if "ESP/SRP" not in detail:
            key = "error_crash"
    tmpl = t.get(key) or t.get("error_blocked") or "{detail}"
    return tmpl.replace("{detail}", detail or "(see logs)")


def absorb_fatal(exc: BaseException, *, strings: dict | None = None) -> int:
    """
    Report + show friendly dialog. Never re-raise — avoids PyInstaller
    'Unhandled exception in script' for expected upgrade stops.
    """
    from engine.errors import EXIT_BLOCKED, EXIT_FAILED, UpgradeBlockedError  # type: ignore

    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise exc

    t = strings or {}
    try:
        if not t:
            t = load_strings(app_root())
    except Exception:
        t = {}

    links: dict = {}

    def _report() -> None:
        try:
            from engine.gh_report import report_unhandled_exception  # type: ignore
            from engine.errors import remember_failure  # type: ignore

            if isinstance(exc, UpgradeBlockedError):
                remember_failure(str(exc), kind=exc.kind, links=exc.links)
            elif "Issue:" not in str(exc):
                report_unhandled_exception(
                    type(exc),
                    exc,
                    exc.__traceback__,
                    kind="app-fatal-exception",
                )
                remember_failure(str(exc), kind="app-fatal-exception", links={})
        except Exception as report_error:
            try:
                from engine.logutil import log  # type: ignore

                log(f"fatal report failed: {report_error}", "WARN")
            except Exception:
                pass

    try:
        from engine.issue_url import run_in_background  # type: ignore

        run_in_background(_report, name="fatal-report")
    except Exception:
        pass

    kind, detail = _failure_detail(exc)
    if links.get("issue") and links["issue"] not in detail:
        detail = (detail + f"\n\nGitHub: {links['issue']}").strip()
    title = t.get("app_title", "Win11 Magic Upgrade")
    body = _format_user_error(t, kind, detail)
    _message_box(title, body, error=True)
    return EXIT_BLOCKED if isinstance(exc, UpgradeBlockedError) or "ESP/SRP" in str(exc) else EXIT_FAILED


def cli_help_text() -> str:
    """Console help. Kept encodable in cp1252 so Windows PowerShell 5.1 can print it."""
    return """
Win11 Magic Upgrade - CLI

  Win11MagicUpgrade.exe --cli --repair-before-reboot
  Win11MagicUpgrade.exe --cli --diagnose
  Win11MagicUpgrade.exe --cli --oneclick
  Win11MagicUpgrade.exe --cli --dry-run
  Win11MagicUpgrade.exe --cli --resume | --boot-resume
  Win11MagicUpgrade.exe --cli --bypass | --mbr | --srp | --hybrid
  Win11MagicUpgrade.exe --cli --openduet-prepare
  Win11MagicUpgrade.exe --cli --openduet-write --disk N            # dry-run (prints confirm token)
  Win11MagicUpgrade.exe --cli --openduet-write --disk N --confirm TOKEN [--volume E:]
  Win11MagicUpgrade.exe --cli --openduet-write --disk N --confirm TOKEN --openduet-partition
  Win11MagicUpgrade.exe --cli --patch | --patch-deep | --install-patches
  Win11MagicUpgrade.exe --cli --support-zip | --offline-pack
  Win11MagicUpgrade.exe --cli --research
  Win11MagicUpgrade.exe --cli --stats
  Win11MagicUpgrade.exe --update
  Win11MagicUpgrade.exe --no-update
  Win11MagicUpgrade.exe --skip-boot-guard

Exit codes:
  0  OK / Setup launched / already done
  2  Upgrade blocked (ESP/SRP, Vista gate, ISO verify, ...)
  3  Unexpected failure
  4  UIA / automation guard
  5  UAC elevation failed

Env (selected):
  MAGIC_ALLOW_VISTA=1          Acknowledge Vista best-effort
  MAGIC_ALLOW_LTSC=1           Attempt retail ISO on LTSC (often blocked)
  MAGIC_ALLOW_AUTOMATION=1     Bypass UIA guard
  MAGIC_STATS=1 / MAGIC_TELEMETRY=1  Local success/failure counters
  MAGIC_LP_DRY_RUN=1           Language-pack audit without DISM remove
  MAGIC_UNINSTALL_ALLOWLIST=1  Extra warn for curated uninstall targets
  MAGIC_SOFT_UNINSTALL_AV=1    Soft-uninstall AV + restore via winget after (off by default)
  MAGIC_SKIP_SOFT_UNINSTALL=1  Detect blockers only (no uninstall)
  MAGIC_SKIP_APP_RESTORE=1     Do not winget-restore soft-removed apps
  MAGIC_SKIP_APP_CARRY=1       Do not capture/restore app license keys/settings
  MAGIC_SKIP_UNLOCK_PACK=1     Skip MC/DVD/Win8 migration + LabConfig unlock pack
  MAGIC_INSTALL_FRAMEWORKS=1   Install .NET / DirectX / VC++ (GUI checkboxes)
  MAGIC_INSTALL_NETFX35=1      .NET Framework 3.5 (DISM)
  MAGIC_INSTALL_NETFX48=1      .NET Framework 4.8
  MAGIC_INSTALL_DOTNET_DESKTOP=1  .NET Desktop Runtime 8
  MAGIC_INSTALL_DIRECTX=1      DirectX End-User Runtime
  MAGIC_INSTALL_VCREDIST=1     VC++ 2015-2022 redistributables
  MAGIC_INSTALL_MEDIA_FEATURE=1  Media Feature Pack (N editions)
  MAGIC_SKIP_FRAMEWORKS=1      Disable all framework installs
  MAGIC_ESP_EXPAND=1           Opt in to shrink C: or create another ESP
  MAGIC_ESP_FONT_CLEANUP=1    Opt in to delete EFI font files after a backup
  MAGIC_BOOT_AUTO_FIX=1        Run EFI/MBR/smart-partition failover bot
  MAGIC_SKIP_BOOT_REMEDIATE=1  Disable boot remediation bot
  MAGIC_SKIP_BOOT_GUARD=1      Disable autonomous EFI/GRUB/WinRE safety net
  MAGIC_SKIP_STARTUP_UPDATE=1  Double-click runs the boot guard only, no auto update
  MAGIC_STARTUP_COUNTDOWN=20   Seconds before the no-args startup chain (default 20)
  MAGIC_WINRE_BOOT=1           Force one-shot WinRE ramdisk on next reboot
  MAGIC_WINPE_BOOT=1           Force one-shot WinPE ramdisk if WinRE unavailable
  MAGIC_INSTALL_VLC=1          Install VLC after Win10 settle if DVD Player missing
  MAGIC_FREE_SPACE=1           Aggressive disk reclaim
  MAGIC_ISO_CACHE=             Shared ISO folder(s) (; separated)
  MAGIC_DOWNLOAD_SEGMENTS=6    Parallel ISO/ESD connections (4-8)
  MAGIC_ISO_SHA256=            Expected SHA-256; mismatch deletes and retries
  MAGIC_ENTERPRISE_ISO_DIR=    Local VL/enterprise ISO folder
  MAGIC_DU_CAB_DIR=            Offline Dynamic Update .cab folder (staged before Setup)
  MAGIC_DU_OFFLINE=1           Force /dynamicupdate disable when cabs staged
  MAGIC_ISO_DIRS=              Extra ISO search dirs (; separated)
  MAGIC_SRP_CONTINUE=1         DANGER: continue after ESP/SRP fail
  MAGIC_BLOCK_VISTA=1          Hard-block Vista One-Click
  MAGIC_OPENDUET_PREPARE=1     Stage OpenDuet files during One-Click (no disk write)
  MAGIC_OPENDUET_BLESS=        EFI loader path for OpenCore BlessOverride
  MAGIC_KEEP_LEGACY=1          Do not convert MBR to GPT
  MAGIC_FORCE_MBR2GPT=1        Convert MBR even when firmware looks legacy-only
  MAGIC_OPENDUET_ALLOW_LARGE=1 Allow partitioning a USB larger than 128 GiB
  MAGIC_NO_DIALOG=1            Print errors instead of a modal dialog (set in CI)
  MAGIC_SKIP_AUTO_DIAG=1      Do not scan logs or open an auto-diag issue at the end
  MAGIC_AUTO_DIAG_GIST=1      Upload the redacted log zip with gh gist when logged in
  MAGIC_SKIP_BOOT_GUARD=1     Skip the startup anti-failed-boot guard
  MAGIC_BOOT_GUARD_BUDGET=180 Wall-clock seconds for that guard

Docs: docs/ARCHITECTURE.md  docs/IMPROVEMENTS.md  docs/BLOCKER_APPS.md
""".strip()


def _emit_cli_help() -> None:
    text = cli_help_text()
    try:
        print(text)
        return
    except UnicodeEncodeError:
        pass
    encoding = getattr(sys.stdout, "encoding", None) or "cp1252"
    data = (text + "\n").encode(encoding, errors="replace")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
        return
    print(data.decode("ascii", errors="replace"))


def _no_args_guard() -> None:
    """ESP/SRP, BCD, bootmgr, firmware mode, Secure Boot CA, and BIOS boot code."""
    from engine.pre_reboot import boot_guard_skipped, startup_boot_guard  # type: ignore

    if boot_guard_skipped([]):
        return
    if not is_admin():
        if relaunch_as_admin([]):
            raise SystemExit(0)
        print("UAC elevation failed. The boot guard could not verify the firmware store.")
    report = startup_boot_guard([])
    print(report.get("summary_text") or report.get("verdict") or "")


def _no_args_update() -> None:
    try:
        from engine.self_update import startup_update_async  # type: ignore

        startup_update_async([])
    except Exception:
        pass


def main() -> None:
    stdio_safe.ensure_stdio()
    if not sys.argv[1:]:
        from engine.pre_reboot import no_args_entrypoint  # type: ignore

        no_args_entrypoint(guard=_no_args_guard, update=_no_args_update, continue_app=lambda: _main_after_no_args())
        return
    _main_after_no_args()


def _main_after_no_args() -> None:
    root = app_root()
    _ensure_sys_path(root)
    argv = sys.argv[1:]
    argv_l = [a.lower() for a in argv]
    if any(a in argv_l for a in ("-h", "--help", "/?")):
        _emit_cli_help()
        raise SystemExit(0)
    try:
        from engine.uia_guard import mark_app_start  # type: ignore

        mark_app_start()
    except Exception:
        pass
    try:
        from engine.gh_report import install_exception_hooks  # type: ignore

        install_exception_hooks()
    except Exception:
        pass
    try:
        from engine.stale_letters import sweep_stale_letters  # type: ignore

        sweep_stale_letters(when="startup")
    except Exception:
        pass
    auto_action = _parse_auto_action(argv)
    strings = load_strings(root)
    try:
        from engine.self_update import startup_update_async  # type: ignore

        startup_update_async(argv)
    except Exception:
        pass

    try:
        from engine.pre_reboot import (  # type: ignore
            boot_guard_skipped,
            guard_blocks_action,
            startup_action,
            startup_boot_guard,
        )

        if not boot_guard_skipped(argv):
            guard_report = startup_boot_guard(argv)
            if guard_blocks_action(startup_action(argv), str(guard_report.get("verdict") or "")):
                print("NO-GO: upgrade and reboot steps are blocked.")
                if any(a in argv_l for a in ("--cli", "--oneclick", "-oneclick", "--mbr", "--srp")):
                    raise SystemExit(2)
    except SystemExit:
        raise
    except Exception as guard_error:
        print(f"Startup boot guard failed: {guard_error}")

    if not argv and stdio_safe.stream_isatty(sys.stdin) and stdio_safe.stream_isatty(sys.stdout):
        from engine.win11_inplace_policy import startup_countdown_seconds  # type: ignore

        if not console_countdown(startup_countdown_seconds(), wait=time.sleep, key_pressed=_console_key_pressed):
            raise SystemExit(0)
        if not is_admin() and relaunch_as_admin(["--startup-chain"]):
            raise SystemExit(0)
        raise SystemExit(run_console_startup())

    cli = any(
        a in argv_l
        for a in (
            "--cli",
            "--startup-chain",
            "--oneclick",
            "-oneclick",
            "--diagnose",
            "--bypass",
            "--resume",
            "--boot-resume",
            "--mbr",
            "--srp",
            "--hybrid",
            "--hybrid-activate",
            "--patch",
            "--patch-deep",
            "--install-patches",
            "--stats",
            "--dry-run",
            "--dryrun",
            "--support-zip",
            "--offline-pack",
            "--research",
            "--boot-fix",
            "--boot-heal",
            "--repair-before-reboot",
            "--openduet-prepare",
            "--openduet-write",
        )
    )
    # --auto keeps GUI (not CLI)
    try:
        if cli and auto_action is None:
            from engine import (
                apply_bypass_only,
                convert_mbr_only,
                deploy_hybrid_only,
                fix_system_reserved_only,
                install_preventive_only,
                run_diagnose,
                run_patch_enrichment,
                run_pipeline,
            )
            from engine.errors import EXIT_BLOCKED, EXIT_FAILED, UpgradeBlockedError  # type: ignore

            if "--stats" in argv_l:
                from engine.stats import print_stats  # type: ignore

                print_stats()
                return
            if "--support-zip" in argv_l:
                from engine.support_zip import export_support_zip  # type: ignore

                print(export_support_zip())
                return
            if "--offline-pack" in argv_l:
                from engine.offline_pack import create_offline_pack  # type: ignore

                print(create_offline_pack())
                return
            if "--research" in argv_l:
                from engine.detect import collect_report  # type: ignore
                from engine.research_pack import apply_research_pack  # type: ignore

                print(apply_research_pack(collect_report(), resume=False))
                return
            if "--startup-chain" in argv_l:
                raise SystemExit(run_console_startup())
            if "--diagnose" in argv_l:
                run_diagnose()
                return
            if "--openduet-prepare" in argv_l or "--openduet-write" in argv_l:
                if "--openduet-write" in argv_l and not is_admin():
                    if relaunch_as_admin(argv):
                        raise SystemExit(0)
                    print("ERROR: UAC elevation failed or was cancelled.", file=sys.stderr)
                    raise SystemExit(5)
                from engine.openduet import run_openduet_cli  # type: ignore

                raise SystemExit(run_openduet_cli(argv))
            if "--dry-run" in argv_l or "--dryrun" in argv_l:
                raise SystemExit(run_pipeline(dry_run=True))
            if not is_admin():
                if relaunch_as_admin(argv):
                    raise SystemExit(0)
                print("ERROR: UAC elevation failed or was cancelled.", file=sys.stderr)
                raise SystemExit(5)
            try:
                if "--bypass" in argv_l:
                    apply_bypass_only()
                    return
                if "--boot-heal" in argv_l:
                    from engine.boot_guard import verify_and_heal_boot  # type: ignore

                    heal = verify_and_heal_boot(force=True)
                    raise SystemExit(0 if heal.get("ok") or heal.get("bootable") else 2)
                if "--repair-before-reboot" in argv_l:
                    from engine.pre_reboot import default_uefi, repair_before_reboot  # type: ignore

                    report = repair_before_reboot(uefi=default_uefi())
                    print(report.get("summary_text") or report.get("verdict"))
                    raise SystemExit(0 if report.get("verdict") == "GO" else 2)
                if "--boot-fix" in argv_l:
                    from engine.boot_remediation import run_boot_auto_remediate  # type: ignore
                    from engine.detect import collect_report  # type: ignore

                    bot = run_boot_auto_remediate(
                        collect_report(),
                        force_esp=True,
                        try_mbr2gpt=False,
                        try_hybrid=True,
                    )
                    raise SystemExit(0 if bot.get("ok") or bot.get("bootable") else 2)
                if "--mbr" in argv_l:
                    convert_mbr_only()
                    return
                if "--srp" in argv_l:
                    fix_system_reserved_only()
                    return
                if "--hybrid-activate" in argv_l:
                    deploy_hybrid_only(activate=True)
                    return
                if "--hybrid" in argv_l:
                    deploy_hybrid_only(activate=False)
                    return
                if "--install-patches" in argv_l:
                    install_preventive_only()
                    return
                if "--patch-deep" in argv_l:
                    run_patch_enrichment(deep_heal=True)
                    return
                if "--patch" in argv_l:
                    run_patch_enrichment(deep_heal=False)
                    return
                # Silent one-click via CLI under UIA/AI automation risk → refuse
                try:
                    from engine.uia_guard import cli_blocks_silent_oneclick  # type: ignore

                    if cli_blocks_silent_oneclick():
                        _message_box(
                            strings.get("app_title", "Win11 Magic Upgrade"),
                            strings.get(
                                "uia_blocked",
                                "Blocked by UIA/automation guard. Set MAGIC_ALLOW_AUTOMATION=1 to override.",
                            ),
                            error=True,
                        )
                        raise SystemExit(4)
                except SystemExit:
                    raise
                except Exception:
                    pass
                if "--boot-resume" in argv_l:
                    try:
                        from engine.autonomy import should_auto_resume_on_startup  # type: ignore

                        if not should_auto_resume_on_startup():
                            raise SystemExit(0)
                    except SystemExit:
                        raise
                    except Exception:
                        pass
                if "--oneclick" in argv_l or "-oneclick" in argv_l:
                    from engine.pre_reboot import (  # type: ignore
                        default_uefi,
                        guard_blocks_action,
                        last_boot_guard,
                        open_repair_issue,
                        repair_before_reboot,
                        verdict_message,
                    )

                    repair = last_boot_guard()
                    if repair is None:
                        repair = repair_before_reboot(uefi=default_uefi())
                        print(open_repair_issue(repair))
                    print(repair.get("summary_text") or repair.get("verdict"))
                    print(verdict_message(str(repair.get("verdict") or ""), str(repair.get("bundle") or "")))
                    if guard_blocks_action("oneclick", str(repair.get("verdict") or "")):
                        raise SystemExit(2)
                code = run_pipeline(resume="--resume" in argv_l or "--boot-resume" in argv_l)
                if code in (EXIT_BLOCKED, EXIT_FAILED):
                    kind, detail = _failure_detail(code=code)
                    _message_box(
                        strings.get("app_title", "Win11 Magic Upgrade"),
                        _format_user_error(strings, kind, detail),
                        error=True,
                    )
                raise SystemExit(code)
            except UpgradeBlockedError as e:
                raise SystemExit(absorb_fatal(e, strings=strings))
            except SystemExit:
                raise
            except Exception as e:
                raise SystemExit(absorb_fatal(e, strings=strings))

        App(auto_action=auto_action).mainloop()
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(absorb_fatal(e, strings=strings))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:
        # Absolute last line of defense vs PyInstaller "Unhandled exception in script"
        try:
            raise SystemExit(absorb_fatal(e))
        except SystemExit:
            raise
        except Exception:
            try:
                _message_box("Win11 Magic Upgrade", str(e)[:800], error=True)
            except Exception:
                pass
            raise SystemExit(3)
    finally:
        if not any(a.lower() in ("-h", "--help", "/?") for a in sys.argv[1:]):
            try:
                from engine.run_diag import finish_run_async  # type: ignore

                finish_run_async(mode="process")
            except Exception:
                pass
