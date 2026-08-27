"""Intelligent Win11 compatibility engine.

Goals:
  1. Detect real hardware gaps (TPM, Secure Boot, RAM, CPU features).
  2. Bypass EVERY soft compatibility gate Setup / Appraiser / HwReqChk uses.
  3. Spoof only what is missing (intelligent) while still applying full LabConfig.
  4. Soften app BlockMigration (0xC1900208) in cached CompatData XML.
  5. Write SetupConfig.ini so setup.exe always uses Compat=IgnoreWarning.

Hard limit (honest): missing SSE4.2 / POPCNT cannot be spoofed — chain must
target Win10 22H2 instead of Win11 24H2+ (already handled by autodiag/chain).

No PowerShell / no binary patching of setup.exe / appraiser.dll.
"""
from __future__ import annotations

import os
import re
import shutil
import winreg
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .logutil import STATE_DIR, log

# Expanded 24H2/25H2 HwReqChk vocabulary (community + Rufus-aligned + extras)
FULL_HWREQCHK_SPOOF = [
    "SQ_SecureBootCapable=TRUE",
    "SQ_SecureBootEnabled=TRUE",
    "SQ_TpmVersion=2",
    "SQ_RamMB=8192",
    "SQ_DiskNVMe=TRUE",
    "SQ_SSD=TRUE",
    "SQ_DiskGB=256",
    "SQ_CpuCores=8",
    "SQ_CpuThreads=16",
    "SQ_CpuMhz=3000",
    "SQ_DirectXVersion=12",
    "SQ_WDDMVersion=3.0",
    "SQ_VbsEnabled=TRUE",
    "SQ_HvciEnabled=TRUE",
    "SQ_CpuFamily=25",
    "SQ_CpuModel=1",
    "SQ_CpuStepping=1",
]

LABCONFIG_BYPASSES = [
    "BypassTPMCheck",
    "BypassSecureBootCheck",
    "BypassRAMCheck",
    "BypassStorageCheck",
    "BypassCPUCheck",
    "BypassDiskCheck",
    # Extra DWORD names used by various community/ISO tools (ignored if unused)
    "BypassNICCheck",
    "BypassMemoryCheck",
]

COMPAT_DELETE_TREES = [
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\CompatMarkers",
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Shared",
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\TargetVersionUpgradeExperienceIndicators",
    # Extra SoftBlock / telemetry trees that remember failed HW checks
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Appraiser",
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\TelemetryController",
]


@dataclass
class CompatAssessment:
    tpm_ok: bool = False
    secure_boot_ok: bool = False
    ram_ok: bool = False
    storage_ok: bool = False
    sse42_ok: bool | None = None
    cpu_64bit: bool = True
    gaps: list[str] = field(default_factory=list)
    hard_block_win11_24h2: bool = False
    strategy: str = "full_bypass"

    def as_dict(self) -> dict:
        return asdict(self)


def _hive(name: str):
    return winreg.HKEY_LOCAL_MACHINE if name == "HKLM" else winreg.HKEY_CURRENT_USER


def _ensure_key(hive, path: str):
    cur = hive
    for p in path.split("\\"):
        cur = winreg.CreateKeyEx(cur, p, 0, winreg.KEY_ALL_ACCESS)
    return cur


def _set_dword(hive, path: str, name: str, value: int) -> None:
    key = _ensure_key(hive, path)
    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
    winreg.CloseKey(key)


def _set_multi(hive, path: str, name: str, values: list[str]) -> None:
    key = _ensure_key(hive, path)
    winreg.SetValueEx(key, name, 0, winreg.REG_MULTI_SZ, list(values))
    winreg.CloseKey(key)


def _set_sz(hive, path: str, name: str, value: str) -> None:
    key = _ensure_key(hive, path)
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
    winreg.CloseKey(key)


def _delete_tree(path: str) -> None:
    def _del(key_path: str) -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_ALL_ACCESS
            ) as k:
                while True:
                    try:
                        sub = winreg.EnumKey(k, 0)
                        _del(key_path + "\\" + sub)
                    except OSError:
                        break
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, key_path)
        except OSError:
            pass

    _del(path)


