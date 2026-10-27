"""
Full unlock registry pack: Media Center / DVD Player / Win8 migration / HW bypass.

Applied before every ISO hop (and on resume) so incompatible hardware can still
run Win10 22H2 → Win11 via LabConfig + MoSetup + HwReqChk, while Media Center
Key Edition keeps the correct upgrade + DVD Player entitlement markers.

Local-only: Media Center .reg snapshots live under
%LOCALAPPDATA%\\Win11MagicUpgrade\\app-carry\\_media_center\\
(never Support ZIP / GitHub).

Disable: MAGIC_SKIP_UNLOCK_PACK=1
"""
from __future__ import annotations

import json
import os
import subprocess
import winreg
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, load_state, log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CARRY_MC = STATE_DIR / "app-carry" / "_media_center"
MAGIC_UNLOCK = r"SOFTWARE\Win11MagicUpgrade\Unlock"

# Registry trees to snapshot before Media Center / Win8→10 hop
_MC_EXPORT_PATHS = (
    r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Media Center",
    r"HKLM\SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Media Center",
    r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\MCEPartner",
    r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Media Center",
)

# Win8 / 8.1 / GWX-era migration + Pro Pack style unlocks (safe DWORDs only)
_LEGACY_MIGRATION_DWORDS: list[tuple[str, str, int]] = [
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "AllowOSUpgrade", 1),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "ReservationsAllowed", 1),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "AllowOSUpgradeUnlocked", 1),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "UpgradeEligibility", 1),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "CompatibleAppsUpgraded", 1),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "RefreshAUAfterOSUpgrade", 1),
    (r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate", "AllowOSUpgrade", 1),
    (r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate", "DisableWUfBSafeguards", 1),
    # Soften GWX / Get Windows 10 soft-blocks when remnants exist
    (r"SOFTWARE\Policies\Microsoft\Windows\Gwx", "DisableGwx", 0),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "MediaCenterOffer", 1),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "DvdPlayerOffer", 1),
]

# Extra LabConfig names beyond compat.LABCONFIG_BYPASSES (harmless if unused)
_EXTRA_LABCONFIG = (
    "BypassTPMCheck",
    "BypassSecureBootCheck",
    "BypassRAMCheck",
    "BypassStorageCheck",
    "BypassCPUCheck",
    "BypassDiskCheck",
    "BypassNICCheck",
    "BypassMemoryCheck",
)


def _env_skip() -> bool:
    return os.environ.get("MAGIC_SKIP_UNLOCK_PACK", "").strip().lower() in ("1", "true", "yes")


def _set_dword(path: str, name: str, value: int, *, hive: int = winreg.HKEY_LOCAL_MACHINE) -> bool:
    try:
        key = winreg.CreateKeyEx(hive, path, 0, winreg.KEY_SET_VALUE)
        with key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
        return True
    except OSError as e:
        log(f"Unlock REG {path}\\{name}: {e}", "INFO")
        return False


def _set_sz(path: str, name: str, value: str, *, hive: int = winreg.HKEY_LOCAL_MACHINE) -> bool:
    try:
        key = winreg.CreateKeyEx(hive, path, 0, winreg.KEY_SET_VALUE)
        with key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
        return True
    except OSError as e:
        log(f"Unlock REG SZ {path}\\{name}: {e}", "INFO")
        return False


