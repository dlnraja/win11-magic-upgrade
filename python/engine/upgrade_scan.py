"""Compat scan, safeguard holds, language match, phase codes, and wimmount.

scanonly is built here and runs only when MAGIC_COMPAT_SCANONLY=1.
Safeguard bypass stays off unless MAGIC_DISABLE_SAFEGUARDS=1.
"""
from __future__ import annotations

import os
from pathlib import Path

SCAN_CODES = {
    0xC1900210: ("clean", "No compatibility issues found."),
    0xC1900208: ("actionable", "Setup found compatibility issues that can be acted on."),
    0xC1900200: ("hardware", "This PC does not meet the hardware requirements."),
    0xC1900202: ("hardware", "This PC does not meet the hardware requirements."),
    0xC190020E: ("disk", "There is not enough free disk space."),
}

# Intel SST audio hold, Microsoft known issue 2746.
_HOLDS = (
    {"driver": "intcaudiobus", "versions": ("10.29.0.5152", "10.30.0.5152"), "fix": "Install a newer Intel Smart Sound driver from the PC maker or Windows Update."},
)


def scanonly_enabled() -> bool:
    return os.environ.get("MAGIC_COMPAT_SCANONLY", "").strip().lower() in ("1", "true", "yes")


def scanonly_argv(setup: str, log_dir: str) -> list[str]:
    return [
        setup,
        "/auto",
        "upgrade",
        "/quiet",
        "/compat",
        "scanonly",
        "/copylogs",
        log_dir,
        "/noreboot",
    ]


def decode_scan_code(code: int) -> dict:
    kind, message = SCAN_CODES.get(int(code) & 0xFFFFFFFF, ("unknown", f"Setup compat scan returned {code}"))
    return {"code": int(code), "kind": kind, "message": message, "go": kind == "clean"}


def safeguard_hold(driver: str, version: str) -> dict | None:
    name = (driver or "").lower()
    for row in _HOLDS:
        if row["driver"] in name and version in row["versions"]:
            return {"driver": driver, "version": version, "fix": row["fix"]}
    return None


def safeguards_bypass_allowed() -> bool:
    return os.environ.get("MAGIC_DISABLE_SAFEGUARDS", "").strip().lower() in ("1", "true", "yes")


def _lang_key(value: str) -> str:
    return (value or "").strip().lower().replace("_", "-")


def keep_apps_allowed(*, host_language: str, iso_language: str, host_edition: str, iso_edition: str, keep_apps: bool) -> tuple[bool, str]:
    if not keep_apps:
        return True, ""
    if host_language and iso_language and _lang_key(host_language) != _lang_key(iso_language):
        return False, f"Keep personal files and apps needs an ISO in {host_language}, not {iso_language}."
    host_ed = (host_edition or "").lower()
    iso_ed = (iso_edition or "").lower()
    if host_ed and iso_ed and host_ed not in iso_ed and iso_ed not in host_ed:
        return False, f"Keep personal files and apps needs the {host_edition} edition, not {iso_edition}."
    return True, ""


def phase_from_subcode(subcode: int) -> dict:
    """0xC1900101 extended codes. Second-boot failures are driver/GPU/AV."""
    code = int(subcode)
    if code in (0x20017, 0x20004, 0x2000C):
        phase = "safeos"
    elif code in (0x30018, 0x3000D):
        phase = "first-boot"
    elif code in (0x40017, 0x4000D):
        phase = "second-boot"
    else:
        phase = "unknown"
    message = {
        "safeos": "SafeOS failed. Check storage drivers, encryption, and disconnected extra disks.",
        "first-boot": "First boot migration failed. Check filter drivers and the network adapter.",
        "second-boot": "Second boot failed. Check the GPU, antivirus filters, and Memory Integrity incompatible drivers.",
        "unknown": f"Upgrade failed in an unrecognized phase ({code:#x}).",
    }[phase]
    return {"subcode": code, "phase": phase, "message": message, "basic_display_opt_in": phase == "second-boot"}


def wimmount_status(*, path: str, inbox_path: str) -> dict:
    current = Path(path) if path else None
    inbox = Path(inbox_path) if inbox_path else None
    ok = bool(current and inbox and current.is_file() and inbox.is_file() and current.resolve() == inbox.resolve())
    return {
        "ok": ok,
        "action": "" if ok else "reregister-inbox-wimmount",
        "message": "wimmount.sys matches the inbox driver." if ok else "Re-register the inbox wimmount.sys before Setup.",
    }
