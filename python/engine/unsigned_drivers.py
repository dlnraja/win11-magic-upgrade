"""
Unsigned / problem driver inventory (MS: sigverif + SetupDiag UnknownDriverMigrationFailure).

Uses driverquery / pnputil — no 3rd-party sigcheck binary required.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from .logutil import STATE_DIR, log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd: list[str], timeout: int = 120) -> str:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)


def scan_unsigned_and_problem_drivers() -> dict[str, Any]:
    result: dict[str, Any] = {
        "unsigned_hints": [],
        "problem_devices": [],
        "oem_inf_count": 0,
        "notes": [],
    }
    log("=== Driver signature / problem device scan ===", "STEP")
    # driverquery /si — signed drivers report
    out = _run(["driverquery", "/si", "/fo", "csv"])
    if out and "FALSE" in out.upper():
        for line in out.splitlines()[1:]:
            if re.search(r",\s*FALSE\s*,|FALSE$", line, re.I):
                # CSV: Module Name, Display Name, Driver Type, Link Date, ...
                parts = [p.strip().strip('"') for p in line.split(",")]
                name = parts[0] if parts else line[:80]
                result["unsigned_hints"].append(name[:120])
        result["unsigned_hints"] = sorted(set(result["unsigned_hints"]))[:40]
        if result["unsigned_hints"]:
            log(
                "Possibly unsigned drivers: " + ", ".join(result["unsigned_hints"][:10]),
                "WARN",
            )
            result["notes"].append(
                "MS recommends updating/removing unsigned drivers before upgrade "
                "(SetupDiag UnknownDriverMigrationFailure)."
            )
    else:
        log("driverquery /si: no FALSE IsSigned rows (or tool unavailable)", "OK")

    # pnputil problem devices
    pout = _run(["pnputil", "/enum-devices", "/problem"])
    if pout and "Instance ID" in pout:
        for m in re.finditer(r"Instance ID:\s*(.+)", pout, re.I):
            result["problem_devices"].append(m.group(1).strip()[:160])
        result["problem_devices"] = result["problem_devices"][:30]
        if result["problem_devices"]:
            log(f"pnputil problem devices: {len(result['problem_devices'])}", "WARN")

    # Count 3rd-party OEM infs (informational)
    enum = _run(["pnputil", "/enum-drivers"])
    oem = re.findall(r"Published Name:\s*(oem\d+\.inf)", enum, re.I)
    result["oem_inf_count"] = len(set(oem))
    log(f"Third-party driver packages (oem*.inf): {result['oem_inf_count']}", "INFO")

    try:
        import json

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "driver-scan.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    save_state({"DriverScan": result})
    return result
