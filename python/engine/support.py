"""Support enrichment: actionable remediation checklist for users / techs."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, MIGRATION_REPORT, SETUPACT, SETUPERR, log, write_migration_report


SUPPORT_CHECKLIST = [
    ("Backup", "Confirm important files backed up (OneDrive / external disk)."),
    ("Power", "Plug laptop into AC power; disable sleep during upgrade."),
    ("Secure Boot", "If using hybrid IA32 CSMWrap: disable Secure Boot in firmware."),
    ("UEFI after MBR2GPT", "After MBR->GPT: set firmware boot mode to UEFI (CSM off)."),
    ("USB", "Unplug USB disks, docks, SD cards, printers (keep keyboard/mouse)."),
    ("VPN / AV", "Uninstall or fully quit 3rd-party VPN and antivirus before retry."),
    ("Encryption", "Decrypt VeraCrypt system volume; remove leftover SetupConfig.ini."),
    ("Space", "Keep ~20 GB free on C:; ESP/SRP needs ~50 MB free (tool auto-fixes)."),
    ("ESP/MBR safety", "Tool backs up BCD before boot edits; refuses unknown disk # / locked BitLocker."),
    ("GParted rescue", "If native expand fails: see Desktop Win11MagicUpgrade-GParted-Rescue.txt + %LOCALAPPDATA%\\Win11MagicUpgrade\\rescue\\"),
    ("Reboot", "If pending reboot / 0xC1900107: reboot once, then rerun."),
    ("Logs", f"Send support: {SETUPERR} + Desktop Win11MagicUpgrade-MigrationReport.txt"),
]


def write_support_pack(extra: dict[str, Any] | None = None) -> Path:
    """Write SupportGuide.txt + refresh MigrationReport with checklist."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    guide = STATE_DIR / "SupportGuide.txt"
    lines = [
        "Win11 Magic Upgrade — Support Guide",
        "=" * 40,
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"PC: {os.environ.get('COMPUTERNAME', '?')}",
        "",
        "What the app already patched automatically",
        "-" * 40,
        "- Hardware bypass registry (TPM/CPU/SB/RAM/...)",
        "- ESP / System Reserved cleanup + enlarge (validated preflight/postflight)",
        "- BCD export backup before MBR/EFI edits; GParted Live rescue if expand fails",
        "- WIMMount / WinRE / BitLocker suspend",
        "- WU cache reset, CompatData scan, ProfileList audit",
        "- Hybrid IA32 CSMWrap staging when needed",
        "- EspPaddingPercent + language-pack orphan trim + restore point",
        "- Panther setupact.log / setuperr.log + MigrationReport.txt",
        "",
        "Manual checklist if upgrade still fails",
        "-" * 40,
    ]
    for i, (title, tip) in enumerate(SUPPORT_CHECKLIST, 1):
        lines.append(f"{i}. [{title}] {tip}")
    lines.extend(
        [
            "",
            "Key log paths",
            "-" * 40,
            f"setupact.log: {SETUPACT}",
            f"setuperr.log: {SETUPERR}",
            f"MigrationReport: {MIGRATION_REPORT}",
            f"State folder: {STATE_DIR}",
            "",
            "Windows Setup logs (if Setup already failed)",
            "-" * 40,
            r"C:\$WINDOWS.~BT\Sources\Panther\setuperr.log",
            r"C:\$WINDOWS.~BT\Sources\Rollback\setupact.log",
            r"C:\Windows\Panther\setuperr.log",
            "",
            "CLI helpers",
            "-" * 40,
            "Win11MagicUpgrade.exe --cli --diagnose",
            "Win11MagicUpgrade.exe --cli --install-patches",
            "Win11MagicUpgrade.exe --cli --patch",
            "Win11MagicUpgrade.exe --cli --patch-deep",
            "Win11MagicUpgrade.exe --cli --srp",
            "Win11MagicUpgrade.exe --cli --hybrid",
            "",
            "Preventive vs runtime",
            "-" * 40,
            "--install-patches : durable REG/services installed on the PC (survive reboot)",
            "--patch / One-Click : install preventives + runtime remediation each run",
            "Inventory: %LOCALAPPDATA%\\Win11MagicUpgrade\\installed-preventive-patches.json",
            "",
        ]
    )
    if extra:
        lines.append("Session extras")
        lines.append("-" * 40)
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
        lines.append("")

    body = "\n".join(lines)
    guide.write_text(body, encoding="utf-8", errors="replace")

    # Desktop copy
    desk = Path.home() / "Desktop"
    if desk.is_dir():
        try:
            (desk / "Win11MagicUpgrade-SupportGuide.txt").write_text(body, encoding="utf-8", errors="replace")
        except Exception:
            pass

    log(f"Support guide written: {guide}", "OK")
    write_migration_report(
        title="Win11 Magic Upgrade — Support / Enrichment Report",
        extra={"Result": "SUPPORT_PACK", "SupportGuide": str(guide), **(extra or {})},
    )
    return guide
