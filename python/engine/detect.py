"""System detection via winreg + ctypes + wmic - no PowerShell / no .NET."""
from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import winreg
from dataclasses import asdict, dataclass
from pathlib import Path

from .logutil import log


def _reg_get(root, path: str, name: str, default=None):
    try:
        with winreg.OpenKey(root, path) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return val
    except OSError:
        return default


@dataclass
class Report:
    product_name: str
    edition_id: str
    display_version: str
    build: int
    ubr: int
    is_win11: bool
    is_win10: bool
    architecture: str
    needs_intermediate: bool
    mbr2gpt_available: bool
    ram_gb: float
    free_gb: float
    locale: str
    disk_number: int
    partition_style: str
    is_uefi: bool
    secure_boot: bool
    cpu_name: str
    sse42: bool | None
    tpm_present: bool
    cpu_64bit: bool = True
    bootmgr_arch: str | None = None
    firmware_likely_ia32: bool = False
    bootmgr_mismatch: bool = False
    boot_strategy: str = "ok"

    def as_dict(self):
        return asdict(self)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def has_sse42() -> bool | None:
    try:
        # 38 = PF_SSE4_2_INSTRUCTIONS_AVAILABLE (proxy for POPCNT era CPUs)
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(38))
    except Exception:
        return None


def _wmic(*args: str) -> str:
    exe = shutil.which("wmic")
    if not exe:
        return ""
    try:
        r = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _firmware_type_code() -> int | None:
    """1=BIOS, 2=UEFI via GetFirmwareType (no admin)."""
    try:
        ft = ctypes.c_uint(0)
        if ctypes.windll.kernel32.GetFirmwareType(ctypes.byref(ft)):
            return int(ft.value)
    except Exception:
        pass
    code = _reg_get(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control",
        "PEFirmwareType",
        None,
    )
    return int(code) if code is not None else None


def _disk_style() -> tuple[int, str]:
    """
    Return (disk_number, MBR|GPT|Unknown) for the system drive.
    disk_number is -1 when unresolved (never invent 0).
    """
    if not is_admin():
        ft = _firmware_type_code()
        if ft == 2:
            return -1, "GPT"
        if ft == 1:
            return -1, "MBR"
        return -1, "Unknown"

    try:
        from .diskpart_safe import ensure_select_disk, get_system_disk_number, run_diskpart

        disk_n = get_system_disk_number()
        if disk_n is None:
            log("System disk # unresolved", "WARN")
            ft = _firmware_type_code()
            if ft == 2:
                return -1, "GPT"
            if ft == 1:
                return -1, "MBR"
            return -1, "Unknown"

        ok, out2 = ensure_select_disk(disk_n)
        if not ok:
            # Still try list style from detail
            _, out2 = run_diskpart(f"select disk {disk_n}\ndetail disk\nexit\n")
        style = "Unknown"
        if re.search(r"GPT|GUID", out2 or "", re.I):
            style = "GPT"
        elif re.search(r"MBR|Master Boot|enregistrement de d[eé]marrage", out2 or "", re.I):
            style = "MBR"
        return int(disk_n), style
    except Exception as e:
        log(f"diskpart detect failed: {e}", "WARN")
        ft = _firmware_type_code()
        if ft == 2:
            return -1, "GPT"
        if ft == 1:
            return -1, "MBR"
        return -1, "Unknown"


def _firmware_uefi() -> bool:
    ft = _firmware_type_code()
    if ft == 2:
        return True
    if ft == 1:
        return False
    _, style = _disk_style()
    return style == "GPT"


def _secure_boot() -> bool:
    val = _reg_get(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\SecureBoot\State",
        "UEFISecureBootEnabled",
        0,
    )
    return bool(val)


def _tpm_present() -> bool:
    out = _wmic("tpm", "get", "IsEnabled_InitialValue", "/value")
    if "IsEnabled_InitialValue" in out:
        return True
    # Also check TPM presence key
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\TPM",
        ):
            return True
    except OSError:
        return False


def _cpu_name() -> str:
    name = _reg_get(
        winreg.HKEY_LOCAL_MACHINE,
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        "ProcessorNameString",
        "",
    )
    return str(name).strip() or platform.processor() or "Unknown"


def _ram_gb() -> float:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return round(stat.ullTotalPhys / (1024**3), 1)
    return 0.0


def _free_gb() -> float:
    usage = shutil.disk_usage(os.environ.get("SystemDrive", "C:\\"))
    return round(usage.free / (1024**3), 1)


