"""
Smart Boot Manager / UEFI bitness handling for Win11 x64 upgrades.

Scenarios (forums + Microsoft UEFI rules):
  A) OS x64 + wrong/stale IA32 boot files on ESP  -> fix with bcdboot (safe)
  B) OS x86 + CPU x64 + UEFI IA32 firmware        -> Win11 impossible; max Win10 22H2 x86
  C) OS x86 + CPU x64 + Legacy/BIOS               -> no inplace Win11; clean x64 only if CSM
  D) OS x64 + BIOS boot, UEFI-capable disk GPT    -> repair to UEFI x64 bootmgr

Windows UEFI requires matching bitness for native boot.
For IA32-only firmware + x64 CPU we use a hybrid CSMWrap bridge:
  IA32 UEFI -> CSMWrap -> SeaBIOS -> BIOS bootmgr -> Windows x64
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .logutil import log

IMAGE_FILE_MACHINE_I386 = 0x014C
IMAGE_FILE_MACHINE_AMD64 = 0x8664


def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except Exception as e:
        return 1, str(e)


def pe_machine(path: Path) -> int | None:
    """Return PE Machine field or None."""
    try:
        with path.open("rb") as f:
            if f.read(2) != b"MZ":
                return None
            f.seek(0x3C)
            pe_off = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_off)
            if f.read(4) != b"PE\0\0":
                return None
            return struct.unpack("<H", f.read(2))[0]
    except Exception:
        return None


def machine_label(m: int | None) -> str | None:
    if m == IMAGE_FILE_MACHINE_AMD64:
        return "x64"
    if m == IMAGE_FILE_MACHINE_I386:
        return "x86"
    return None


def cpu_is_64bit() -> bool:
    """True if CPU can run x64 (even if OS is 32-bit)."""
    # PROCESSOR_ARCHITECTURE is x86 under WoW64-less 32-bit OS even on x64 CPU
    arch = (os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    arch64 = (os.environ.get("PROCESSOR_ARCHITEW6432") or "").upper()
    if arch in ("AMD64", "ARM64") or arch64 in ("AMD64", "ARM64"):
        return True
    try:
        import ctypes

        class SYSTEM_INFO(ctypes.Structure):
            _fields_ = [
                ("wProcessorArchitecture", ctypes.c_uint16),
                ("wReserved", ctypes.c_uint16),
                ("dwPageSize", ctypes.c_uint32),
                ("lpMinimumApplicationAddress", ctypes.c_void_p),
                ("lpMaximumApplicationAddress", ctypes.c_void_p),
                ("dwActiveProcessorMask", ctypes.c_void_p),
                ("dwNumberOfProcessors", ctypes.c_uint32),
                ("dwProcessorType", ctypes.c_uint32),
                ("dwAllocationGranularity", ctypes.c_uint32),
                ("wProcessorLevel", ctypes.c_uint16),
                ("wProcessorRevision", ctypes.c_uint16),
            ]

        si = SYSTEM_INFO()
        # GetNativeSystemInfo reflects real CPU under 32-bit OS on x64
        ctypes.windll.kernel32.GetNativeSystemInfo(ctypes.byref(si))
        # 9 = PROCESSOR_ARCHITECTURE_AMD64, 12 = ARM64
        return si.wProcessorArchitecture in (9, 12)
    except Exception:
        pass
    # Fallback: WMIC
    code, out = _run(["wmic", "cpu", "get", "AddressWidth", "/value"])
    if "AddressWidth=64" in out.replace(" ", ""):
        return True
    return arch.endswith("64")


@dataclass
class BootEnv:
    os_arch: str  # x64 | x86
    cpu_64bit: bool
    is_uefi: bool
    bootmgr_arch: str | None  # x64 | x86 | None
    esp_has_bootx64: bool
    esp_has_bootia32: bool
    firmware_likely_ia32: bool
    mismatch: bool
    win11_possible: bool
    strategy: str
    notes: list[str]


def _mount_esp_readonly() -> str | None:
    from .sysreserved import mount_esp

    return mount_esp()


def _unmount(letter_root: str | None) -> None:
    if not letter_root:
        return
    try:
        from .sysreserved import unmount_letter

        unmount_letter(letter_root)
    except Exception:
        pass


def inspect_esp_boot_files(esp_root: str) -> tuple[str | None, bool, bool]:
    """
    Returns (primary_bootmgr_arch, has_bootx64, has_bootia32).
    Prefers EFI\\Microsoft\\Boot\\bootmgfw.efi then EFI\\Boot\\bootx64/bootia32.
    """
    base = Path(esp_root + "\\")
    candidates = [
        base / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi",
        base / "EFI" / "Boot" / "bootx64.efi",
        base / "EFI" / "Boot" / "bootia32.efi",
        base / "EFI" / "Microsoft" / "Boot" / "bootia32.efi",
    ]
    has_x64 = False
    has_ia32 = False
    primary = None

    for p in candidates:
        if not p.is_file():
            continue
        name = p.name.lower()
        lab = machine_label(pe_machine(p))
        if lab == "x64" or name == "bootx64.efi":
            has_x64 = True
            if primary is None or "bootmgfw" in name:
                primary = "x64" if lab in (None, "x64") else lab
        if lab == "x86" or "ia32" in name:
            has_ia32 = True
            if primary is None:
                primary = "x86"
        if lab == "x64":
            primary = "x64"

    return primary, has_x64, has_ia32


def analyze_boot_environment(os_arch: str, is_uefi: bool) -> BootEnv:
    notes: list[str] = []
    cpu64 = cpu_is_64bit()
    bootmgr_arch = None
    has_x64 = False
    has_ia32 = False
    mounted = None

    if is_uefi:
        try:
            mounted = _mount_esp_readonly()
            if mounted:
                bootmgr_arch, has_x64, has_ia32 = inspect_esp_boot_files(mounted)
                notes.append(
                    f"ESP boot files: bootmgr={bootmgr_arch or '?'} bootx64={has_x64} bootia32={has_ia32}"
                )
        except Exception as e:
            notes.append(f"ESP inspect skipped: {e}")
        finally:
            _unmount(mounted)

    # Infer IA32-only firmware: UEFI + only IA32 boot files + (OS x86 or bootmgr x86 without x64)
    firmware_ia32 = bool(is_uefi and has_ia32 and not has_x64 and (os_arch == "x86" or bootmgr_arch == "x86"))

    mismatch = False
    if os_arch == "x64" and bootmgr_arch == "x86":
        mismatch = True
        notes.append("Mismatch: 64-bit Windows but 32-bit Boot Manager on ESP - repairable via bcdboot")
    if os_arch == "x64" and is_uefi and has_ia32 and not has_x64:
        mismatch = True
        notes.append("ESP missing bootx64.efi while OS is x64 - will rewrite UEFI boot files")

    # Strategy selection
    if os_arch == "x64":
        if mismatch:
            strategy = "repair_bootmgr_x64"
            win11 = True
        elif firmware_ia32:
            # Extremely rare if OS is already x64 — still use hybrid bridge
            strategy = "hybrid_ia32_csmwrap"
            win11 = True  # via hybrid BIOS path after CSMWrap
            notes.append(
                "IA32 UEFI + x64 OS: deploy hybrid CSMWrap (UEFI IA32 -> SeaBIOS -> BIOS bootmgr)"
            )
        else:
            strategy = "ok_x64"
            win11 = True
    else:
        # 32-bit OS
        if not cpu64:
            strategy = "max_win10_x86_cpu32"
            win11 = False
            notes.append("32-bit CPU - Windows 11 does not exist")
        elif firmware_ia32:
            strategy = "hybrid_ia32_csmwrap"
            win11 = False  # no inplace x86->x64; hybrid enables later clean Win11 x64
            notes.append(
                "IA32 UEFI + x64 CPU: hybrid CSMWrap staged for Win11 x64 boot. "
                "Inplace keep-apps max remains Win10 22H2 x86; Win11 x64 = clean install after hybrid."
            )
        elif is_uefi and has_x64:
            # Odd: x86 OS on firmware that has x64 boot files - still no inplace to Win11
            strategy = "clean_install_x64_only"
            win11 = False
            notes.append(
                "32-bit Windows cannot inplace-upgrade to 64-bit Win11. "
                "CPU/firmware look x64-capable: clean install Win11 x64 would work (backup first)."
            )
        elif not is_uefi:
            strategy = "clean_install_x64_legacy_or_uefi"
            win11 = False
            notes.append(
                "32-bit Windows on BIOS/CSM + 64-bit CPU: no inplace to Win11. "
                "Optional: enable UEFI+GPT or Legacy CSM and clean-install Win11 x64."
            )
        else:
            strategy = "max_win10_x86"
            win11 = False
            notes.append("32-bit OS - inplace Win11 impossible; target Win10 22H2 x86")

    return BootEnv(
        os_arch=os_arch,
        cpu_64bit=cpu64,
        is_uefi=is_uefi,
        bootmgr_arch=bootmgr_arch,
        esp_has_bootx64=has_x64,
        esp_has_bootia32=has_ia32,
        firmware_likely_ia32=firmware_ia32,
        mismatch=mismatch,
        win11_possible=win11,
        strategy=strategy,
        notes=notes,
    )


def repair_bootmgr_to_os_arch(prefer_uefi: bool = True) -> bool:
    """
    Rewrite Boot Manager from the running Windows tree so ESP matches OS bitness.
    Safe for scenario A (x64 OS + stale IA32 boot files).
    """
    log("=== Repair Boot Manager bitness (match OS) ===", "STEP")
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    if not bcdboot.exists():
        log("bcdboot.exe missing", "ERROR")
        return False

    from .mbrgpt import repair_boot_manager

    ok = repair_boot_manager(prefer_uefi=prefer_uefi)
    if not ok:
        # Explicit ESP mount + bcdboot
        from .sysreserved import mount_esp, unmount_letter

        letter = mount_esp()
        if letter:
            mode = "UEFI" if prefer_uefi else "ALL"
            code, out = _run([str(bcdboot), sys_root, "/s", letter, "/f", mode])
            log(f"bcdboot /s {letter} /f {mode} -> {code}: {out[:250]}")
            # Verify PE
            bootmgfw = Path(letter + "\\") / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi"
            lab = machine_label(pe_machine(bootmgfw)) if bootmgfw.exists() else None
            if lab:
                log(f"bootmgfw.efi now {lab}", "OK" if lab == "x64" else "WARN")
            # If IA32 leftover confuses some firmware, keep bootia32 but ensure bootx64 exists
            bootx64 = Path(letter + "\\") / "EFI" / "Boot" / "bootx64.efi"
            if prefer_uefi and bootmgfw.exists() and not bootx64.exists():
                try:
                    bootx64.parent.mkdir(parents=True, exist_ok=True)
                    import shutil

                    shutil.copy2(bootmgfw, bootx64)
                    log("Copied bootmgfw -> EFI\\Boot\\bootx64.efi", "OK")
                except Exception as e:
                    log(f"bootx64 copy: {e}", "WARN")
            unmount_letter(letter)
            ok = code == 0 or "successfully" in out.lower()
    return ok


def apply_smart_boot_strategy(env: BootEnv | None = None, os_arch: str = "x64", is_uefi: bool = True) -> BootEnv:
    """Inspect and apply safe repairs. Never force unsupported IA32->x64 Win11 boots."""
    env = env or analyze_boot_environment(os_arch, is_uefi)
    for n in env.notes:
        log(n, "WARN" if env.mismatch or not env.win11_possible else "INFO")

    if env.strategy == "repair_bootmgr_x64":
        if repair_bootmgr_to_os_arch(prefer_uefi=True):
            log("Boot Manager aligned to x64 for Windows 11 upgrade path", "OK")
        else:
            log("Boot Manager repair incomplete - upgrade may fail at reboot", "WARN")
    elif env.strategy == "hybrid_ia32_csmwrap":
        try:
            from .hybrid_uefi import apply_hybrid_ia32_path

            # Non-destructive stage by default; activate only if OS already x64
            activate = env.os_arch == "x64"
            res = apply_hybrid_ia32_path(activate=activate, prepare_bios=True)
            if res.get("ok"):
                log(
                    "Hybrid IA32 path ready (CSMWrap). Disable Secure Boot. "
                    + (
                        "Default bootia32 replaced — next boot is SeaBIOS/legacy."
                        if res.get("activated")
                        else "Select CSMWrap from firmware boot menu when installing Win11 x64."
                    ),
                    "OK",
                )
            else:
                log("Hybrid deploy incomplete — falling back to Win10 22H2 x86 keep-apps path", "WARN")
        except Exception as e:
            log(f"Hybrid IA32 path failed: {e}", "WARN")
    elif env.strategy.startswith("max_win10") or env.strategy.startswith("clean_install"):
        log(f"Win11 x64 path blocked by boot/firmware architecture ({env.strategy})", "WARN")
    elif env.strategy == "blocked_ia32_firmware":
        log(
            "IA32 UEFI without hybrid deploy — use --cli --hybrid to stage CSMWrap bridge.",
            "ERROR",
        )
    return env
