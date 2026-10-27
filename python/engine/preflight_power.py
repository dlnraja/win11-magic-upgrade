"""Preflight: AC power, sleep/hibernate disable for long Setup sessions."""
from __future__ import annotations

import ctypes
import subprocess
from typing import Any

from .logutil import log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# SYSTEM_POWER_STATUS
class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_byte),
        ("BatteryFlag", ctypes.c_byte),
        ("BatteryLifePercent", ctypes.c_byte),
        ("SystemStatusFlag", ctypes.c_byte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def on_ac_power() -> bool | None:
    try:
        st = _SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return None
        # 1 = AC, 0 = battery, 255 = unknown
        if st.ACLineStatus == 1:
            return True
        if st.ACLineStatus == 0:
            return False
        return None
    except Exception:
        return None


def harden_power_for_setup() -> dict[str, Any]:
    result: dict[str, Any] = {"ac": None, "actions": []}
    ac = on_ac_power()
    result["ac"] = ac
    if ac is False:
        log("BATTERY power — plug into AC before long Setup (risk of mid-upgrade power loss)", "WARN")
    elif ac is True:
        log("AC power OK", "OK")

    def _pc(args: list[str]) -> None:
        try:
            subprocess.run(
                ["powercfg"] + args,
                capture_output=True,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
            )
            result["actions"].append(" ".join(args))
        except (OSError, subprocess.TimeoutExpired):
            pass

    # Monitor/sleep never while on AC (temporary for upgrade session)
    _pc(["/change", "monitor-timeout-ac", "0"])
    _pc(["/change", "standby-timeout-ac", "0"])
    _pc(["/change", "hibernate-timeout-ac", "0"])
    log("Power: AC sleep/monitor timeouts set to never for Setup session", "OK")
    save_state({"PreflightPower": result})
    return result
