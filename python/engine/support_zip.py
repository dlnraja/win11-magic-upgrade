"""Export SupportPack ZIP (logs + state, privacy-scrubbed paths only).

Never include app-carry/ (product keys, licenses.json, userdata backups) —
those stay under %LOCALAPPDATA%\\Win11MagicUpgrade\\app-carry\\ only.
"""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from .logutil import STATE_DIR, log


# Explicit denylist if future code walks STATE_DIR recursively
EXCLUDE_PREFIXES = ("app-carry/", "app-carry\\")

INCLUDE_NAMES = (
    "MigrationReport.txt",
    "SupportGuide.txt",
    "AppRestoreGuide.txt",
    "PostMigrationChecklist.txt",
    "state.json",
    "compat-engine.json",
    "compat-scan.json",
    "setup-recovery.json",
    "last-diagnose.json",
    "pending-app-restores.json",
    "local-stats.json",
    "activation-pre.json",
    "activation-post.json",
    "profile-audit.json",
    "driver-scan.json",
    "rollback-scan.json",
    "hardware-inventory.json",
    "SetupDiag-Results.txt",
    "SetupDiagResults.xml",
    "BitLocker-Protectors.txt",
)


def export_support_zip(dest: Path | None = None) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = dest or (Path.home() / "Desktop" / f"Win11MagicUpgrade-Support-{stamp}.zip")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in INCLUDE_NAMES:
            p = STATE_DIR / name
            if p.is_file():
                zf.write(p, arcname=name)
        panther = STATE_DIR / "Panther"
        if panther.is_dir():
            for p in panther.glob("*.log"):
                try:
                    # Cap size ~2 MB each
                    if p.stat().st_size > 2_000_000:
                        data = p.read_bytes()[-2_000_000:]
                        zf.writestr(f"Panther/{p.name}", data)
                    else:
                        zf.write(p, arcname=f"Panther/{p.name}")
                except OSError:
                    continue
    log(f"Support ZIP: {out}", "OK")
    return out
