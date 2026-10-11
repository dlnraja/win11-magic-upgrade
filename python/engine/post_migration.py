"""Post-migration checklist after One-Click completes."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log, save_state


def write_post_migration_checklist(report: Any | None = None) -> Path:
    build = int(getattr(report, "build", 0) or 0) if report else 0
    product = getattr(report, "product_name", "") if report else ""
    lines = [
        "Win11 Magic Upgrade — Post-migration checklist",
        "=" * 48,
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"OS: {product} build {build}",
        "",
        "[ ] Confirm Windows activation (Settings → System → Activation)",
        "[ ] Windows Update → Check for updates (drivers + .NET)",
        "[ ] Verify apps/files still present (Desktop, Documents)",
        "[ ] Windows DVD Player / VLC if you need optical discs",
        "[ ] Reinstall any apps listed in AppRestoreGuide.txt",
        "[ ] Firmware: UEFI mode (CSM off) after MBR→GPT",
        "[ ] Secure Boot: enable only if all boot entries OK",
        "[ ] BitLocker: resume protection if it was suspended",
        "[ ] Create a restore point / new backup",
        "[ ] Remove leftover ISO mounts from Explorer",
        "[ ] Optional: uninstall old OEM bloat only after drivers OK",
        "",
        f"State folder: {STATE_DIR}",
        f"Logs: {STATE_DIR / 'Panther'}",
    ]
    path = STATE_DIR / "PostMigrationChecklist.txt"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        desk = Path.home() / "Desktop" / "Win11MagicUpgrade-PostMigration.txt"
        desk.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass
    save_state({"PostMigrationChecklist": str(path)})
    log(f"Post-migration checklist: {path}", "OK")
    return path


def run_post_migration(report: Any | None = None) -> dict[str, Any]:
    try:
        from .activation import snapshot_activation

        snapshot_activation(label="post")
    except Exception as e:
        log(f"Post activation snapshot: {e}", "WARN")
    path = write_post_migration_checklist(report)
    # Final restore pass if anything left
    try:
        from .blocker_apps import restore_queued_apps

        restore_queued_apps(host_build=int(getattr(report, "build", 0) or 0) if report else None)
    except Exception as e:
        log(f"Post restore apps: {e}", "WARN")
    return {"checklist": str(path)}