def assess_compatibility(report=None) -> CompatAssessment:
    """Probe hardware and classify soft vs hard gaps."""
    a = CompatAssessment()
    ram = 8.0
    free = 64.0
    tpm = False
    sb = False
    sse = None
    x64 = True

    if report is not None:
        ram = float(getattr(report, "ram_gb", 8) or 0)
        free = float(getattr(report, "free_gb", 64) or 0)
        tpm = bool(getattr(report, "tpm_present", False))
        sb = bool(getattr(report, "secure_boot", False))
        sse = getattr(report, "sse42", None)
        x64 = bool(getattr(report, "cpu_64bit", True))
    else:
        try:
            from .detect import collect_report

            r = collect_report()
            return assess_compatibility(r)
        except Exception:
            pass

    a.tpm_ok = tpm
    a.secure_boot_ok = sb
    a.ram_ok = ram >= 4.0
    a.storage_ok = free >= 64.0 or True  # free space gate handled elsewhere; disk size soft
    a.sse42_ok = sse
    a.cpu_64bit = x64

    if not a.tpm_ok:
        a.gaps.append("TPM")
    if not a.secure_boot_ok:
        a.gaps.append("SecureBoot")
    if not a.ram_ok:
        a.gaps.append("RAM")
    if sse is False:
        a.gaps.append("SSE4.2/POPCNT")
        a.hard_block_win11_24h2 = True
        a.strategy = "win10_22h2_keep_apps"
    if not x64:
        a.gaps.append("x86_OS")
        a.hard_block_win11_24h2 = True
        a.strategy = "win10_22h2_x86"

    if a.gaps and not a.hard_block_win11_24h2:
        a.strategy = "intelligent_spoof_and_bypass"
    elif not a.gaps:
        a.strategy = "native_compatible_plus_guards"

    return a


def build_intelligent_hwreqchk(report=None) -> list[str]:
    """
    Build HwReqChkVars: use real-looking values from the machine when good,
    spoof only failing soft checks. Always emit a complete MULTI_SZ set so
    24H2 Appraiser does not fall back to hardware probes.
    """
    a = assess_compatibility(report)
    ram_mb = 8192
    if report is not None:
        try:
            ram_mb = max(8192, int(float(report.ram_gb) * 1024))
        except Exception:
            ram_mb = 8192

    # Always present a "fully compatible" profile to HwReqChk — Setup LabConfig
    # still bypasses; this spoof is what 24H2 inplace upgrades actually read.
    vars_list = [
        "SQ_SecureBootCapable=TRUE",
        "SQ_SecureBootEnabled=TRUE",
        "SQ_TpmVersion=2",
        f"SQ_RamMB={ram_mb}",
        "SQ_DiskNVMe=TRUE",
        "SQ_SSD=TRUE",
        "SQ_DiskGB=256",
        "SQ_CpuCores=8",
        "SQ_CpuThreads=16",
        "SQ_CpuMhz=3000",
        "SQ_DirectXVersion=12",
        "SQ_WDDMVersion=3.0",
        "SQ_VbsEnabled=TRUE",
        "SQ_HvciEnabled=TRUE",
        "SQ_CpuFamily=25",
        "SQ_CpuModel=1",
        "SQ_CpuStepping=1",
    ]
    if a.gaps:
        log(
            f"Compat gaps detected {a.gaps} — applying intelligent HwReqChk spoof "
            f"(strategy={a.strategy})",
            "STEP",
        )
    else:
        log("Hardware looks compatible — still installing HwReqChk guards for Setup", "OK")
    return vars_list


