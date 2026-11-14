"""Keep shim and GRUB in place when Windows boot repair runs.

bcdboot's default moves Windows Boot Manager first. When Linux EFI trees are
present, repair uses /p and does not force /addfirst. SBAT opt-out is opt-in.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def linux_vendors_recorded() -> list[str]:
    try:
        from .logutil import STATE_DIR

        manifest = STATE_DIR / "boot-snapshots" / "linux-efi" / "manifest.json"
        if not manifest.is_file():
            return []
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    vendors = payload.get("vendors") if isinstance(payload, dict) else None
    return [str(item) for item in vendors or [] if str(item).strip()]


def preserve_firmware_order(vendors: list[str] | None = None) -> bool:
    if os.environ.get("MAGIC_LINUX_EFI", "").strip().lower() in ("1", "true", "yes"):
        return True
    found = vendors if vendors is not None else linux_vendors_recorded()
    return bool(found)


def sbat_opt_out_enabled() -> bool:
    return os.environ.get("MAGIC_SBAT_OPTOUT", "").strip().lower() in ("1", "true", "yes")


def grub_recovery_note() -> str:
    return (
        "Linux was found on the ESP. Windows was not moved to the front of the firmware order. "
        "If GRUB no longer lists Windows: set GRUB_DISABLE_OS_PROBER=false and run os-prober, "
        "or restore the previous order with efibootmgr -o. "
        "A shim SBAT failure can be cleared with mokutil --set-sbat-policy delete. "
        "SBAT opt-out (HKLM\\...\\SecureBoot\\SBAT OptOut=1) runs only with MAGIC_SBAT_OPTOUT=1."
    )
