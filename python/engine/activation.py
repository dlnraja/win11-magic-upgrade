"""Snapshot digital entitlement / activation state before and after hops."""
from __future__ import annotations

import subprocess
import winreg
from datetime import datetime, timezone
from typing import Any

from .logutil import STATE_DIR, log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _reg(path: str, name: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v or "")
    except OSError:
        return ""


def snapshot_activation(*, label: str = "pre") -> dict[str, Any]:
    """Capture channel/edition markers (no product keys)."""
    snap: dict[str, Any] = {
        "label": label,
        "ts": datetime.now(timezone.utc).isoformat(),
        "ProductName": _reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName"),
        "EditionID": _reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "EditionID"),
        "ProductId": _reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductId"),
        "DigitalProductIdPresent": bool(
            _reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DigitalProductId")
        ),
        "Channel": "",
        "PartialKey": "",
        "Description": "",
    }
    try:
        r = subprocess.run(
            ["cscript.exe", "//Nologo", r"C:\Windows\System32\slmgr.vbs", "/dli"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
        text = (r.stdout or "") + (r.stderr or "")
        for line in text.splitlines():
            low = line.lower()
            if "channel" in low or "canal" in low:
                snap["Channel"] = line.split(":", 1)[-1].strip()
            if "partial product key" in low or "clé" in low or "cle" in low:
                snap["PartialKey"] = line.split(":", 1)[-1].strip()[-5:]
            if "description" in low or "description" in low:
                snap["Description"] = line.split(":", 1)[-1].strip()[:120]
    except (OSError, subprocess.TimeoutExpired) as e:
        snap["slmgr_error"] = str(e)

    key = f"ActivationSnapshot_{label}"
    save_state({key: snap})
    try:
        import json

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f"activation-{label}.json").write_text(
            json.dumps(snap, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    log(
        f"Activation snapshot [{label}]: {snap.get('EditionID')} "
        f"channel={snap.get('Channel') or '?'} partial=***{snap.get('PartialKey') or '?'}",
        "OK",
    )
    return snap
