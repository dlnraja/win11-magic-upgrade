"""Intelligent registry bypass pack for unsupported Win11 upgrades.

Delegates to compat.make_system_win11_compatible for full LabConfig / MoSetup /
HwReqChk spoof / CompatData soften / SetupConfig.ini.

All keys are well-known community/Microsoft-documented setup overrides.
Applied via winreg only (no PowerShell / no .NET).
"""
from __future__ import annotations

import winreg

from .logutil import log

# Canonical pack kept for list_registry_pack() / docs (compat engine applies a superset)
REGISTRY_PACK: list[dict] = [
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\MoSetup",
        "name": "AllowUpgradesWithUnsupportedTPMOrCPU",
        "type": "dword",
        "value": 1,
        "why": "Microsoft-documented inplace bypass for unsupported TPM/CPU",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassTPMCheck",
        "type": "dword",
        "value": 1,
        "why": "Skip TPM 2.0 requirement during setup",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassSecureBootCheck",
        "type": "dword",
        "value": 1,
        "why": "Skip Secure Boot requirement",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassRAMCheck",
        "type": "dword",
        "value": 1,
        "why": "Skip 4GB+ RAM gate",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassStorageCheck",
        "type": "dword",
        "value": 1,
        "why": "Skip 64GB storage gate",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassCPUCheck",
        "type": "dword",
        "value": 1,
        "why": "Skip CPU allow-list check",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassDiskCheck",
        "type": "dword",
        "value": 1,
        "why": "Skip disk style soft checks when possible",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\HwReqChk",
        "name": "HwReqChkVars",
        "type": "multi",
        "value": [
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
        ],
        "why": "24H2+ hardware requirement checklist spoof (full set)",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\UpgradeEligibility",
        "name": "UpgradedSystem",
        "type": "dword",
        "value": 1,
        "why": "Reduce UpgradeEligibility soft-block UI",
    },
    {
        "hive": "HKCU",
        "path": r"Software\Microsoft\PCHC",
        "name": "UpgradeEligibility",
        "type": "dword",
        "value": 1,
        "why": "PC Health Check / Upgrade Assistant eligibility flag",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
        "name": "DisableWUfBSafeguards",
        "type": "dword",
        "value": 1,
        "why": "Allow feature updates despite safeguard holds",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup",
        "name": "CmdLine",
        "type": "delete",
        "value": None,
        "why": "Clear stale setup cmdline that can confuse resumes",
    },
]

DELETE_TREES = [
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\CompatMarkers",
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Shared",
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\TargetVersionUpgradeExperienceIndicators",
]


def apply_hardware_bypass(report=None) -> dict | None:
    """
    Intelligent full compatibility pass (LabConfig + HwReqChk + CompatData + SetupConfig).
    Prefer this over applying REGISTRY_PACK alone.
    """
    log("Applying intelligent compatibility bypass pack...", "STEP")
    try:
        from .compat import make_system_win11_compatible

        return make_system_win11_compatible(report)
    except Exception as e:
        log(f"Compat engine failed ({e}) — falling back to legacy REGISTRY_PACK", "WARN")
        return _apply_legacy_pack()


def _apply_legacy_pack() -> dict:
    def _hive(name: str):
        return winreg.HKEY_LOCAL_MACHINE if name == "HKLM" else winreg.HKEY_CURRENT_USER

    def _ensure(hive, path: str):
        cur = hive
        for p in path.split("\\"):
            cur = winreg.CreateKeyEx(cur, p, 0, winreg.KEY_ALL_ACCESS)
        return cur

    applied = 0
    for item in REGISTRY_PACK:
        hive = _hive(item["hive"])
        try:
            if item["type"] == "dword":
                k = _ensure(hive, item["path"])
                winreg.SetValueEx(k, item["name"], 0, winreg.REG_DWORD, int(item["value"]))
                winreg.CloseKey(k)
            elif item["type"] == "multi":
                k = _ensure(hive, item["path"])
                winreg.SetValueEx(k, item["name"], 0, winreg.REG_MULTI_SZ, list(item["value"]))
                winreg.CloseKey(k)
            elif item["type"] == "delete":
                try:
                    with winreg.OpenKey(hive, item["path"], 0, winreg.KEY_ALL_ACCESS) as k:
                        winreg.DeleteValue(k, item["name"])
                except OSError:
                    pass
            else:
                continue
            applied += 1
            log(f"REG {item['name']} - {item['why']}", "OK")
        except OSError as e:
            log(f"REG skip {item['name']}: {e}", "WARN")
    log(f"Legacy registry pack applied ({applied} values).", "OK")
    return {"RegistryApplied": applied, "Legacy": True}


def list_registry_pack() -> list[dict]:
    return list(REGISTRY_PACK)


def setup_bypass_args(quiet: bool = False) -> list[str]:
    """Always IgnoreWarning + server product path for max soft-compat bypass.

    Note: some 25H2 channels ignore /product server — media Appraiser neutralize
    (media_bypass.py) is the complementary path.
    """
    args = [
        "/product",
        "server",
        "/auto",
        "upgrade",
        "/compat",
        "IgnoreWarning",
        "/dynamicupdate",
        "enable",
        "/eula",
        "accept",
        "/telemetry",
        "disable",
    ]
    if quiet:
        args += ["/quiet", "/showoobe", "none"]
    return args
