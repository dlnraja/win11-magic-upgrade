"""Suspend BitLocker for the whole repair and reboot window.

manage-bde without -rebootcount resumes protection on the next restart.
Boot repair plus Setup can reboot more than once, so the suspend covers that
window. Boot, BCD, ESP and DBX writes stay refused while protection is still On.
"""
from __future__ import annotations

import os
import re
from typing import Callable

REBOOT_MIN = 0
REBOOT_MAX = 15
DEFAULT_REBOOTS = 3

Runner = Callable[..., tuple[int, str]]


def clamp_reboot_count(value: int | str | None, default: int = DEFAULT_REBOOTS) -> int:
    try:
        count = int(value) if value is not None and str(value).strip() != "" else default
    except (TypeError, ValueError):
        count = default
    return max(REBOOT_MIN, min(REBOOT_MAX, count))


def reboot_count_for_plan(extra: int | None = None) -> int:
    raw = os.environ.get("MAGIC_BITLOCKER_REBOOTS")
    if raw is not None and str(raw).strip() != "":
        return clamp_reboot_count(raw)
    return clamp_reboot_count(extra if extra is not None else DEFAULT_REBOOTS)


def suspend_argv(manage: str, drive: str, reboot_count: int) -> list[str]:
    return [
        manage,
        "-protectors",
        "-disable",
        drive,
        "-rebootcount",
        str(clamp_reboot_count(reboot_count)),
    ]


def parse_protection(text: str) -> str:
    """locked, on, off, or unknown. Locked is the only hard lock."""
    blob = text or ""
    if re.search(r"Lock Status:\s*Locked", blob, re.I):
        return "locked"
    if re.search(r"Protection\s*Status\s*:\s*On|Protection On", blob, re.I):
        return "on"
    if re.search(
        r"Protection\s*Status\s*:\s*Off|Protection Off|Fully Decrypted|No Key Protectors",
        blob,
        re.I,
    ):
        return "off"
    return "unknown"


def parse_reboot_count(text: str) -> int | None:
    match = re.search(r"Reboot\s*Count\s*:\s*(\d+)", text or "", re.I)
    if not match:
        return None
    return int(match.group(1))


def protector_ids(text: str) -> list[str]:
    """Numerical Password IDs only. The password itself is not returned."""
    ids: list[str] = []
    for match in re.finditer(
        r"Numerical Password:[\s\S]{0,200}?ID:\s*(\{[0-9A-Fa-f-]+\})",
        text or "",
        re.I,
    ):
        ids.append(match.group(1))
    return ids


def key_ack_present() -> bool:
    return os.environ.get("MAGIC_BITLOCKER_KEY_ACK", "").strip().lower() in ("1", "true", "yes")


def boot_writes_allowed(status: str, *, has_password_protector: bool) -> tuple[bool, str]:
    """Refuse boot, BCD, ESP and DBX writes while protection is still On."""
    if status == "locked":
        return False, "BitLocker is locked. Unlock with the recovery key before any boot change."
    if status == "on":
        return False, "BitLocker protection is still On. Boot, BCD, ESP and DBX writes are refused."
    if has_password_protector and not key_ack_present():
        return (
            False,
            "A BitLocker recovery key exists. Set MAGIC_BITLOCKER_KEY_ACK=1 after you have noted the key ID.",
        )
    return True, ""


def suspend_for_window(
    drive: str,
    *,
    manage: str,
    reboot_count: int,
    runner: Runner,
    status_text: str = "",
    protectors_text: str = "",
) -> dict:
    """Disable protectors for reboot_count restarts and re-read the status."""
    count = clamp_reboot_count(reboot_count)
    code, output = runner(suspend_argv(manage, drive, count), timeout=90)
    status = parse_protection(status_text or output)
    allowed, reason = boot_writes_allowed(status, has_password_protector=bool(protector_ids(protectors_text)))
    return {
        "ok": code == 0 and allowed,
        "code": code,
        "reboot_count": count,
        "status": status,
        "reboot_count_seen": parse_reboot_count(status_text or output),
        "key_ids": protector_ids(protectors_text),
        "boot_writes_allowed": allowed,
        "reason": reason,
        "output": output,
    }