def collect_report() -> Report:
    cv = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    product = str(_reg_get(winreg.HKEY_LOCAL_MACHINE, cv, "ProductName", "Windows"))
    edition = str(_reg_get(winreg.HKEY_LOCAL_MACHINE, cv, "EditionID", ""))
    display = str(
        _reg_get(winreg.HKEY_LOCAL_MACHINE, cv, "DisplayVersion", None)
        or _reg_get(winreg.HKEY_LOCAL_MACHINE, cv, "ReleaseId", "")
    )
    build = int(_reg_get(winreg.HKEY_LOCAL_MACHINE, cv, "CurrentBuildNumber", 0) or 0)
    ubr = int(_reg_get(winreg.HKEY_LOCAL_MACHINE, cv, "UBR", 0) or 0)
    is_win11 = build >= 22000
    is_win10 = (not is_win11) and build >= 10240
    if is_win11 and "Windows 10" in product:
        product = product.replace("Windows 10", "Windows 11")
    arch = "x64" if platform.machine().endswith("64") else "x86"
    disk_n, style = _disk_style()
    sse = has_sse42()
    locale = str(_reg_get(winreg.HKEY_CURRENT_USER, r"Control Panel\International", "LocaleName", "en-US"))
    is_uefi = _firmware_uefi()

    bootmgr_arch = None
    firmware_ia32 = False
    boot_mismatch = False
    boot_strategy = "ok"
    cpu64 = arch == "x64"
    try:
        from .bootmgr import analyze_boot_environment, cpu_is_64bit

        cpu64 = cpu_is_64bit()
        env = analyze_boot_environment(arch, is_uefi)
        bootmgr_arch = env.bootmgr_arch
        firmware_ia32 = env.firmware_likely_ia32
        boot_mismatch = env.mismatch
        boot_strategy = env.strategy
    except Exception as e:
        log(f"Boot env probe skipped: {e}", "WARN")

    return Report(
        product_name=product,
        edition_id=edition,
        display_version=display,
        build=build,
        ubr=ubr,
        is_win11=is_win11,
        is_win10=is_win10,
        architecture=arch,
        needs_intermediate=is_win10 and build < 19045,
        mbr2gpt_available=build >= 15063,
        ram_gb=_ram_gb(),
        free_gb=_free_gb(),
        locale=locale,
        disk_number=disk_n,
        partition_style=style,
        is_uefi=is_uefi,
        secure_boot=_secure_boot(),
        cpu_name=_cpu_name(),
        sse42=sse,
        tpm_present=_tpm_present(),
        cpu_64bit=cpu64,
        bootmgr_arch=bootmgr_arch,
        firmware_likely_ia32=firmware_ia32,
        bootmgr_mismatch=boot_mismatch,
        boot_strategy=boot_strategy,
    )


def print_report(r: Report) -> None:
    log(
        f"OS: {r.product_name} {r.display_version} build {r.build}.{r.ubr} ({r.architecture})",
        "STEP",
    )
    log(f"Edition: {r.edition_id} | Locale: {r.locale} | RAM: {r.ram_gb} GB | Free: {r.free_gb} GB")
    log(f"Disk #{r.disk_number if r.disk_number is not None and r.disk_number >= 0 else '?'}: {r.partition_style} | UEFI={r.is_uefi} SecureBoot={r.secure_boot}")
    log(f"CPU: {r.cpu_name} | SSE4.2/POPCNT={r.sse42} | TPM={r.tpm_present}")
    log(
        f"Boot: OS={r.architecture} CPU64={r.cpu_64bit} bootmgr={r.bootmgr_arch or '?'} "
        f"IA32fw={r.firmware_likely_ia32} strategy={r.boot_strategy}"
    )
    try:
        from .oem_adapt import get_oem_profile

        oem = get_oem_profile()
        log(
            f"OEM: {oem.family} | {oem.manufacturer} {oem.model} | "
            f"BitLocker={oem.bitlocker} DevEnc={oem.device_encryption} "
            f"MSDM={oem.msdm_present} ToshibaHDDpw={oem.toshiba_hdd_password_likely}"
        )
        if oem.toshiba_hdd_password_likely:
            log("Toshiba/Dynabook: unlock HDD Password in BIOS if disk edits fail.", "WARN")
        if oem.encryption_blocks_mutate or oem.bitlocker == "locked":
            log("BitLocker LOCKED (or drive unreachable) — unlock first. Protection On is OK.", "ERROR")
        elif oem.bitlocker == "on":
            log("BitLocker Protection On — will suspend protectors (safe, not a lock).", "INFO")
    except Exception as e:
        log(f"OEM probe skipped: {e}", "WARN")
    log("Runtime: pure Python (no PowerShell, no .NET Framework 4.x required)", "OK")
    if r.bootmgr_mismatch:
        log("Boot Manager bitness mismatch - will repair before Win11 upgrade.", "WARN")
    if r.firmware_likely_ia32:
        log("IA32 UEFI: hybrid CSMWrap path (UEFI32->SeaBIOS->BIOS) for Win11 x64.", "WARN")
    if r.needs_intermediate:
        log("Obsolete Win10 - intermediate Win10 22H2 required before Win11.", "WARN")
    if r.partition_style == "MBR":
        log("MBR disk - will convert to GPT without wipe when possible.", "WARN")
    if r.sse42 is False:
        log("CPU lacks SSE4.2/POPCNT - Win11 24H2+ will not boot.", "ERROR")
