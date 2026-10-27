"""Create offline pack folder layout for air-gapped One-Click runs."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log


def create_offline_pack(dest: Path | None = None) -> dict[str, Any]:
    """
    Scaffold:
      OfflinePack/
        README.txt
        iso/          (drop Win10/Win11/optional 8.1 ISOs)
        du_cabs/      (Dynamic Update .cab)
        preventives/  (copy of installed-preventive inventory hint)
    Set MAGIC_ISO_DIRS / MAGIC_DU_CAB_DIR / MAGIC_WIN81_ISO to these paths.
    """
    root = dest or (Path.home() / "Desktop" / "Win11MagicUpgrade-OfflinePack")
    iso = root / "iso"
    du = root / "du_cabs"
    prev = root / "preventives"
    for d in (iso, du, prev):
        d.mkdir(parents=True, exist_ok=True)
    readme = root / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "Win11 Magic Upgrade — Offline pack",
                "=" * 40,
                "1) Copy official Microsoft Win10 22H2 + Win11 ISOs into iso\\",
                "2) Optional: Win8.1 ISO as *8.1*.iso for Win8→8.1 hop",
                "3) Optional: Dynamic Update .cab into du_cabs\\",
                "4) On target PC (admin CMD):",
                f'   set MAGIC_ISO_DIRS={iso}',
                f'   set MAGIC_DU_CAB_DIR={du}',
                f'   set MAGIC_DU_OFFLINE=1',
                f'   set MAGIC_WIN81_ISO={iso}\\Win8.1.iso   (if present)',
                "5) Run Win11MagicUpgrade.exe --cli --oneclick",
                "",
                "Preventive inventory (if any) is mirrored under preventives\\.",
            ]
        ),
        encoding="utf-8",
    )
    inv = STATE_DIR / "installed-preventive-patches.json"
    if inv.is_file():
        try:
            shutil.copy2(inv, prev / inv.name)
        except OSError:
            pass
    log(f"Offline pack scaffold: {root}", "OK")
    return {"root": str(root), "iso": str(iso), "du_cabs": str(du)}
