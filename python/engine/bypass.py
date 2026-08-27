"""Registry hardware bypasses via winreg — no PowerShell / no .NET."""
from __future__ import annotations

import winreg

from .logutil import log


def _ensure_key(root, path: str):
    parts = path.split("\\")
    cur = root
    for p in parts:
        cur = winreg.CreateKeyEx(cur, p, 0, winreg.KEY_ALL_ACCESS)
    return cur


def _set_dword(path: str, name: str, value: int = 1) -> None:
    key = _ensure_key(winreg.HKEY_LOCAL_MACHINE, path)
    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
    winreg.CloseKey(key)


def _set_multi_sz(path: str, name: str, values: list[str]) -> None:
    key = _ensure_key(winreg.HKEY_LOCAL_MACHINE, path)
    winreg.SetValueEx(key, name, 0, winreg.REG_MULTI_SZ, values)
    winreg.CloseKey(key)


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
    log("Applying Win11 hardware bypasses (winreg, no .NET)...", "STEP")
    _set_dword(r"SYSTEM\Setup\MoSetup", "AllowUpgradesWithUnsupportedTPMOrCPU", 1)
    log("MoSetup AllowUpgradesWithUnsupportedTPMOrCPU=1", "OK")

    lab = r"SYSTEM\Setup\LabConfig"
    for name in (
        "BypassTPMCheck",
        "BypassSecureBootCheck",
        "BypassRAMCheck",
        "BypassStorageCheck",
        "BypassCPUCheck",
    ):
        _set_dword(lab, name, 1)
    log("LabConfig bypasses set", "OK")

    for p in (
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\CompatMarkers",
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Shared",
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\TargetVersionUpgradeExperienceIndicators",
    ):
        _delete_tree(p)

    _set_multi_sz(
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\HwReqChk",
        "HwReqChkVars",
        [
            "SQ_SecureBootCapable=TRUE",
            "SQ_SecureBootEnabled=TRUE",
            "SQ_TpmVersion=2",
            "SQ_RamMB=8192",
        ],
    )
    log("HwReqChkVars spoofed (24H2)", "OK")

    try:
        _set_dword(
            r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
            "DisableWUfBSafeguards",
            1,
        )
    except OSError as e:
        log(f"WU policy soft-set skipped: {e}", "WARN")

    log("All registry bypasses applied.", "OK")


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
