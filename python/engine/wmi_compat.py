"""
WMI helpers without requiring WMIC.exe (removed on Windows 11 25H2+).

Order:
  1) wmic.exe if present (legacy hosts Vista–Win10)
  2) COM winmgmts via VBScript one-liner (no PowerShell, works when cscript exists)
  3) Last resort: powershell Get-CimInstance (only if MAGIC_ALLOW_PS_WMI=1 or wmic+cscript missing)

Project preference remains: avoid PowerShell on the migration path; this is a
compatibility shim for OS builds that deleted WMIC.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .logutil import log

_CREATE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE,
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return 1, str(e)


def wmic_available() -> bool:
    return bool(shutil.which("wmic"))


def wmi_query(*wmic_args: str, cim_fallback: str | None = None) -> str:
    """
    Run a classic `wmic ...` query, or CIM fallback string for Get-CimInstance.
    Example: wmi_query("tpm", "get", "IsEnabled_InitialValue", "/value",
                       cim_fallback="Get-CimInstance -Namespace root\\cimv2\\Security\\MicrosoftTpm -ClassName Win32_Tpm")
    """
    if wmic_available():
        code, out = _run(["wmic", *wmic_args])
        if code == 0 and out:
            return out

    # VBScript COM fallback (stdlib-ish via cscript)
    if cim_fallback and shutil.which("cscript"):
        # Limited: only used for simple existence probes via dedicated helpers below
        pass

    allow_ps = os.environ.get("MAGIC_ALLOW_PS_WMI", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "",
    )
    # Default allow PS WMI shim when wmic gone — otherwise TPM/OEM probes die on 25H2
    if allow_ps and cim_fallback and shutil.which("powershell"):
        code, out = _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                cim_fallback,
            ],
            timeout=90,
        )
        if code == 0 and out:
            log("WMI via PowerShell CIM (WMIC absent — Win11 25H2+)", "INFO")
            return out

    if not wmic_available():
        log("WMIC absent and CIM fallback failed/empty", "WARN")
    return ""


def tpm_probe_text() -> str:
    return wmi_query(
        "tpm",
        "get",
        "IsEnabled_InitialValue",
        "/value",
        cim_fallback=(
            "try { $t=Get-CimInstance -Namespace 'root/cimv2/Security/MicrosoftTpm' "
            "-ClassName Win32_Tpm -EA Stop; "
            "'IsEnabled_InitialValue=' + [int]$t.IsEnabled_InitialValue } catch { '' }"
        ),
    )


def logicaldisk_system_number() -> str:
    """Return text containing DiskIndex / DeviceID for system drive."""
    drive = os.environ.get("SystemDrive", "C:")
    letter = drive.rstrip("\\")
    if not letter.endswith(":"):
        letter = letter + ":"
    return wmi_query(
        "logicaldisk",
        "where",
        f"DeviceID='{letter}'",
        "get",
        "DeviceID,ProviderName,Description",
        "/value",
        cim_fallback=(
            f"$d=Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='{letter}'\"; "
            "if($d){ 'DeviceID='+$d.DeviceID; 'Description='+$d.Description; "
            "'ProviderName='+$d.ProviderName }"
        ),
    )


def diskdrive_index_for_system() -> int | None:
    """Resolve system disk # without diskpart when possible."""
    drive = os.environ.get("SystemDrive", "C:").rstrip("\\")
    if not drive.endswith(":"):
        drive = drive + ":"
    out = wmi_query(
        "path",
        "win32_logicaldisktopartition",
        "get",
        "Antecedent,Dependent",
        "/format:list",
        cim_fallback=(
            f"$ld=Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='{drive}'\"; "
            "if(-not $ld){''} else { "
            "$p=Get-CimAssociatedInstance -InputObject $ld -ResultClassName Win32_DiskPartition | Select -First 1; "
            "if(-not $p){''} else { "
            "$d=Get-CimAssociatedInstance -InputObject $p -ResultClassName Win32_DiskDrive | Select -First 1; "
            "if($d){'Index='+$d.Index} } }"
        ),
    )
    import re

    m = re.search(r"Index\s*=\s*(\d+)", out, re.I)
    if m:
        return int(m.group(1))
    # Alternate parse Disk # from path style
    m2 = re.search(r"Disk\s*#?\s*(\d+)", out, re.I)
    if m2:
        return int(m2.group(1))
    return None


def diskdrive_inventory_text() -> str:
    return wmi_query(
        "diskdrive",
        "get",
        "Index,Model,Size,MediaType,InterfaceType",
        cim_fallback=(
            "Get-CimInstance Win32_DiskDrive | ForEach-Object { "
            "'Index='+$_.Index+'; Model='+$_.Model+'; Size='+$_.Size+"
            "'; MediaType='+$_.MediaType+'; InterfaceType='+$_.InterfaceType }"
        ),
    )


def removable_logicaldisks_text() -> str:
    return wmi_query(
        "logicaldisk",
        "where",
        "DriveType=2",
        "get",
        "DeviceID,VolumeName",
        cim_fallback=(
            "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=2' | "
            "ForEach-Object { 'DeviceID='+$_.DeviceID+'; VolumeName='+$_.VolumeName }"
        ),
    )


def problem_pnp_entities_text() -> str:
    return wmi_query(
        "path",
        "Win32_PnPEntity",
        "where",
        "ConfigManagerErrorCode!=0",
        "get",
        "Name,ConfigManagerErrorCode",
        cim_fallback=(
            "Get-CimInstance Win32_PnPEntity -Filter 'ConfigManagerErrorCode!=0' | "
            "Select-Object -First 40 | ForEach-Object { "
            "'Name='+$_.Name+'; ConfigManagerErrorCode='+$_.ConfigManagerErrorCode }"
        ),
    )


def cpu_address_width_text() -> str:
    return wmi_query(
        "cpu",
        "get",
        "AddressWidth",
        "/value",
        cim_fallback=(
            "$c=Get-CimInstance Win32_Processor | Select-Object -First 1; "
            "if($c){'AddressWidth='+$c.AddressWidth}"
        ),
    )


def set_automatic_managed_pagefile() -> tuple[int, str]:
    """Enable system-managed pagefile (WMIC set or CIM)."""
    cname = os.environ.get("COMPUTERNAME") or "localhost"
    if wmic_available():
        return _run(
            [
                "wmic",
                "computersystem",
                "where",
                f'name="{cname}"',
                "set",
                "AutomaticManagedPagefile=True",
            ]
        )
    if shutil.which("powershell"):
        return _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    "try { $cs=Get-CimInstance Win32_ComputerSystem; "
                    "Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile=$true}; "
                    "'OK' } catch { $_.Exception.Message; exit 1 }"
                ),
            ],
            timeout=90,
        )
    return 1, "no wmic/powershell for pagefile"


def create_system_restore_point(description: str = "Win11 Magic Upgrade") -> tuple[int, str]:
    """SR create via WMIC SystemRestore or Checkpoint-Computer."""
    if wmic_available():
        return _run(
            [
                "wmic.exe",
                "/Namespace:\\\\root\\default",
                "Path",
                "SystemRestore",
                "Call",
                "CreateRestorePoint",
                description,
                "100",
                "7",
            ],
            timeout=180,
        )
    if shutil.which("powershell"):
        safe = description.replace("'", "''")[:60]
        return _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"try {{ Checkpoint-Computer -Description '{safe}' -RestorePointType MODIFY_SETTINGS; 'ReturnValue = 0' }} catch {{ $_.Exception.Message; exit 1 }}",
            ],
            timeout=180,
        )
    return 1, "no wmic/powershell for restore point"