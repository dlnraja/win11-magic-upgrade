"""Install ALL preventive patches persistently (survive reboots).

Runtime remediation (cleanup, stop services, etc.) still runs separately.
This module WRITES durable system configuration so future upgrades are
pre-hardened — similar to installing a patch pack before Setup.
"""
from __future__ import annotations

import json
import os
import subprocess
import winreg
from datetime import datetime
from pathlib import Path

from .logutil import STATE_DIR, log

# Durable registry preventives (in addition to LabConfig / MoSetup bypass pack)
PREVENTIVE_REGISTRY: list[dict] = [
    {
        "hive": "HKLM",
        "path": r"SYSTEM\CurrentControlSet\Control\Bfsvc",
        "name": "EspPaddingPercent",
        "type": "dword",
        "value": 0,
        "why": "Prevent SRP/ESP 'could not update reserved partition' padding hard-fail",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade",
        "name": "AllowOSUpgrade",
        "type": "dword",
        "value": 1,
        "why": "Allow OS feature upgrade path via WU/Setup",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
        "name": "DisableWUfBSafeguards",
        "type": "dword",
        "value": 1,
        "why": "Prevent WUfB safeguard holds blocking feature updates",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
        "name": "ExcludeWUDriversInQualityUpdate",
        "type": "dword",
        "value": 0,
        "why": "Allow driver Dynamic Updates during quality/feature servicing",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU",
        "name": "NoAutoUpdate",
        "type": "dword",
        "value": 0,
        "why": "Ensure Windows Update is not policy-disabled",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\MoSetup",
        "name": "AllowUpgradesWithUnsupportedTPMOrCPU",
        "type": "dword",
        "value": 1,
        "why": "Prevent MoSetup HW block (persistently installed)",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassTPMCheck",
        "type": "dword",
        "value": 1,
        "why": "Preventive LabConfig TPM bypass",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassSecureBootCheck",
        "type": "dword",
        "value": 1,
        "why": "Preventive LabConfig Secure Boot bypass",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassRAMCheck",
        "type": "dword",
        "value": 1,
        "why": "Preventive LabConfig RAM bypass",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassStorageCheck",
        "type": "dword",
        "value": 1,
        "why": "Preventive LabConfig storage bypass",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassCPUCheck",
        "type": "dword",
        "value": 1,
        "why": "Preventive LabConfig CPU allow-list bypass",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\Setup\LabConfig",
        "name": "BypassDiskCheck",
        "type": "dword",
        "value": 1,
        "why": "Preventive LabConfig disk check bypass",
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
        "why": "Preventive 24H2+ HwReqChk spoof (installed permanently)",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\UpgradeEligibility",
        "name": "UpgradedSystem",
        "type": "dword",
        "value": 1,
        "why": "Preventive UpgradeEligibility soft-block reduction",
    },
    {
        "hive": "HKCU",
        "path": r"Software\Microsoft\PCHC",
        "name": "UpgradeEligibility",
        "type": "dword",
        "value": 1,
        "why": "Preventive PC Health Check eligibility",
    },
    {
        "hive": "HKLM",
        "path": r"SYSTEM\CurrentControlSet\Control\FileSystem",
        "name": "LongPathsEnabled",
        "type": "dword",
        "value": 1,
        "why": "Reduce InstallPathTooLong / MIG path failures",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore",
        "name": "RPSessionInterval",
        "type": "dword",
        "value": 1,
        "why": "Keep System Restore sessions available for rollback safety",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Microsoft\Windows\CurrentVersion\ReserveManager",
        "name": "ShippedWithReserves",
        "type": "dword",
        "value": 0,
        "why": "Reduce reserved storage soft pressure before feature upgrades",
    },
    {
        "hive": "HKLM",
        "path": r"SOFTWARE\Policies\Microsoft\WindowsStore",
        "name": "AutoDownload",
        "type": "dword",
        "value": 2,
        "why": "Limit Store auto-churn during feature upgrade windows",
    },
]

# AppCompat trees that soft-block upgrades — purge once as preventive
PREVENTIVE_DELETE_TREES = [
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\CompatMarkers",
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Shared",
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\TargetVersionUpgradeExperienceIndicators",
]

# Services that must remain available for Setup / WU
PREVENTIVE_SERVICES = {
    "wuauserv": "auto",
    "bits": "delayed-auto",
    "cryptsvc": "auto",
    "TrustedInstaller": "demand",
    "DeviceInstall": "demand",
    "msiserver": "demand",
    "WIMMount": "demand",
}


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return str(e)


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


def _delete_tree(key_path: str) -> None:
    def _del(path: str) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        sub = winreg.EnumKey(k, 0)
                        _del(path + "\\" + sub)
                    except OSError:
                        break
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            pass

    _del(key_path)


def _install_registry_item(item: dict) -> bool:
    hive = _hive(item["hive"])
    try:
        if item["type"] == "dword":
            _set_dword(hive, item["path"], item["name"], item["value"])
        elif item["type"] == "multi":
            _set_multi(hive, item["path"], item["name"], item["value"])
        else:
            return False
        return True
    except OSError as e:
        log(f"Preventive REG fail {item['name']}: {e}", "WARN")
        return False


