"""Refuse destructive BCD deletes. Low-risk loop breakers stay suggestions.

bcdedit /delete {current} and bcdedit /delete {default} are never run.
A stuck safeboot value, WinRE uninstall-updates, and DISM RevertPendingActions
are the documented first steps and are not launched from this module.
"""
from __future__ import annotations

_DENY_IDS = ("{current}", "{default}")


def denies_bcd_delete(argv: list[str]) -> str:
    parts = [str(part) for part in argv]
    lowered = [part.lower() for part in parts]
    if not any(part in ("/delete", "-delete") for part in lowered):
        return ""
    for part in lowered:
        if part in _DENY_IDS:
            return f"refusing bcdedit /delete {part}"
    return ""


def low_risk_loop_steps() -> list[str]:
    return [
        "Remove a stuck safeboot value from the Windows loader.",
        "Use WinRE Uninstall updates.",
        "DISM /Image:C:\\ /Cleanup-Image /RevertPendingActions.",
    ]