def _export_reg(full_path: str, dest: Path) -> bool:
    try:
        r = subprocess.run(
            ["reg", "export", full_path, str(dest), "/y"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode == 0 and dest.is_file()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _import_reg(reg_file: Path) -> bool:
    try:
        r = subprocess.run(
            ["reg", "import", str(reg_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def snapshot_media_center_registry() -> dict[str, Any]:
    """Export Media Center / MCE registry trees before the Win10 hop."""
    out: dict[str, Any] = {"exported": 0, "paths": []}
    CARRY_MC.mkdir(parents=True, exist_ok=True)
    for i, path in enumerate(_MC_EXPORT_PATHS):
        dest = CARRY_MC / f"mc_{i}.reg"
        if _export_reg(path, dest):
            out["exported"] += 1
            out["paths"].append(path)
            log(f"Media Center registry snapshot: {path}", "OK")
    meta = {
        "exported": out["exported"],
        "paths": out["paths"],
        "from_state": {
            "MediaCenterEligible": load_state().get("MediaCenterEligible"),
            "DvdPlayerOfferEligible": load_state().get("DvdPlayerOfferEligible"),
            "SourceEditionId": load_state().get("SourceEditionId"),
        },
    }
    (CARRY_MC / "snapshot_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    save_state({"MediaCenterRegSnapshot": meta})
    return out


def apply_win8_migration_pack(*, os_family: str = "", build: int = 0) -> int:
    """
    Windows 8 / 8.1 migration + GWX / OSUpgrade unlock keys.
    Also applied on Win10 hosts before Win11 so feature-update soft holds clear.
    """
    n = 0
    log("=== Unlock: Win8 migration / OSUpgrade / Pro Pack path ===", "STEP")
    for path, name, val in _LEGACY_MIGRATION_DWORDS:
        if _set_dword(path, name, val):
            n += 1

    fam = (os_family or "").lower()
    if fam in ("win8", "win81") or (9200 <= int(build or 0) < 10240):
        if _set_dword(r"SYSTEM\Setup", "Compact", 1):
            n += 1
            log("SYSTEM\\Setup\\Compact=1 (Win8/8.1 Setup unlock)", "OK")
        # Setup\\Status remnants can block resume on 8.x
        for name in ("CleanupInProgress", "SystemHiveCorruptionDetected"):
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\Setup\Status", 0, winreg.KEY_SET_VALUE
                ) as k:
                    try:
                        winreg.DeleteValue(k, name)
                        n += 1
                    except OSError:
                        pass
            except OSError:
                pass

    # Durable magic markers (survives edition change; never secrets)
    if _set_dword(MAGIC_UNLOCK, "Win8MigrationPack", 1):
        n += 1
    if _set_dword(MAGIC_UNLOCK, "OsUpgradeUnlocked", 1):
        n += 1
    log(f"Win8 migration unlock: {n} value(s)", "OK")
    return n


def apply_media_center_dvd_unlock(report: Any | None = None) -> int:
    """
    Media Center Key Edition + Windows DVD Player entitlement markers.

    Snapshots WMC registry before hop; writes OSUpgrade MediaCenter/DvdPlayer
    offer flags and durable Unlock markers used after EditionID becomes Pro.
    """
    n = 0
    log("=== Unlock: Media Center pack + DVD Player markers ===", "STEP")

    from .legacy_os import (
        detect_media_center,
        media_center_eligible_from_state,
        persist_media_center_eligibility,
        os_family_from_build,
    )

    build = int(getattr(report, "build", 0) or 0) if report is not None else 0
    fam = ""
    mc = media_center_eligible_from_state()
    if report is not None:
        fam = getattr(report, "os_family", "") or os_family_from_build(build)
        mc = bool(
            mc
            or getattr(report, "has_media_center", False)
            or detect_media_center(
                getattr(report, "product_name", ""),
                getattr(report, "edition_id", ""),
                build=build if build else None,
            )
        )
        persist_media_center_eligibility(report)

    # Snapshot classic WMC keys while still on 8/8.1 (or if trees remain)
    if mc or build < 10240:
        snap = snapshot_media_center_registry()
        n += int(snap.get("exported") or 0)

    for path, name, val in (
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "MediaCenterOffer", 1),
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "DvdPlayerOffer", 1),
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "AllowOSUpgrade", 1),
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "CompatibleAppsUpgraded", 1),
    ):
        if _set_dword(path, name, val):
            n += 1

    if mc or media_center_eligible_from_state():
        if _set_dword(MAGIC_UNLOCK, "MediaCenterEligible", 1):
            n += 1
        if _set_dword(MAGIC_UNLOCK, "DvdPlayerOfferEligible", 1):
            n += 1
        src_ed = str(load_state().get("SourceEditionId") or getattr(report, "edition_id", "") or "")
        if src_ed:
            _set_sz(MAGIC_UNLOCK, "SourceEditionId", src_ed)
            n += 1
        src_fam = str(load_state().get("SourceOsFamily") or fam or "")
        if src_fam:
            _set_sz(MAGIC_UNLOCK, "SourceOsFamily", src_fam)
            n += 1
        # Re-seed Ident under Media Center if key tree empty but we have eligibility
        # (harmless marker; does not reinstall classic ehome)
        if _set_sz(
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Media Center",
            "Ident",
            "Win11MagicUpgrade.CarryForward",
        ):
            n += 1
        log(
            "Media Center / DVD Player unlock markers written "
            "(eligibility also in state.json + Unlock hive)",
            "OK",
        )
    else:
        log("No Media Center / DVD eligibility — OSUpgrade unlock keys still applied", "INFO")

    return n


def restore_media_center_markers_after_hop() -> dict[str, Any]:
    """
    After Win10 inplace: EditionID is Pro and WMC files are gone.
    Re-apply offer markers + best-effort import of snapshotted .reg (safe trees).
    """
    result = {"reg_imported": 0, "markers": 0}
    if _env_skip():
        return result
    log("=== Unlock: restore Media Center / DVD markers after hop ===", "STEP")

    st = load_state()
    eligible = bool(st.get("MediaCenterEligible") or st.get("DvdPlayerOfferEligible"))
    # Also read durable hive if state wiped
    if not eligible:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, MAGIC_UNLOCK) as k:
                try:
                    v, _ = winreg.QueryValueEx(k, "DvdPlayerOfferEligible")
                    eligible = int(v) == 1
                except OSError:
                    pass
        except OSError:
            pass

    if not eligible:
        log("No DVD/Media Center eligibility to restore", "INFO")
        return result

    for path, name, val in (
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "MediaCenterOffer", 1),
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "DvdPlayerOffer", 1),
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "AllowOSUpgrade", 1),
        (MAGIC_UNLOCK, "MediaCenterEligible", 1),
        (MAGIC_UNLOCK, "DvdPlayerOfferEligible", 1),
    ):
        if _set_dword(path, name, val):
            result["markers"] += 1

    # Import snapshotted trees (best-effort; obsolete values ignored by Win10)
    if CARRY_MC.is_dir():
        for regf in sorted(CARRY_MC.glob("mc_*.reg")):
            if _import_reg(regf):
                result["reg_imported"] += 1
                log(f"Re-imported Media Center snapshot {regf.name}", "OK")
            else:
                log(f"Media Center .reg import soft-fail (expected on Win10+): {regf.name}", "INFO")

    save_state(
        {
            "MediaCenterEligible": True,
            "DvdPlayerOfferEligible": True,
            "MediaCenterMarkersRestored": result,
        }
    )
    log(
        f"DVD/Media Center markers restored (markers={result['markers']}, "
        f"reg={result['reg_imported']})",
        "OK",
    )
    return result


