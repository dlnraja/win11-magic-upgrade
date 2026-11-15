"""Unsupported-PC helpers inspired by FlyOOBE. No IFEO and no trimmed images.

Every write stays behind a flag. The checklist is read-only.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def disclaimer() -> str:
    return (
        "This PC is outside Microsoft's published Windows 11 requirements. "
        "Back up your files first. Extended Security Updates are not included. "
        "Monthly updates may continue. The next yearly version is not guaranteed through Windows Update. "
        "Set MAGIC_UNSUPPORTED_ACK=1 after you have read this."
    )


def go_blocked(*, unsupported: bool) -> bool:
    if not unsupported:
        return False
    return os.environ.get("MAGIC_UNSUPPORTED_ACK", "").strip().lower() not in ("1", "true", "yes")


def checklist(facts: dict) -> list[dict]:
    """Read-only rows. Missing facts are reported as unknown, not as a failure."""
    rows = []
    for key, label, ok_when in (
        ("tpm", "TPM", lambda value: str(value).lower() in ("1", "true", "present", "2.0")),
        ("secure_boot", "Secure Boot", lambda value: str(value).lower() in ("1", "true", "on")),
        ("ram_gb", "RAM", lambda value: float(value) >= 4),
        ("arch", "Architecture", lambda value: str(value).lower() in ("x64", "amd64", "arm64")),
        ("free_gb", "Free storage", lambda value: float(value) >= 20),
        ("version", "Windows version", lambda value: bool(str(value).strip())),
    ):
        value = facts.get(key)
        if value in (None, ""):
            rows.append({"name": label, "ok": None, "detail": "unknown"})
            continue
        try:
            ok = bool(ok_when(value))
        except (TypeError, ValueError):
            ok = False
        rows.append({"name": label, "ok": ok, "detail": str(value)})
    return rows


def accept_dropped_iso(path: str) -> str:
    """Remember a dropped or pasted ISO. Other files are refused."""
    text = (path or "").strip().strip('"')
    if not text.lower().endswith(".iso"):
        return ""
    if not Path(text).is_file():
        return ""
    os.environ["MAGIC_ISO"] = text
    return text


def pause_update_commands() -> list[str]:
    if os.environ.get("MAGIC_PAUSE_WINDOWS_UPDATE", "").strip().lower() not in ("1", "true", "yes"):
        return []
    return [
        "reg add HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU /v NoAutoRebootWithLoggedOnUsers /t REG_DWORD /d 1 /f",
    ]


DEBLOAT = (
    "Microsoft.BingNews",
    "Microsoft.GetHelp",
    "Microsoft.Todos",
    "Microsoft.XboxApp",
)


def debloat_commands() -> list[str]:
    if os.environ.get("MAGIC_DEBLOAT", "").strip().lower() not in ("1", "true", "yes"):
        return []
    return [f"winget uninstall --id {app} --exact --disable-interactivity" for app in DEBLOAT]


def oobe_choices() -> dict:
    """Privacy, Copilot off, and a local account. Applied only when the flag is set."""
    enabled = os.environ.get("MAGIC_OOBE_CHOICES", "").strip().lower() in ("1", "true", "yes")
    return {
        "apply": enabled,
        "privacy_diagnostics": "basic" if enabled else "",
        "copilot": "off" if enabled else "",
        "local_account": True if enabled else False,
    }


def append_change(path: Path, kind: str, detail: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, "detail": detail[:300]},
        ensure_ascii=False,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


WINGET_CATALOG = (
    "VideoLAN.VLC",
    "7zip.7zip",
    "Microsoft.PowerToys",
)


def winget_install_commands() -> list[str]:
    if os.environ.get("MAGIC_WINGET_CATALOG", "").strip().lower() not in ("1", "true", "yes"):
        return []
    return [f"winget install --id {app} --exact --disable-interactivity" for app in WINGET_CATALOG]


def export_recipe(recipe: dict) -> str:
    return json.dumps(recipe, indent=2, sort_keys=True)


def import_recipe(text: str) -> dict:
    data = json.loads(text or "{}")
    if not isinstance(data, dict):
        raise ValueError("A recipe must be a JSON object")
    return data
