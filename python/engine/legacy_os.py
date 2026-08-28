"""
Legacy Windows host support (Vista / 7 / 8 / 8.1) and edition blockers.

Research-backed notes (forums + Microsoft docs):
  - Win7 SP1 / Win8.1: in-place upgrade to Win10 still works with retail ISO + setupprep.
  - Win8.1 Pro + Media Center: Setup blocks "different edition" — fix with sources\\ei.cfg + pid.txt
    (force Professional) and setupprep.exe (not root setup.exe).
  - Vista: no supported in-place jump to Win10; we still attempt Win10 22H2 ISO (keep-apps when Setup allows).
  - Win7 MBR: mbr2gpt runs after first Win10 hop (1703+ ships mbr2gpt).
  - Registry: AllowOSUpgrade, Setup\\Compact (8.1), MoSetup on Win10+ hops.
"""
from __future__ import annotations

import winreg
from pathlib import Path
from typing import Any

from .logutil import log

# NT kernel build numbers (CurrentBuildNumber)
BUILD_VISTA = (6000, 6001, 6002)
BUILD_WIN7 = (7600, 7601)
BUILD_WIN8 = (9200,)
BUILD_WIN81 = (9600, 9601)
BUILD_WIN10_MIN = 10240

# Generic Win10 Pro install key (edition selection only — not activation)
WIN10_PRO_GENERIC_PID = "VK7JG-NPHTM-C97JM-9MPGT-3V66T"


def os_family_from_build(build: int) -> str:
    if build >= 22000:
        return "win11"
    if build >= BUILD_WIN10_MIN:
        return "win10"
    if build in BUILD_WIN81 or (9600 <= build < BUILD_WIN10_MIN):
        return "win81"
    if build in BUILD_WIN8:
        return "win8"
    if build in BUILD_WIN7 or (7600 <= build < 9200):
        return "win7"
    if build in BUILD_VISTA or (6000 <= build < 7600):
        return "vista"
    return "unknown"


def detect_media_center(product_name: str, edition_id: str) -> bool:
    blob = f"{product_name} {edition_id}".lower()
    return (
        "mediacenter" in blob.replace(" ", "")
        or "media center" in blob
        or edition_id.lower() in ("coremediacenter", "professionalmediacenter")
    )


def is_legacy_host(build: int, os_family: str) -> bool:
    return os_family in ("vista", "win7", "win8", "win81")


def legacy_label(os_family: str, build: int) -> str:
    labels = {
        "vista": "Windows Vista",
        "win7": "Windows 7",
        "win8": "Windows 8",
        "win81": "Windows 8.1",
    }
    if os_family in labels:
        return labels[os_family]
    return f"Windows (build {build})"


def _reg_dword(root, path: str, name: str, value: int) -> bool:
    try:
        try:
            k = winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE)
        except OSError:
            k = winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE)
        with k:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))
        return True
    except OSError as e:
        log(f"Registry {path}\\{name}: {e}", "INFO")
        return False