def _export_reg_snapshot(items: list[dict], dest: Path) -> None:
    """Human-readable inventory (not a formal .reg import file for MULTI_SZ complexity)."""
    lines = [
        "Windows Registry Editor Version 5.00",
        "",
        f"; Win11 Magic Upgrade preventive pack — {datetime.now().isoformat(timespec='seconds')}",
        "; MULTI_SZ values are documented in installed-preventive-patches.json",
        "",
    ]
    for item in items:
        if item["type"] != "dword":
            continue
        root = "HKEY_LOCAL_MACHINE" if item["hive"] == "HKLM" else "HKEY_CURRENT_USER"
        lines.append(f"[{root}\\{item['path']}]")
        lines.append(f"\"{item['name']}\"=dword:{int(item['value']):08x}")
        lines.append("")
    dest.write_text("\n".join(lines), encoding="utf-8")


def configure_preventive_services() -> None:
    log("Installing preventive service start types...", "STEP")
    for svc, start in PREVENTIVE_SERVICES.items():
        # sc config syntax requires space after =
        _run(["sc", "config", svc, f"start= {start}"])
        q = _run(["sc", "query", svc])
        if "RUNNING" not in q.upper() and svc in ("wuauserv", "bits", "cryptsvc"):
            _run(["sc", "start", svc])
        log(f"Service {svc} start={start}", "OK")


def configure_preventive_power_and_pagefile() -> None:
    log("Installing preventive power/pagefile settings...", "STEP")
    _run(["powercfg", "/hibernate", "off"])
    cname = os.environ.get("COMPUTERNAME") or "localhost"
    _run(
        [
            "wmic",
            "computersystem",
            "where",
            f'name="{cname}"',
            "set",
            "AutomaticManagedPagefile=True",
        ]
    )
    log("Hibernate off + managed pagefile applied (persistent)", "OK")


def ensure_wimmount_preventive() -> None:
    """Persist WIMMount service if driver exists (prevent SafeOS mount failures)."""
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    sys_file = windir / "System32" / "drivers" / "wimmount.sys"
    q = _run(["sc", "query", "WIMMount"])
    if "SERVICE_NAME" in q.upper() or "RUNNING" in q.upper() or "STOPPED" in q.upper():
        _run(["sc", "config", "WIMMount", "start= demand"])
        return
    if not sys_file.exists():
        return
    log("Installing preventive WIMMount service registration...", "WARN")
    _run(
        [
            "sc",
            "create",
            "WIMMount",
            "type=",
            "filesys",
            "start=",
            "demand",
            "binPath=",
            r"System32\Drivers\wimmount.sys",
            "DisplayName=",
            "WIMMount",
        ]
    )
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\WIMMount",
            0,
            winreg.KEY_ALL_ACCESS,
        )
        winreg.SetValueEx(
            key,
            "ImagePath",
            0,
            winreg.REG_EXPAND_SZ,
            r"\SystemRoot\System32\Drivers\wimmount.sys",
        )
        winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, 3)
        winreg.SetValueEx(key, "Type", 0, winreg.REG_DWORD, 2)
        winreg.CloseKey(key)
    except OSError:
        pass


def install_all_preventive_patches() -> dict:
    """
    Install every durable preventive patch on the machine.
    Safe to re-run (idempotent). Does NOT replace runtime remediation.
    """
    log("=== INSTALL preventive patch pack (persistent) ===", "STEP")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    installed: list[dict] = []
    failed: list[str] = []

    # Merge: dedicated preventive list (includes LabConfig etc.) — single source for durable keys
    for item in PREVENTIVE_REGISTRY:
        ok = _install_registry_item(item)
        rec = {
            "hive": item["hive"],
            "path": item["path"],
            "name": item["name"],
            "why": item["why"],
            "ok": ok,
        }
        installed.append(rec)
        if ok:
            log(f"INSTALL REG {item['hive']}\\{item['path']}\\{item['name']} — {item['why']}", "OK")
        else:
            failed.append(item["name"])

    # Also apply full bypass pack for any keys not duplicated (CmdLine delete, etc.)
    try:
        from .bypass import apply_hardware_bypass

        apply_hardware_bypass()
    except Exception as e:
        log(f"Bypass pack during preventive install: {e}", "WARN")

    for tree in PREVENTIVE_DELETE_TREES:
        _delete_tree(tree)
        log(f"Purged AppCompat tree {tree}", "OK")

    configure_preventive_services()
    configure_preventive_power_and_pagefile()
    ensure_wimmount_preventive()

    # Persist inventory for support
    inventory = {
        "InstalledAt": datetime.now().isoformat(timespec="seconds"),
        "Computer": os.environ.get("COMPUTERNAME"),
        "CountOk": sum(1 for x in installed if x["ok"]),
        "CountFail": len(failed),
        "Failed": failed,
        "Items": installed,
        "Note": "Preventive patches are persistent. Runtime remediation still runs before each upgrade.",
    }
    inv_path = STATE_DIR / "installed-preventive-patches.json"
    inv_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    reg_path = STATE_DIR / "preventive-patches.reg"
    _export_reg_snapshot(PREVENTIVE_REGISTRY, reg_path)

    # Marker key so diagnose can report pack is present
    try:
        _set_dword(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Win11MagicUpgrade",
            "PreventivePackInstalled",
            1,
        )
        _set_dword(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Win11MagicUpgrade",
            "PreventivePackVersion",
            150,
        )
    except OSError:
        pass

    log(
        f"Preventive pack installed: {inventory['CountOk']} OK, {inventory['CountFail']} failed. "
        f"Inventory: {inv_path}",
        "OK",
    )
    log(f"Registry snapshot: {reg_path}", "INFO")
    return inventory
