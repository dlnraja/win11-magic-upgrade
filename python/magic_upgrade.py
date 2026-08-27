#!/usr/bin/env python3
"""
Win11 Magic Upgrade — portable GUI/CLI launcher.
Delegates the heavy lifting to the PowerShell engine (no FlyOOBE / modern .NET required).
"""
from __future__ import annotations

import ctypes
import json
import locale
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext


def load_strings(root: Path) -> dict:
    lang = (locale.getdefaultlocale()[0] or "en").lower()
    code = "fr" if lang.startswith("fr") else "en"
    path = root / "i18n" / "strings.json"
    data = {"en": {}, "fr": {}}
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


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", here))
        for candidate in (here, meipass, here / "_internal", meipass / "payload"):
            if (candidate / "src" / "Win11MagicUpgrade.ps1").exists():
                return candidate
            if (candidate / "Win11MagicUpgrade.ps1").exists():
                return candidate
        return here
    return Path(__file__).resolve().parents[1]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: list[str] | None = None) -> None:
    args = extra_args or []
    if getattr(sys, "frozen", False):
        params = " ".join(f'"{a}"' if " " in a else a for a in args)
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
    else:
        script = str(Path(__file__).resolve())
        params = " ".join([f'"{script}"'] + [f'"{a}"' if " " in a else a for a in args])
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )


def find_ps1(root: Path) -> Path:
    candidates = [
        root / "src" / "Win11MagicUpgrade.ps1",
        root / "Win11MagicUpgrade.ps1",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "Win11MagicUpgrade.ps1 introuvable. Gardez le dossier src/ à côté de l'EXE."
    )


def run_powershell(ps1: Path, ps_args: list[str], log_cb) -> int:
    env = os.environ.copy()
    env["WMU_NO_PAUSE"] = "1"
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps1),
        *ps_args,
    ]
    log_cb(" ".join(cmd) + "\n")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log_cb(line)
    return proc.wait()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.root_dir = app_root()
        self.t = load_strings(self.root_dir)
        self.title(self.t.get("app_title", "Win11 Magic Upgrade"))
        self.geometry("760x520")
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
            text=self.t.get("app_sub", ""),
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
            command=lambda: self.start(["-OneClick"]),
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
            command=lambda: self.start(["-DiagnoseOnly"]),
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
            command=lambda: self.start(["-ApplyBypassOnly"]),
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            self,
            text=self.t.get("note", ""),
            font=("Segoe UI", 9),
            fg="#64748b",
            bg="#0f172a",
            wraplength=700,
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
        self.append(self.t.get("ready", "Ready.") + "\n")

    def append(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

    def start(self, ps_args: list[str]) -> None:
        if self._busy:
            return
        title = self.t.get("app_title", "Win11 Magic Upgrade")
        if not is_admin():
            if messagebox.askyesno(title, self.t.get("need_admin", "Admin required?")):
                relaunch_as_admin(ps_args if getattr(sys, "frozen", False) else None)
            return
        try:
            ps1 = find_ps1(self.root_dir)
        except FileNotFoundError as e:
            messagebox.showerror(title, str(e))
            return

        if "-OneClick" in ps_args:
            if not messagebox.askyesno(title, self.t.get("confirm_upgrade", "Continue?")):
                return

        self._busy = True
        self.btn_go.configure(state="disabled")

        def worker() -> None:
            try:
                code = run_powershell(ps1, ps_args, lambda s: self.after(0, self.append, s))
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
    # CLI mode: python magic_upgrade.py --oneclick
    argv = [a.lower() for a in sys.argv[1:]]
    if "--cli" in argv or "--oneclick" in argv or "-oneclick" in argv:
        if not is_admin():
            relaunch_as_admin(sys.argv[1:])
            return
        root = app_root()
        ps1 = find_ps1(root)
        args = ["-OneClick"]
        if "--diagnose" in argv:
            args = ["-DiagnoseOnly"]
        raise SystemExit(run_powershell(ps1, args, lambda s: print(s, end="")))

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
