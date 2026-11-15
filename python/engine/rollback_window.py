"""Keep Windows.old until the user confirms a longer rollback window.

DISM /Set-OSUninstallWindow accepts 2 to 60 days. The default is 10. This
module only builds or runs that command when MAGIC_KEEP_WINDOWS_OLD=1.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Callable

Runner = Callable[..., tuple[int, str]]


def clamp_days(value: int | str | None) -> int:
    try:
        days = int(value) if value is not None and str(value).strip() else 30
    except (TypeError, ValueError):
        days = 30
    return max(2, min(60, days))


def keep_requested() -> bool:
    return os.environ.get("MAGIC_KEEP_WINDOWS_OLD", "").strip().lower() in ("1", "true", "yes")


def set_window_argv(days: int, dism: str = "dism.exe") -> list[str]:
    return [dism, "/Online", "/Set-OSUninstallWindow", f"/Value:{clamp_days(days)}"]


def get_window_argv(dism: str = "dism.exe") -> list[str]:
    return [dism, "/Online", "/Get-OSUninstallWindow"]


def plan_keep_windows_old(*, days: int | None = None, now: datetime | None = None) -> dict:
    chosen = clamp_days(days if days is not None else os.environ.get("MAGIC_WINDOWS_OLD_DAYS"))
    moment = now or datetime.now(timezone.utc)
    expiry = (moment + timedelta(days=chosen)).date().isoformat()
    return {
        "apply": keep_requested(),
        "days": chosen,
        "expiry": expiry,
        "warning": (
            f"Keep Windows.old until {expiry}. Disk Cleanup and Storage Sense will remove the rollback "
            "if they run before that date."
        ),
    }


def apply_keep_windows_old(runner: Runner, *, days: int | None = None) -> dict:
    plan = plan_keep_windows_old(days=days)
    if not plan["apply"]:
        plan["ran"] = False
        return plan
    code, output = runner(set_window_argv(plan["days"]), timeout=120)
    plan["ran"] = True
    plan["code"] = code
    plan["output"] = output
    return plan
