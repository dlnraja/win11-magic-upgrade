"""Export hardware/OS inventory JSON for support + dry-run artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log


def export_inventory(report: Any) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "report": report.as_dict() if hasattr(report, "as_dict") else {},
    }
    try:
        from .oem_adapt import get_oem_profile

        oem = get_oem_profile()
        data["oem"] = {
            "family": oem.family,
            "manufacturer": oem.manufacturer,
            "model": oem.model,
            "bitlocker": oem.bitlocker,
        }
    except Exception as e:
        data["oem_error"] = str(e)
    path = STATE_DIR / "hardware-inventory.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        desk = Path.home() / "Desktop" / "Win11MagicUpgrade-Inventory.json"
        desk.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass
    log(f"Inventory exported: {path}", "OK")
    return path
