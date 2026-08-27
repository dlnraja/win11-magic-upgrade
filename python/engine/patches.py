"""Researched migration patches - stdlib / native exes only."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import winreg
from pathlib import Path

from .logutil import log

BLOCKERS = [
    (r"Logitech Gaming Software", "Legacy LGS filters / 0xC1900101"),
    (r"Norton|McAfee|Avast|AVG|Kaspersky|Bitdefender|ESET|Sophos|Malwarebytes", "AV filters SafeOS"),
    (r"Acronis|Macrium|EaseUS Todo|AOMEI|Veeam Agent", "Disk/VSS filter drivers"),
    (r"SentinelOne|CrowdStrike|Carbon Black", "EDR leftover .sys"),
    (r"Daemon Tools|Alcohol 120%|PowerISO", "Virtual CD filters"),
]


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return str(e)


def _installed_names() -> list[str]:
    names = []
    for hive_path in (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_path) as root:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            name, _ = winreg.QueryValueEx(k, "DisplayName")
                            names.append(str(name))
                    except OSError:
                        pass
        except OSError:
            pass
    return names


def apply_migration_patches() -> None:
    log("=== Migration patches (no .NET / no PowerShell) ===", "STEP")

    # Prior setuperr scan
    for rel in (
        r"C:\$WINDOWS.~BT\Sources\Panther\setuperr.log",
        r"C:\$WINDOWS.~BT\Sources\Rollback\setuperr.log",
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Panther", "setuperr.log"),
    ):
        p = Path(rel)
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(
                    r"0xC1900101|0xC1900208|0x80070070|0x8007001F|0x800F081F|SECOND_BOOT",
                    text,
                ):
                    log(f"Prior log hit in {p.name}: {m.group(0)}", "WARN")
                    break
            except Exception:
                pass

    for name in _installed_names():
        for pat, reason in BLOCKERS:
            if re.search(pat, name, re.I):
                log(f"Blocker software: {name} :: {reason}", "WARN")
                break

    log("Disconnecting mapped network drives...", "STEP")
    _run(["net", "use", "*", "/delete", "/y"])

    # Stop risky 3rd-party services
    out = _run(["sc", "query", "type=", "service", "state=", "all"])
    for svc in re.findall(r"SERVICE_NAME:\s+(\S+)", out):
        if re.search(
            r"norton|mcafee|avast|avg|kaspersky|bitdefender|eset|sophos|malwarebytes|acronis|macrium",
            svc,
            re.I,
        ):
            log(f"Stopping service {svc}", "WARN")
            _run(["sc", "stop", svc])

    # Clear leftover upgrade folders
    for junk in (r"C:\$WINDOWS.~BT", r"C:\$Windows.~WS"):
        if Path(junk).exists():
            log(f"Removing leftover {junk}", "WARN")
            _run(["cmd", "/c", f"rmdir /s /q \"{junk}\""])

    # Free space helpers
    _run(["powercfg", "/hibernate", "off"])
    temp = Path(os.environ.get("TEMP", "."))
    for child in list(temp.glob("*"))[:500]:
        try:
            if child.is_file():
                child.unlink(missing_ok=True)
        except Exception:
            pass

    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    appraiser = windir / "appcompat" / "appraiser"
    if appraiser.exists():
        for f in appraiser.rglob("*"):
            if f.suffix.lower() in {".xml", ".sdb", ".cab"}:
                try:
                    f.unlink()
                except Exception:
                    pass
        log("Cleared appraiser cache", "OK")

    free = shutil.disk_usage(os.environ.get("SystemDrive", "C:\\")).free / (1024**3)
    log(f"Free space now ~{free:.1f} GB", "OK" if free >= 15 else "WARN")
    if free < 12:
        raise RuntimeError(f"Not enough free disk space ({free:.1f} GB). Need ~20 GB.")

    log("Migration patches done.", "OK")
