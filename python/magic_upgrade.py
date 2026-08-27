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
import threading
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
    code = "fr" if lang.startswith("fr") else "en"
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
        self.geometry("820x620")
        self.minsize(700, 500)
        self.configure(bg="#0f172a")
        self._busy = False
        self._elevating = False
        self._auto_action = auto_action

        header = tk.Frame(self, bg="#0f172a")
        header.pack(fill="x", padx=20, pady=(18, 8))
        tk.Label(
            header,
            text=self.t.get("app_title", "Win11 Magic Upgrade"),
            font=("Segoe UI Semibold", 18),
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
            text=self.t.get("app_sub", "") + "  |  " + admin_txt,
            font=("Segoe UI", 10),
            fg="#94a3b8",
            bg="#0f172a",
        ).pack(anchor="w")

        btns = tk.Frame(self, bg="#0f172a")
        btns.pack(fill="x", padx=20, pady=8)
        self.btn_go = tk.Button(
            btns,
            text=self.t.get("btn_oneclick", "One-Click"),
            font=("Segoe UI Semibold", 11),
            bg="#0284c7",
            fg="white",
            activebackground="#0369a1",
            relief="flat",
            padx=14,
            pady=8,
            command=lambda: self.start("oneclick"),
        )
        self.btn_go.pack(side="left")
        tk.Button(
            btns,
            text=self.t.get("btn_diagnose", "Diagnose"),
            font=("Segoe UI", 10),
            bg="#1e293b",
            fg="#e2e8f0",
            relief="flat",
            padx=12,
            pady=8,
            command=lambda: self.start("diagnose"),
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            btns,
            text=self.t.get("btn_bypass", "Bypass"),
            font=("Segoe UI", 10),
            bg="#1e293b",
            fg="#e2e8f0",
            relief="flat",
            padx=12,
            pady=8,
            command=lambda: self.start("bypass"),
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            btns,
            text=self.t.get("btn_mbr", "MBR->GPT + Boot"),
            font=("Segoe UI", 10),
            bg="#1e293b",
            fg="#e2e8f0",
            relief="flat",
            padx=12,
            pady=8,
            command=lambda: self.start("mbr"),
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            btns,
            text=self.t.get("btn_srp", "Fix ESP/SRP"),
            font=("Segoe UI", 10),
            bg="#1e293b",
            fg="#e2e8f0",
            relief="flat",
            padx=12,
            pady=8,
            command=lambda: self.start("srp"),
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            btns,
            text=self.t.get("btn_patch", "Patch / Enrich"),
            font=("Segoe UI", 10),
            bg="#334155",
            fg="#e2e8f0",
            relief="flat",
            padx=12,
            pady=8,
            command=lambda: self.start("patch"),
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            btns,
            text=self.t.get("btn_install_patches", "Install preventives"),
            font=("Segoe UI", 10),
            bg="#14532d",
            fg="#e2e8f0",
            relief="flat",
            padx=12,
            pady=8,
            command=lambda: self.start("install-patches"),
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            self,
            text=self.t.get("note", ""),
            font=("Segoe UI", 9),
            fg="#64748b",
            bg="#0f172a",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 6))

        # --- Progress panel: overall + step, elapsed / remaining, alive pulse ---
        prog = tk.Frame(self, bg="#0f172a")
        prog.pack(fill="x", padx=20, pady=(0, 8))
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

        self.log = scrolledtext.ScrolledText(
            self,
            font=("Consolas", 9),
            bg="#020617",
            fg="#cbd5e1",
            insertbackground="#cbd5e1",
            relief="flat",
        )
        self.log.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.append(f"Root: {self.root_dir}\n")
        self.append("Runtime: pure Python - no .NET 4.x / no PowerShell\n")
        if is_admin():
            self.append("Running as Administrator.\n")
        self.append(self.t.get("ready", "Ready.") + "\n")

        if self._auto_action:
            self.after(350, lambda: self.start(self._auto_action or "oneclick"))

    def append(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

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

    def start(self, action: str) -> None:
        if self._busy or self._elevating:
            return
        title = self.t.get("app_title", "Win11 Magic Upgrade")
        # Elevate INTO GUI (not invisible --cli) so progress bars stay visible
        if action != "diagnose" and not is_admin():
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

        self._busy = True
        self.btn_go.configure(state="disabled")
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
                elif action == "patch":
                    run_patch_enrichment(sink, deep_heal=False)
                    code = 0
                elif action == "install-patches":
                    install_preventive_only(sink)
                    code = 0
                else:
                    code = run_pipeline(sink, quiet=True)
                self.after(0, self.append, f"\n--- exit {code} ---\n")
                SETUP_OK = {0, 3010, 3011}
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
                elif code == 0:
                    self.after(
                        0,
                        lambda: messagebox.showinfo(title, self.t.get("done_ok", "Done")),
                    )
                else:
                    msg = self.t.get("done_warn", "Code {code}").replace("{code}", str(code))
                    self.after(0, lambda: messagebox.showwarning(title, msg))
            except Exception as ex:
                self.after(0, self.append, f"\nERROR: {ex}\n")
                self.after(0, lambda: messagebox.showerror(title, str(ex)))
            finally:
                try:
                    from engine.progress import end_session, set_progress_callback  # type: ignore

                    end_session(success=True)
                    set_progress_callback(None)
                except Exception:
                    pass
                self.after(0, self._done)

        threading.Thread(target=worker, daemon=True).start()

    def _done(self) -> None:
        self._busy = False
        self.btn_go.configure(state="normal")
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


def _parse_auto_action(argv: list[str]) -> str | None:
    lower = [a.lower() for a in argv]
    if "--auto" in lower:
        i = lower.index("--auto")
        if i + 1 < len(argv):
            return argv[i + 1].lower().lstrip("-")
    return None


def main() -> None:
    root = app_root()
    _ensure_sys_path(root)
    argv = sys.argv[1:]
    argv_l = [a.lower() for a in argv]
    auto_action = _parse_auto_action(argv)

    cli = any(
        a in argv_l
        for a in (
            "--cli",
            "--oneclick",
            "-oneclick",
            "--diagnose",
            "--bypass",
            "--resume",
            "--mbr",
            "--srp",
            "--hybrid",
            "--hybrid-activate",
            "--patch",
            "--patch-deep",
            "--install-patches",
            "--declare-av",
        )
    )
    # --auto keeps GUI (not CLI)
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

        if "--diagnose" in argv_l:
            run_diagnose()
            return
        if "--declare-av" in argv_l:
            from engine.av_trust import declare_all_av_trust
            from engine.logutil import init_logging

            init_logging()
            # Cloud declare does not strictly need admin; local Defender exclusions do
            if not is_admin():
                if relaunch_as_admin(argv):
                    raise SystemExit(0)
                print("WARN: continuing cloud declare without admin", flush=True)
            declare_all_av_trust()
            return
        if not is_admin():
            if relaunch_as_admin(argv):
                raise SystemExit(0)
            print("ERROR: UAC elevation failed or was cancelled.", file=sys.stderr)
            raise SystemExit(5)
        if "--bypass" in argv_l:
            apply_bypass_only()
            return
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
        code = run_pipeline(resume="--resume" in argv_l)
        raise SystemExit(code)

    App(auto_action=auto_action).mainloop()


if __name__ == "__main__":
    main()
