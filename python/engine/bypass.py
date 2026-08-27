"""Intelligent registry bypass pack for unsupported Win11 upgrades.

All keys are well-known community/Microsoft-documented setup overrides.
Applied via winreg only (no PowerShell / no .NET). Safe defaults: DWORD=1 or MULTI_SZ spoof.
"""
from __future__ import annotations

import winreg

from .logutil import log

# Canonical pack embedded in the program (do not rely on external .reg files)
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
        ],
        "why": "24H2+ hardware requirement checklist spoof",
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


def _delete_value(hive, path: str, name: str) -> None:
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_ALL_ACCESS) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def _delete_tree(path: str) -> None:
    def _del(key_path: str):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        sub = winreg.EnumKey(k, 0)
                    except OSError:
                        break
                    _del(key_path + "\\" + sub)
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, key_path)
        except OSError:
            pass

    _del(path)


def apply_hardware_bypass() -> None:
    log("Applying intelligent registry bypass pack...", "STEP")
    applied = 0
    for item in REGISTRY_PACK:
        hive = _hive(item["hive"])
        try:
            if item["type"] == "dword":
                _set_dword(hive, item["path"], item["name"], item["value"])
            elif item["type"] == "multi":
                _set_multi(hive, item["path"], item["name"], item["value"])
            elif item["type"] == "delete":
                _delete_value(hive, item["path"], item["name"])
            else:
                continue
            applied += 1
            log(f"REG {item['hive']}\\{item['path']}\\{item['name']} - {item['why']}", "OK")
        except OSError as e:
            log(f"REG skip {item['name']}: {e}", "WARN")

    for p in DELETE_TREES:
        _delete_tree(p)
        log(f"Cleared AppCompat tree {p}", "OK")

    log(f"Registry pack applied ({applied} values).", "OK")


def list_registry_pack() -> list[dict]:
    return list(REGISTRY_PACK)


def setup_bypass_args(quiet: bool = False) -> list[str]:
    args = [
        "/product",
        "server",
        "/auto",
        "upgrade",
        "/compat",
        "IgnoreWarning",
        "/dynamicupdate",
        "disable",
        "/eula",
        "accept",
        "/telemetry",
        "disable",
    ]
    if quiet:
        args += ["/quiet", "/showoobe", "none"]
    return args
