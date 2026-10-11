"""
Scan $WINDOWS.~BT Rollback folder after failed upgrades (MS Setup resolution docs).

Looks for setupmem.dmp, setupapi, setupact hints without requiring WinDbg.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log, save_state

ROLLBACK = Path(r"C:\$WINDOWS.~BT\Sources\Rollback")


def scan_rollback_folder() -> dict[str, Any]:
    result: dict[str, Any] = {
        "present": False,
        "files": [],
        "dump": False,
        "driver_hints": [],
        "codes": [],
        "notes": [],
    }
    if not ROLLBACK.is_dir():
        log("No Rollback folder (no prior failed SafeOS apply)", "INFO")
        save_state({"RollbackScan": result})
        return result
    result["present"] = True
    try:
        for p in ROLLBACK.iterdir():
            if p.is_file():
                result["files"].append(p.name)
                if p.suffix.lower() == ".dmp" or "setupmem" in p.name.lower():
                    result["dump"] = True
    except OSError as e:
        result["notes"].append(str(e))

    # Parse setupact/setuperr in Rollback for module names
    for name in ("setupact.log", "setuperr.log", "setupapi.dev.log"):
        path = ROLLBACK / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[-200_000:]
        except OSError:
            continue
        for m in re.finditer(r"0xC1900101[^\r\n]{0,40}", text, re.I):
            result["codes"].append(m.group(0)[:60])
        for m in re.finditer(
            r"(?:Module name|FAIL|Failed to load|driver)\s*[:=]?\s*([A-Za-z0-9_\-\.]+\.sys)",
            text,
            re.I,
        ):
            result["driver_hints"].append(m.group(1))
    result["codes"] = sorted(set(result["codes"]))[:20]
    result["driver_hints"] = sorted(set(result["driver_hints"]))[:30]
    if result["dump"]:
        result["notes"].append(
            "setupmem.dmp present — SafeOS BSOD; update/remove drivers in driver_hints; "
            "disconnect USB docks; suspend BitLocker/EDR."
        )
        log("Rollback setupmem.dmp found — prior SafeOS crash", "WARN")
    if result["driver_hints"]:
        log("Rollback driver hints: " + ", ".join(result["driver_hints"][:10]), "WARN")
    try:
        import json

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "rollback-scan.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    save_state({"RollbackScan": result})
    return result