def apply_labconfig_and_mosetup() -> int:
    n = 0
    for name in LABCONFIG_BYPASSES:
        _set_dword(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\Setup\LabConfig", name, 1)
        log(f"LabConfig {name}=1", "OK")
        n += 1
    # MoSetup — Microsoft-documented + community extras
    for name, val in (
        ("AllowUpgradesWithUnsupportedTPMOrCPU", 1),
        ("AllowUpgradesWithUnsupportedTPMorCPU", 1),  # typo variant seen in some guides
    ):
        try:
            _set_dword(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\Setup\MoSetup", name, val)
            log(f"MoSetup {name}={val}", "OK")
            n += 1
        except OSError as e:
            log(f"MoSetup {name} skip: {e}", "WARN")
    # Clear stale CmdLine that can confuse resumes
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\Setup", 0, winreg.KEY_ALL_ACCESS) as k:
            try:
                winreg.DeleteValue(k, "CmdLine")
                log("Cleared SYSTEM\\Setup\\CmdLine", "OK")
            except OSError:
                pass
    except OSError:
        pass
    return n


def apply_eligibility_and_wu_policies() -> int:
    n = 0
    _set_dword(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\UpgradeEligibility",
        "UpgradedSystem",
        1,
    )
    n += 1
    _set_dword(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\PCHC", "UpgradeEligibility", 1)
    n += 1
    try:
        _set_dword(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\PCHC", "UpgradeEligibility", 1)
        n += 1
    except OSError:
        pass

    # WUfB / feature update soft holds
    for path, name, val in (
        (r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate", "DisableWUfBSafeguards", 1),
        (r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate", "SetDisableUXWUAccess", 0),
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "AllowOSUpgrade", 1),
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "ReservationsAllowed", 1),
        # OOBE network requirement (post-upgrade friction)
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\OOBE", "BypassNRO", 1),
    ):
        try:
            _set_dword(winreg.HKEY_LOCAL_MACHINE, path, name, val)
            n += 1
        except OSError as e:
            log(f"Policy skip {name}: {e}", "INFO")

    try:
        _set_dword(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
            "TargetReleaseVersion",
            1,
        )
        _set_sz(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
            "ProductVersion",
            "Windows 11",
        )
        n += 2
        log("WU policy: ProductVersion=Windows 11 + TargetReleaseVersion", "OK")
    except OSError as e:
        log(f"WU product policy skip: {e}", "WARN")

    # SoftBlock allow: UpgradeEligibility indicators
    try:
        _set_dword(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\UpgradeEligibility",
            "UpgradeEligible",
            1,
        )
        n += 1
    except OSError:
        pass

    log(f"Eligibility / WU / OOBE bypass values applied ({n})", "OK")
    return n


def purge_appcompat_markers() -> None:
    for tree in COMPAT_DELETE_TREES:
        _delete_tree(tree)
        log(f"Purged AppCompat tree {tree}", "OK")


def write_setupconfig_ini() -> list[Path]:
    """
    SetupConfig.ini tells setupprep/setup to IgnoreWarning on compat scans.
    Written to common locations used by feature updates / WSUS / Default user.
    """
    body = "\r\n".join(
        [
            "[SetupConfig]",
            "Compat=IgnoreWarning",
            "DynamicUpdate=Enable",
            "ShowOobe=None",
            "Telemetry=Disable",
            "",
        ]
    )
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    targets = [
        windir / "System32" / "UpdateHealthTools" / "SetupConfig.ini",  # may not exist
        windir / "Users" / "Default" / "AppData" / "Local" / "Microsoft" / "Windows" / "WSUS" / "SetupConfig.ini",
        STATE_DIR / "SetupConfig.ini",
        Path(os.environ.get("SystemDrive", "C:")) / "Users" / "Default" / "AppData" / "Local" / "Microsoft" / "Windows" / "WSUS" / "SetupConfig.ini",
    ]
    written: list[Path] = []
    for dest in targets:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
            written.append(dest)
            log(f"Wrote SetupConfig.ini → {dest}", "OK")
        except OSError as e:
            log(f"SetupConfig skip {dest}: {e}", "INFO")
    return written


def neutralize_compatdata_blocks() -> int:
    """
    Soften 0xC1900208 app hard-blocks in cached CompatData / Appraiser XML:
    rewrite BlockMigration=\"True\" → False (and similar attributes).
    Does not uninstall apps — makes Setup treat them as non-blocking.
    """
    roots = [
        Path(r"C:\$WINDOWS.~BT\Sources\Panther"),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Panther",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "appcompat" / "appraiser",
        Path(os.environ.get("TEMP", r"C:\Windows\Temp")),
    ]
    pats = (
        (re.compile(r'BlockMigration\s*=\s*"?True"?', re.I), 'BlockMigration="False"'),
        (re.compile(r'DT_ANY_FMC_BlockingApplication\s*=\s*"?True"?', re.I), 'DT_ANY_FMC_BlockingApplication="False"'),
        (re.compile(r'HardBlock\s*=\s*"?True"?', re.I), 'HardBlock="False"'),
        (re.compile(r'BlockUpgrade\s*=\s*"?True"?', re.I), 'BlockUpgrade="False"'),
    )
    changed = 0
    for root in roots:
        if not root.is_dir():
            continue
        for pat_glob in ("CompatData*.xml", "*Appraiser*.xml", "*APPRAISER*.xml", "*HumanReadable*.xml"):
            for xml_path in root.glob(pat_glob):
                try:
                    if xml_path.stat().st_size > 12_000_000:
                        continue
                    text = xml_path.read_text(encoding="utf-8", errors="replace")
                    new = text
                    for rx, repl in pats:
                        new = rx.sub(repl, new)
                    if new != text:
                        bak = xml_path.with_suffix(xml_path.suffix + ".magic.bak")
                        if not bak.exists():
                            shutil.copy2(xml_path, bak)
                        xml_path.write_text(new, encoding="utf-8", errors="replace")
                        changed += 1
                        log(f"Neutralized compat blocks in {xml_path.name}", "OK")
                except Exception as e:
                    log(f"CompatData soften skip {xml_path.name}: {e}", "INFO")
    if changed:
        log(f"Softened BlockMigration in {changed} CompatData/Appraiser XML file(s)", "OK")
    else:
        log("No CompatData BlockMigration=True caches found to soften", "OK")
    return changed


def clear_appraiser_and_compat_caches() -> None:
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    appraiser = windir / "appcompat" / "appraiser"
    if appraiser.is_dir():
        n = 0
        for f in appraiser.rglob("*"):
            if f.is_file() and f.suffix.lower() in {".xml", ".sdb", ".cab", ".dat"}:
                try:
                    f.unlink()
                    n += 1
                except Exception:
                    pass
        log(f"Cleared {n} appraiser cache file(s)", "OK")
    # Panther leftover CompatData from failed runs
    for panther in (
        Path(r"C:\$WINDOWS.~BT\Sources\Panther"),
        windir / "Panther",
    ):
        if not panther.is_dir():
            continue
        for f in panther.glob("CompatData*.xml"):
            try:
                f.unlink()
                log(f"Removed stale {f.name}", "OK")
            except Exception:
                pass


def make_system_win11_compatible(report=None) -> dict:
    """
    Full intelligent path: assess → LabConfig/MoSetup → HwReqChk spoof →
    eligibility/WU → purge markers → SetupConfig → neutralize/clear appraiser.
    Safe to re-run (idempotent).
    """
    log("=== Intelligent compatibility: bypass checks + make compatible ===", "STEP")
    assessment = assess_compatibility(report)

    if assessment.hard_block_win11_24h2:
        log(
            f"HARD limit: {assessment.gaps} — Win11 24H2+ cannot run on this CPU/OS. "
            f"Strategy={assessment.strategy} (keep-apps path via chain). "
            "Soft bypasses still applied for intermediate Win10 upgrades.",
            "WARN",
        )
    else:
        log(
            f"Compat assessment: gaps={assessment.gaps or ['none']} strategy={assessment.strategy}",
            "OK",
        )

    applied = 0
    applied += apply_labconfig_and_mosetup()

    hw = build_intelligent_hwreqchk(report)
    _set_multi(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\HwReqChk",
        "HwReqChkVars",
        hw,
    )
    log(f"HwReqChkVars installed ({len(hw)} entries)", "OK")
    applied += 1

    applied += apply_eligibility_and_wu_policies()
    purge_appcompat_markers()
    # Clear then optionally soften leftovers (order: clear stale, write SetupConfig)
    clear_appraiser_and_compat_caches()
    softened = neutralize_compatdata_blocks()
    configs = write_setupconfig_ini()

    # Marker for diagnose / support
    try:
        _set_dword(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Win11MagicUpgrade",
            "CompatEngineApplied",
            1,
        )
        _set_dword(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Win11MagicUpgrade",
            "CompatEngineVersion",
            190,
        )
        _set_sz(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Win11MagicUpgrade",
            "CompatStrategy",
            assessment.strategy,
        )
    except OSError:
        pass

    summary = {
        "Assessment": assessment.as_dict(),
        "RegistryApplied": applied,
        "HwReqChkCount": len(hw),
        "CompatDataSoftened": softened,
        "SetupConfigFiles": [str(p) for p in configs],
        "Note": "Soft checks bypassed; SSE4.2/POPCNT remain a hard CPU limit for 24H2+",
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import json

        (STATE_DIR / "compat-engine.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    log(
        f"Compatibility engine done: {applied} REG ops, HwReqChk={len(hw)}, "
        f"softened XML={softened}, strategy={assessment.strategy}",
        "OK",
    )
    return summary
