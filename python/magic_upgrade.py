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
from tkinter import messagebox, scrolledtext

# Ensure package import works from source and PyInstaller
_ROOT_CANDIDATES = []


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


def relaunch_as_admin(extra_args: list[str] | None = None) -> None:
    args = extra_args or []
    if getattr(sys, "frozen", False):
        params = " ".join(f'"{a}"' if " " in a else a for a in args)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    else:
        script = str(Path(__file__).resolve())
        params = " ".join([f'"{script}"'] + [f'"{a}"' if " " in a else a for a in args])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.root_dir = app_root()
        _ensure_sys_path(self.root_dir)
        self.t = load_strings(self.root_dir)
        self.title(self.t.get("app_title", "Win11 Magic Upgrade"))
        self.geometry("780x540")
        self.minsize(640, 420)
        self.configure(bg="#0f172a")
        self._busy = False

        header = tk.Frame(self, bg="#0f172a")
        header.pack(fill="x", padx=20, pady=(18, 8))
        tk.Label(
            header,
            text=self.t.get("app_title", "Win11 Magic Upgrade"),
            font=("Segoe UI Semibold", 18),
            fg="#38bdf8",
            bg="#0f172a",
        ).pack(anchor="w")
        tk.Label(
            header,
            text=self.t.get("app_sub", "") + "  |  NO .NET 4.x  |  NO PowerShell",
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

        tk.Label(
            self,
            text=self.t.get("note", "")
            + " | Moteur Python pur (pas de .NET Framework 4.x / pas de powershell.exe).",
            font=("Segoe UI", 9),
            fg="#64748b",
            bg="#0f172a",
            wraplength=720,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 8))

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
        self.append(self.t.get("ready", "Ready.") + "\n")

    def append(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

    def start(self, action: str) -> None:
        if self._busy:
            return
        title = self.t.get("app_title", "Win11 Magic Upgrade")
        if action != "diagnose" and not is_admin():
            if messagebox.askyesno(title, self.t.get("need_admin", "Admin?")):
                relaunch_as_admin([f"--{action}"] if getattr(sys, "frozen", False) else None)
            return
        if action == "oneclick":
            if not messagebox.askyesno(title, self.t.get("confirm_upgrade", "Continue?")):
                return

        self._busy = True
        self.btn_go.configure(state="disabled")

        def worker() -> None:
            try:
                from engine import (  # type: ignore
                    apply_bypass_only,
                    convert_mbr_only,
                    fix_system_reserved_only,
                    run_diagnose,
                    run_patch_enrichment,
                    run_pipeline,
                )

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
                else:
                    code = run_pipeline(sink)
                self.after(0, self.append, f"\n--- exit {code} ---\n")
                if code == 0:
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
                self.after(0, self._done)

        threading.Thread(target=worker, daemon=True).start()

    def _done(self) -> None:
        self._busy = False
        self.btn_go.configure(state="normal")


def main() -> None:
    root = app_root()
    _ensure_sys_path(root)
    argv = [a.lower() for a in sys.argv[1:]]

    cli = any(
        a in argv
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
        )
    )
    if cli:
        from engine import (
            apply_bypass_only,
            convert_mbr_only,
            deploy_hybrid_only,
            fix_system_reserved_only,
            run_diagnose,
            run_patch_enrichment,
            run_pipeline,
        )

        if "--diagnose" in argv:
            run_diagnose()
            return
        if not is_admin():
            relaunch_as_admin(sys.argv[1:])
            return
        if "--bypass" in argv:
            apply_bypass_only()
            return
        if "--mbr" in argv:
            convert_mbr_only()
            return
        if "--srp" in argv:
            fix_system_reserved_only()
            return
        if "--hybrid-activate" in argv:
            deploy_hybrid_only(activate=True)
            return
        if "--hybrid" in argv:
            deploy_hybrid_only(activate=False)
            return
        if "--patch-deep" in argv:
            run_patch_enrichment(deep_heal=True)
            return
        if "--patch" in argv:
            run_patch_enrichment(deep_heal=False)
            return
        code = run_pipeline(resume="--resume" in argv)
        raise SystemExit(code)

    App().mainloop()


if __name__ == "__main__":
    main()
