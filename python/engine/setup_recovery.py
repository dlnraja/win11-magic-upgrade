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
  0x8007042B / 0x2000D MIGRATE_DATA — Crypto RSA / TPM-Driver-WMI / bad profiles
  Language mismatch   setupprep "not compatible with the Windows version"

Also: /product server may be blocked on some Setup builds — media Appraiser +
setupprep remains the Flyby-class path; never spoof SSE4.2/POPCNT.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log, save_state

_CODE_RE = re.compile(
    r"0xC1900101\s*[-–]?\s*(0x[0-9A-Fa-f]+)|"
    r"(0xC1900[0-9A-Fa-f]{3})|"
    r"(0xC142[0-9A-Fa-f]{4})|"
    r"(0x8007[0-9A-Fa-f]{4})|"
    r"(0x800F[0-9A-Fa-f]{4})|"
    r"(0x8024[0-9A-Fa-f]{4})|"
    r"(0x8007042[Bb])|"
    r"\b(8007042[Bb])\b",
    re.I,
)

_CRYPTO_RSA_HINT = re.compile(
    r"Crypto\\RSA|ProgramData\\Microsoft\\Crypto|"
    r"NCrypt|CNG|TPM-Driver-WMI|MigrateData|MIGRATE_DATA|"
    r"ERROR_FAIL.*arbitration|corrupt.*(cert|key|rsa)",
    re.I,
)

_MACHINEKEYS_FILE = re.compile(
    r"(?:MachineKeys|Crypto\\RSA)[\\/]([A-Za-z0-9_\-\.]{8,})",
    re.I,
)

_SECONDARY_DISK_HINT = re.compile(
    r"0x20009|80070002.*20009|secondary\s+disk|non-system\s+disk|"
    r"InstallPathTooLong|profile\s+path.{0,40}long",
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
    "0x2000d": {
        "phase": "SAFE_OS / MIGRATE_DATA",
        "cause": "Migration driver crash (often paired with 0x8007042B on 25H2)",
        "action": "Parse Panther for failing file/reg; delete corrupt ProgramData\\Microsoft\\Crypto\\RSA machine certs if flagged; try a clean local admin session; uninstall VPN leftovers; retry.",
    },
    "0x20009": {
        "phase": "SAFE_OS / FILE",
        "cause": "Missing file during apply (secondary disk / USB / bad path)",
        "action": "Offline secondary fixed disks; disconnect USB storage; check InstallPathTooLong; retry.",
    },
}