def apply_hardware_unlock(report: Any | None = None) -> dict[str, Any]:
    """LabConfig + MoSetup + HwReqChk + eligibility — Win11 on unsupported hardware."""
    log("=== Unlock: hardware bypass (LabConfig / MoSetup / HwReqChk) ===", "STEP")
    summary: dict[str, Any] = {"ok": False}
    # Ensure classic LabConfig DWORDs even if compat engine fails mid-way
    for name in _EXTRA_LABCONFIG:
        _set_dword(r"SYSTEM\Setup\LabConfig", name, 1)
    _set_dword(r"SYSTEM\Setup\MoSetup", "AllowUpgradesWithUnsupportedTPMOrCPU", 1)
    _set_dword(MAGIC_UNLOCK, "HardwareBypassApplied", 1)
    try:
        from .compat import make_system_win11_compatible

        summary = make_system_win11_compatible(report) or {}
        summary["ok"] = True
    except Exception as e:
        log(f"Compat engine: {e} — fallback bypass pack", "WARN")
        try:
            from .bypass import apply_hardware_bypass

            summary = apply_hardware_bypass(report) or {}
            summary["ok"] = True
        except Exception as e2:
            log(f"Hardware unlock fallback failed: {e2}", "WARN")
            summary = {"ok": False, "error": str(e2)}
    return summary


def apply_full_unlock_pack(
    report: Any | None = None,
    *,
    phase: str = "pre_setup",
) -> dict[str, Any]:
    """
    Master entry: migration + Media Center/DVD + HW unlock.

    phase:
      pre_setup   — before every ISO Setup (Win10 or Win11 hop)
      after_win10 — restore MC/DVD markers then HW unlock for Win11 hop
      claim       — before DVD Player claim / settle
    """
    result: dict[str, Any] = {
        "phase": phase,
        "migration": 0,
        "media_center": 0,
        "hw": {},
        "restore": {},
        "skipped": False,
    }
    if _env_skip():
        log("MAGIC_SKIP_UNLOCK_PACK=1 — skip unlock registry pack", "INFO")
        result["skipped"] = True
        return result

    build = int(getattr(report, "build", 0) or 0) if report is not None else 0
    fam = str(getattr(report, "os_family", "") or "") if report is not None else ""

    log(f">>> FULL UNLOCK PACK ({phase}) — migration + MC/DVD + HW bypass <<<", "STEP")

    if phase in ("pre_setup", "after_win10", "claim"):
        result["migration"] = apply_win8_migration_pack(os_family=fam, build=build)

    if phase == "pre_setup":
        result["media_center"] = apply_media_center_dvd_unlock(report)
        # Always HW unlock: needed for Win11 on unsupported HW; harmless on Win10 hop
        result["hw"] = apply_hardware_unlock(report)
    elif phase == "after_win10":
        result["restore"] = restore_media_center_markers_after_hop()
        result["media_center"] = apply_media_center_dvd_unlock(report)
        result["hw"] = apply_hardware_unlock(report)
    elif phase == "claim":
        result["restore"] = restore_media_center_markers_after_hop()
        result["media_center"] = apply_media_center_dvd_unlock(report)

    save_state({"UnlockPackLast": {"phase": phase, "build": build, "family": fam}})
    log(
        f"Unlock pack done [{phase}]: migration={result['migration']} "
        f"mc={result['media_center']} hw_ok={result.get('hw', {}).get('ok')}",
        "OK",
    )
    return result