def apply_legacy_host_registry(report: Any) -> dict[str, int]:
    """
    Host-side registry prep so old Windows allows upgrade to Win10 media.
    Safe DWORD/MULTI_SZ only — no secrets.
    """
    summary = {"keys_set": 0, "media_center": 0}
    fam = getattr(report, "os_family", "") or os_family_from_build(int(report.build))
    if not is_legacy_host(int(report.build), fam):
        return summary

    log(f"=== Legacy host registry prep ({legacy_label(fam, report.build)}) ===", "STEP")

    pairs = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "AllowOSUpgrade", 1),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "ReservationsAllowed", 1),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate", "AllowOSUpgrade", 1),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate", "SusClientIdValidation", 0),
    ]
    # Win8 / 8.1 — Setup Compact missing blocks some Win10 upgrades (Sysnative forums)
    if fam in ("win8", "win81"):
        pairs.append((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\Setup", "Compact", 1))

    for root, path, name, val in pairs:
        if _reg_dword(root, path, name, val):
            summary["keys_set"] += 1
            log(f"Set {path}\\{name}={val}", "OK")

    if getattr(report, "has_media_center", False) or detect_media_center(
        getattr(report, "product_name", ""), getattr(report, "edition_id", "")
    ):
        summary["media_center"] = 1
        # Best-effort: allow upgrade eligibility flags on 8.x
        for path, name in (
            (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "AllowOSUpgrade"),
            (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "CompatibleAppsUpgraded"),
        ):
            if _reg_dword(winreg.HKEY_LOCAL_MACHINE, path, name, 1):
                summary["keys_set"] += 1
        log("Media Center edition — will patch Setup media (ei.cfg + pid.txt) before launch", "WARN")

    return summary


def write_media_center_edition_bypass(sources: Path, *, edition: str = "Professional") -> bool:
    """
    Win8.1 Pro + Media Center → Win10 Pro in-place:
    sources\\ei.cfg + sources\\pid.txt on installation media.
    """
    sources.mkdir(parents=True, exist_ok=True)
    ei = sources / "ei.cfg"
    pid = sources / "pid.txt"
    ei_body = "\r\n".join(
        [
            f"[EditionID]",
            edition,
            "",
            "[Channel]",
            "Retail",
            "",
            "[VL]",
            "0",
            "",
        ]
    )
    pid_body = "\r\n".join(
        [
            "[PID]",
            f"Value={WIN10_PRO_GENERIC_PID}",
            "",
        ]
    )
    try:
        ei.write_text(ei_body, encoding="ascii")
        pid.write_text(pid_body, encoding="ascii")
        log(f"Media Center bypass: wrote {ei.name} + {pid.name} (→ {edition})", "OK")
        return True
    except OSError as e:
        log(f"Media Center media patch failed: {e}", "WARN")
        return False


def prepare_legacy_setup_media(
    iso_root: str | Path,
    report: Any,
    *,
    win: str,
) -> Path:
    """
    Writable staging for legacy hops: Media Center ei.cfg, SetupConfig, Appraiser neutralize.
    """
    from .media_bypass import neutralize_appraiser_on_media, stage_writable_setup, write_media_setupconfig

    root = Path(iso_root)
    fam = getattr(report, "os_family", "") or os_family_from_build(int(report.build))
    mc = getattr(report, "has_media_center", False) or detect_media_center(
        getattr(report, "product_name", ""), getattr(report, "edition_id", "")
    )
    needs_stage = mc or fam in ("vista", "win7", "win8", "win81") or win == "11"

    if needs_stage:
        try:
            root = stage_writable_setup(root, force=False)
        except Exception as e:
            log(f"Legacy media stage fallback to mount: {e}", "WARN")

    sources = root / "sources"
    if mc or (fam == "win81" and "professional" in str(getattr(report, "edition_id", "")).lower()):
        write_media_center_edition_bypass(sources)

    if fam in ("vista", "win7", "win8", "win81"):
        write_media_setupconfig(root)
        neutralize_appraiser_on_media(root)

    if win == "11":
        from .media_bypass import prepare_setup_root

        try:
            root = prepare_setup_root(root, win11=True)
        except Exception as e:
            log(f"Win11 media stage fallback: {e}", "WARN")

    return root


def inplace_notes_for_legacy(report: Any) -> list[str]:
    fam = getattr(report, "os_family", "")
    notes: list[str] = []
    if fam == "vista":
        notes.append(
            "Windows Vista has no Microsoft-supported in-place path to Win10/11; "
            "Setup may offer keep-files upgrade only on some configs — otherwise backup required."
        )
    if fam == "win7":
        notes.append("Windows 7 SP1 → Win10 22H2 in-place is community/Microsoft-documented; then Win11 chain.")
    if fam in ("win8", "win81"):
        notes.append("Windows 8.x → Win10 22H2 via setupprep.exe on staged ISO.")
    if getattr(report, "has_media_center", False):
        notes.append("Media Center edition: Pro target forced via ei.cfg/pid.txt on Setup media.")
    return notes