TOP_CODE_ACTIONS: dict[str, dict[str, str]] = {
    "0x8007042b": {
        "phase": "SAFE_OS / MIGRATE_DATA",
        "cause": "ERROR_FAIL / arbitration during data migrate (25H2 frequent with TPM-Driver-WMI / corrupt Crypto RSA)",
        "action": "Check SetupDiagResults.xml + setuperr for failing object; remove corrupt Crypto\\RSA machine files if safe; stop VPN/EDR; retry from a fresh local admin profile if profile migrate fails.",
    },
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
    "0x80070002": {
        "phase": "SAFE_OS / FILE",
        "cause": "ERROR_FILE_NOT_FOUND (often 0x80070002-0x20009 secondary disk / missing package)",
        "action": "Offline non-system disks; disconnect USB enclosures; verify ISO integrity; retry One-Click.",
    },
    "0xc190012e": {
        "phase": "SAFE_OS",
        "cause": "SafeOS image apply / DU package failure",
        "action": "Enable Dynamic Update (or stage MAGIC_DU_CAB_DIR offline cabs); free space; update storage drivers; retry.",
    },
    "0x800f0922": {
        "phase": "CBS / SERVICING",
        "cause": "CBS package install failed (often SRP/ESP or pending reboot)",
        "action": "Fix ESP/SRP free space; reboot once; run DISM StartComponentCleanup; retry.",
    },
    "0xc1420121": {
        "phase": "MIGRATE",
        "cause": "Hard-block during migrate / appraiser",
        "action": "Review CompatData; neutralize BlockMigration; uninstall listed blockers; retry.",
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
    migrate_data_hint: bool = False
    crypto_rsa_hint: bool = False
    secondary_disk_hint: bool = False
    machinekeys_files: list[str] = field(default_factory=list)
    setupdiag_findings: list[str] = field(default_factory=list)
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


def _setupdiag_paths() -> list[Path]:
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return [
        Path(r"C:\$WINDOWS.~BT\Sources\Panther\SetupDiagResults.xml"),
        windir / "Logs" / "SetupDiag" / "SetupDiagResults.xml",
        STATE_DIR / "SetupDiagResults.xml",
    ]


def _normalize_top_code(raw: str) -> str:
    t = (raw or "").strip().lower()
    if not t:
        return ""
    if not t.startswith("0x"):
        t = "0x" + t
    return t


def parse_setupdiag_xml(path: Path) -> list[str]:
    """Extract FailureData / ErrorCode / Remediations from SetupDiagResults.xml."""
    findings: list[str] = []
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except (OSError, ET.ParseError) as e:
        return [f"SetupDiag parse error ({path.name}): {e}"]

    # Tags vary by SetupDiag version — collect text from common nodes
    interesting = (
        "ErrorCode",
        "FailureData",
        "FailureDetails",
        "Remediation",
        "RuleName",
        "Message",
        "Name",
    )
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag not in interesting:
            continue
        text = (el.text or "").strip()
        if text and len(text) > 2:
            findings.append(f"{tag}: {text[:400]}")
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for f in findings:
        key = f.lower()
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out[:40]


def _hint_corrupt_crypto_rsa(plan: RecoveryPlan) -> None:
    """Safe advisory only — never auto-delete machine Crypto\\RSA keys."""
    rsa = (
        Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        / "Microsoft"
        / "Crypto"
        / "RSA"
        / "MachineKeys"
    )
    if not rsa.is_dir():
        plan.notes.append("Crypto\\RSA\\MachineKeys path not present")
        return
    plan.actions.append(
        "Crypto/MIGRATE_DATA hint: if SetupDiag names a corrupt MachineKeys file under "
        f"{rsa}, back it up then remove only the flagged file (admin), reboot, retry One-Click. "
        "Do not wipe the whole MachineKeys folder."
    )


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
            if _CRYPTO_RSA_HINT.search(text):
                plan.crypto_rsa_hint = True
                plan.migrate_data_hint = True
            if _SECONDARY_DISK_HINT.search(text):
                plan.secondary_disk_hint = True
            for mk in _MACHINEKEYS_FILE.findall(text):
                if mk not in plan.machinekeys_files:
                    plan.machinekeys_files.append(mk)
                    if len(plan.machinekeys_files) >= 8:
                        break
        except OSError as e:
            plan.notes.append(f"read {p}: {e}")

    for sp in _setupdiag_paths():
        if sp.is_file():
            findings = parse_setupdiag_xml(sp)
            if findings:
                plan.setupdiag_findings.extend(findings)
                plan.notes.append(f"SetupDiag: {sp}")
                joined = "\n".join(findings)
                blob_parts.append(joined)
                if _CRYPTO_RSA_HINT.search(joined) or "8007042" in joined.lower():
                    plan.crypto_rsa_hint = True
                    plan.migrate_data_hint = True

    blob = "\n".join(blob_parts)
    if not blob.strip():
        plan.notes.append("No Panther/setuperr content found yet")
        return plan

    seen: set[str] = set()
    for m in _CODE_RE.finditer(blob):
        sub = (m.group(1) or "").lower()
        top = _normalize_top_code(
            m.group(2)
            or m.group(3)
            or m.group(4)
            or m.group(5)
            or m.group(6)
            or m.group(7)
            or m.group(8)
            or ""
        )
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
            if top == "0x8007042b":
                plan.migrate_data_hint = True

    if "0x2000d" in plan.subcodes:
        plan.migrate_data_hint = True

    if plan.language_mismatch_hint:
        plan.actions.append(
            "Language mismatch hint: match ISO language to OS (Fido locale) or install matching language pack before setupprep."
        )
    if plan.esp_hint:
        plan.actions.append(
            "ESP/SRP mentioned in logs — ensure System Reserved / EFI has free space (One-Click SRP step)."
        )
    if plan.crypto_rsa_hint or plan.migrate_data_hint:
        _hint_corrupt_crypto_rsa(plan)
        for mk in plan.machinekeys_files[:5]:
            plan.actions.append(
                f"Named MachineKeys candidate (manual only): {mk} — back up then remove only if SetupDiag confirms it."
            )
    if plan.secondary_disk_hint or "0x20009" in plan.subcodes or "0x80070002" in plan.codes_found:
        plan.actions.append(
            "Secondary disk / 0x20009 hint: One-Click will try offlining non-system disks before retry."
        )
        plan.secondary_disk_hint = True
    for fd in plan.setupdiag_findings[:8]:
        plan.actions.append(f"SetupDiag: {fd}")
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
        if plan.migrate_data_hint or "0x8007042b" in plan.codes_found:
            # Soften TPM/WMI arbitration noise; never delete Crypto RSA automatically
            try:
                stop_risky_services()
                plan.auto_fixes_applied.append("migrate_data_soft_services")
            except Exception:
                pass
            try:
                from .errfix import detect_duplicate_user_profiles, warn_long_profile_paths

                detect_duplicate_user_profiles()
                warn_long_profile_paths()
                plan.auto_fixes_applied.append("profile_path_scan")
            except Exception as e:
                plan.notes.append(f"profile scan: {e}")
        if plan.secondary_disk_hint or "0x20009" in plan.subcodes:
            try:
                from .autonomy import offline_secondary_fixed_disks
                from .errfix import warn_secondary_fixed_disks

                warn_secondary_fixed_disks()
                n = offline_secondary_fixed_disks()
                plan.auto_fixes_applied.append(f"offline_secondary_disks:{n}")
            except Exception as e:
                plan.notes.append(f"secondary disks: {e}")
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
    if plan.migrate_data_hint:
        plan.notes.append(
            "MIGRATE_DATA / 0x8007042B — review SetupDiag; Crypto RSA delete is manual only"
        )

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


def verify_iso_before_setup(
    iso_path: Path,
    *,
    win: str,
    arch: str,
    min_build: int = 0,
    host_locale: str | None = None,
) -> bool:
    """
    Strict gate: inspect ISO; require setupprep or setup + matching family/arch/min_build.
    Research: wrong language ISO / missing setupprep → silent or 'not compatible' failures.
    """
    from .iso_inspect import (
        host_locale_matches_iso,
        inspect_iso,
        iso_matches_target,
    )

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

    # Language gate (when lang.ini known)
    locale = host_locale
    if not locale:
        try:
            import winreg

            locale = str(
                winreg.QueryValueEx(
                    winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International"),
                    "LocaleName",
                )[0]
            )
        except Exception:
            locale = ""
    langs = list(getattr(info, "languages", None) or [])
    if langs and locale and not host_locale_matches_iso(locale, langs):
        log(
            f"ISO rejected: language mismatch — OS locale={locale}, ISO offers {', '.join(langs[:6])}. "
            "Download matching Fido locale or install the OS language pack, then retry.",
            "ERROR",
        )
        return False
    if langs and locale:
        log(f"ISO language OK for host {locale} (media: {', '.join(langs[:6])})", "OK")

    log(
        f"ISO OK: Win{info.win_family} build {b} setupprep={info.has_setupprep}"
        + (f" lang={info.primary_lang}" if info.primary_lang else ""),
        "OK",
    )
    return True
