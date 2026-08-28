"""
Post-Setup / Panther recovery — research-backed (Microsoft Support + forums 2024–2026).

Recurring codes and remediations:
  0xC1900101-0x20004  SafeOS INSTALL_RECOVERY — AV, unused SATA, drivers/BIOS
  0xC1900101-0x20017  SafeOS BOOT — storage/NVMe/RST, disk encryption, CrowdStrike
  0xC1900101-0x2000c  SafeOS WIM apply — chkdsk, disconnect peripherals
  0xC1900101-0x30018  First boot migrate — EDR/AV filters, NIC/GPU drivers
  0xC1900101-0x4000D  Second boot BSOD — driver (setupmem.dmp)
  0xC1900208          Compat apps BlockMigration
  0xC1900200 / ESP    System Reserved / ESP too small
  0xC1900107          Reboot pending / stale ~BT
  0x80070070          Disk full
  Language mismatch   setupprep "not compatible with the Windows version"

Also: /product server may be blocked on some Setup builds — media Appraiser +
setupprep remains the Flyby-class path; never spoof SSE4.2/POPCNT.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log, save_state

_CODE_RE = re.compile(
    r"0xC1900101\s*[-–]?\s*(0x[0-9A-Fa-f]+)|"
    r"(0xC1900[0-9A-Fa-f]{3})|"
    r"(0x8007[0-9A-Fa-f]{4})|"
    r"(0x800F[0-9A-Fa-f]{4})|"
    r"(0x8024[0-9A-Fa-f]{4})",
    re.I,
)

# Subcode → user-facing remediation (EN; FR via support pack)
SUBCODE_ACTIONS: dict[str, dict[str, str]] = {
    "0x20004": {
        "phase": "SAFE_OS / INSTALL_RECOVERY",
        "cause": "Outdated drivers or third-party AV during recovery environment install",
        "action": "Uninstall third-party AV temporarily; disconnect unused SATA/USB; update storage/chipset drivers and BIOS; free ESP/SRP; then retry One-Click.",
    },
    "0x20017": {
        "phase": "SAFE_OS / BOOT",
        "cause": "Driver illegal op — storage (RST/NVMe), disk encryption, or EDR (CrowdStrike)",
        "action": "Suspend BitLocker; stop EDR/AV; update Intel RST/NVMe/storage drivers; disconnect non-essential disks; retry.",
    },
    "0x2000c": {
        "phase": "SAFE_OS / WIM apply",
        "cause": "Outdated driver or disk corruption during WIM apply",
        "action": "Run chkdsk /F (scheduled); update drivers; disconnect all peripherals except keyboard/mouse/display; enable Dynamic Update; retry.",
    },
    "0x30018": {
        "phase": "FIRST_BOOT / migrate",
        "cause": "Driver hang during data migration (often AV/NIC/GPU)",
        "action": "Stop AV/EDR/VPN filters; update network and display drivers; clean boot if needed; retry.",
    },
    "0x3000d": {
        "phase": "FIRST_BOOT",
        "cause": "Driver migration failure",
        "action": "Update or uninstall problem drivers listed in Rollback\\setupapi; disconnect USB storage; retry.",
    },
    "0x4000d": {
        "phase": "SECOND_BOOT",
        "cause": "BSOD / incompatible driver after first boot",
        "action": "Inspect C:\\$WINDOWS.~BT\\Sources\\Rollback\\setupmem.dmp if present; remove recent GPU/filter drivers; retry.",
    },
    "0x40017": {
        "phase": "SECOND_BOOT",
        "cause": "Final configuration crash (peripheral drivers)",
        "action": "Unplug printers/USB docks/external disks; update chipset; retry.",
    },
}

TOP_CODE_ACTIONS: dict[str, dict[str, str]] = {
    "0xc1900208": {
        "phase": "COMPAT",
        "cause": "Incompatible application (BlockMigration)",
        "action": "Review CompatData blockers; uninstall Acronis/EaseUS/Macrium/legacy LGS if listed; One-Click softens cached BlockMigration when possible.",
    },
    "0xc1900200": {
        "phase": "ESP/SRP",
        "cause": "System Reserved / EFI partition too small or full",
        "action": "Run ESP/SRP fix (One-Click does this); if still failing, free ESP fonts/OEM or enlarge ~512 MB boot partition. Do NOT set MAGIC_SRP_CONTINUE=1 unless you accept boot risk.",
    },
    "0xc190020e": {
        "phase": "SPACE",
        "cause": "Not enough free disk space",
        "action": "Free ≥20 GB on system drive; disable hibernation; clear temp; retry.",
    },
    "0xc1900107": {
        "phase": "PENDING",
        "cause": "Reboot pending or stale $WINDOWS.~BT",
        "action": "Reboot once; One-Click cleans stale ~BT when Setup is idle; then retry.",
    },
    "0x80070070": {
        "phase": "SPACE",
        "cause": "ERROR_DISK_FULL",
        "action": "Free disk space on C: and ensure ISO staging folder has room.",
    },
    "0x8007001f": {
        "phase": "DEVICE",
        "cause": "A device attached to the system is not functioning",
        "action": "Disconnect USB storage/docks; check Device Manager problem devices; update drivers.",
    },
}


@dataclass
class RecoveryPlan:
    codes_found: list[str] = field(default_factory=list)
    subcodes: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    auto_fixes_applied: list[str] = field(default_factory=list)
    language_mismatch_hint: bool = False
    esp_hint: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _panther_paths() -> list[Path]:
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return [
        Path(r"C:\$WINDOWS.~BT\Sources\Panther\setuperr.log"),
        Path(r"C:\$WINDOWS.~BT\Sources\Panther\setupact.log"),
        Path(r"C:\$WINDOWS.~BT\Sources\Rollback\setuperr.log"),
        windir / "Panther" / "setuperr.log",
        windir / "Panther" / "setupact.log",
        STATE_DIR / "Panther" / "setuperr.log",
    ]


def scan_panther_codes(*, max_bytes: int = 2_000_000) -> RecoveryPlan:
    plan = RecoveryPlan()
    blob_parts: list[str] = []
    for p in _panther_paths():
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()[-max_bytes:]
            text = data.decode("utf-8", errors="replace")
            blob_parts.append(text)
            if re.search(
                r"not compatible with the Windows version|language|langue|setupprep\.exe is not compatible",
                text,
                re.I,
            ):
                plan.language_mismatch_hint = True
            if re.search(
                r"system reserved|partition r[eé]serv|InsufficientSystemPartition|ESP|EFI system",
                text,
                re.I,
            ):
                plan.esp_hint = True
        except OSError as e:
            plan.notes.append(f"read {p}: {e}")

    blob = "\n".join(blob_parts)
    if not blob.strip():
        plan.notes.append("No Panther/setuperr content found yet")
        return plan

    seen: set[str] = set()
    for m in _CODE_RE.finditer(blob):
        sub = (m.group(1) or "").lower()
        top = (m.group(2) or m.group(3) or m.group(4) or m.group(5) or "").lower()
        if sub and sub not in seen:
            seen.add(sub)
            plan.subcodes.append(sub)
            info = SUBCODE_ACTIONS.get(sub) or SUBCODE_ACTIONS.get(sub.lower())
            if info:
                plan.actions.append(
                    f"{sub} [{info['phase']}]: {info['cause']} → {info['action']}"
                )
            else:
                plan.actions.append(
                    f"{sub}: generic 0xC1900101 SafeOS/driver rollback — update storage/chipset, "
                    "stop AV/EDR, disconnect USB, suspend BitLocker, retry."
                )
        if top and top not in seen:
            seen.add(top)
            plan.codes_found.append(top)
            info = TOP_CODE_ACTIONS.get(top)
            if info:
                plan.actions.append(
                    f"{top} [{info['phase']}]: {info['cause']} → {info['action']}"
                )

    if plan.language_mismatch_hint:
        plan.actions.append(
            "Language mismatch hint: match ISO language to OS (Fido locale) or install matching language pack before setupprep."
        )
    if plan.esp_hint:
        plan.actions.append(
            "ESP/SRP mentioned in logs — ensure System Reserved / EFI has free space (One-Click SRP step)."
        )
    return plan


def apply_recovery_remediations(plan: RecoveryPlan | None = None) -> RecoveryPlan:
    """
    Best-effort automatic remediations after a failed Setup (resume / diagnose).
    Does not uninstall software silently — stops services, cleans stale BT, softens compat.
    """
    plan = plan or scan_panther_codes()
    log("=== Setup recovery (Panther analysis) ===", "STEP")
    if plan.codes_found or plan.subcodes:
        log(
            f"Codes: {', '.join(plan.codes_found + plan.subcodes) or 'none'}",
            "WARN",
        )
    for a in plan.actions[:12]:
        log(f"RECOVERY: {a}", "WARN")

    # Auto fixes (safe)
    try:
        from .patches import (
            clear_upgrade_leftovers,
            repair_wimmount_service,
            stop_risky_services,
            suspend_bitlocker_if_needed,
        )

        if "0xc1900107" in plan.codes_found or plan.subcodes:
            clear_upgrade_leftovers(force=False)
            plan.auto_fixes_applied.append("clear_upgrade_leftovers")
        stop_risky_services()
        plan.auto_fixes_applied.append("stop_risky_services")
        try:
            repair_wimmount_service()
            plan.auto_fixes_applied.append("repair_wimmount")
        except Exception:
            pass
        if any(s in ("0x20017", "0x20004") for s in plan.subcodes):
            try:
                suspend_bitlocker_if_needed()
                plan.auto_fixes_applied.append("suspend_bitlocker")
            except Exception:
                pass
    except Exception as e:
        plan.notes.append(f"patches remediations: {e}")

    try:
        from .compat import neutralize_compatdata_blocks
        from .errfix import apply_extra_error_fixes, scan_compatdata_blockers

        scan_compatdata_blockers()
        neutralize_compatdata_blocks()
        apply_extra_error_fixes(soft_wu_reset=True)
        plan.auto_fixes_applied.append("compat_and_errfix")
    except Exception as e:
        plan.notes.append(f"compat: {e}")

    if plan.esp_hint or any(c in ("0xc1900200",) for c in plan.codes_found):
        plan.notes.append("ESP/SRP flagged — chain will re-run fix_srp on next One-Click")

    out = STATE_DIR / "setup-recovery.json"
    try:
        import json

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan.as_dict(), indent=2), encoding="utf-8")
        log(f"Recovery plan → {out}", "OK")
    except OSError:
        pass
    save_state(
        {
            "LastRecoveryCodes": plan.codes_found + plan.subcodes,
            "LastRecoveryActions": plan.actions[:8],
        }
    )
    return plan


def write_recovery_to_support(plan: RecoveryPlan) -> None:
    """Append recovery actions into SupportGuide when possible."""
    try:
        from .support import append_recovery_section

        append_recovery_section(plan.as_dict())
    except Exception:
        # Fallback: Desktop snippet
        try:
            desk = Path.home() / "Desktop" / "SetupRecovery.txt"
            lines = ["Win11 Magic Upgrade — Setup recovery", ""]
            for a in plan.actions:
                lines.append(f"- {a}")
            desk.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass


def verify_iso_before_setup(iso_path: Path, *, win: str, arch: str, min_build: int = 0) -> bool:
    """
    Strict gate: inspect ISO; require setupprep or setup + matching family/arch/min_build.
    Research: wrong language ISO / missing setupprep → silent or 'not compatible' failures.
    """
    from .iso_inspect import inspect_iso, iso_matches_target

    log(f"Strict ISO verify: {iso_path.name} (Win{win} {arch} min_build≥{min_build})", "STEP")
    info = inspect_iso(iso_path, compute_hash=False, remount=True)
    if not getattr(info, "verified", False):
        log(
            "ISO rejected: setupprep/setup missing or build unknown — refuse Setup launch",
            "ERROR",
        )
        return False
    if not iso_matches_target(info, win, arch):
        log(
            f"ISO rejected: family/arch mismatch (got {getattr(info, 'win_family', '?')} "
            f"{getattr(info, 'architecture', '?')})",
            "ERROR",
        )
        return False
    b = int(getattr(info, "build", 0) or 0)
    if min_build and b < min_build:
        log(f"ISO rejected: build {b} < required {min_build}", "ERROR")
        return False
    if not getattr(info, "has_setupprep", False):
        log(
            "WARN: setupprep.exe missing — using setup.exe (weaker Flyby parity; language mismatch more likely)",
            "WARN",
        )
    log(
        f"ISO OK: Win{info.win_family} build {b} setupprep={info.has_setupprep}",
        "OK",
    )
    return True
