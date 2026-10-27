"""Firmware / disk / bitness decision matrix.

Pure functions: no disk writes. The pipeline asks `mbr2gpt_wanted` before any
conversion and `decide_boot_plan` for the boot path.

Covered combinations:
  UEFI x64 + GPT or MBR or hybrid MBR/GPT
  UEFI IA32 on a 64-bit CPU (Bay Trail class, bootia32.efi) and the reverse
  (64-bit firmware, 32-bit OS)
  BIOS legacy-only, CSM/hybrid (legacy boot on UEFI-capable firmware)
  32-bit and 64-bit OS and CPU, Secure Boot on or off

OpenDuet is only suggested for legacy-only firmware. It is never a disk write.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from typing import Any


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def infer_firmware_bitness(
    *,
    is_uefi: bool,
    os_arch: str,
    esp_has_bootx64: bool = False,
    esp_has_bootia32: bool = False,
    bootmgr_arch: str = "",
    firmware_likely_ia32: bool = False,
) -> str:
    """Return x64, ia32, legacy, or unknown."""
    if firmware_likely_ia32:
        return "ia32"
    if not is_uefi:
        return "legacy"
    arch = (os_arch or "").lower()
    mgr = (bootmgr_arch or "").lower()
    if arch in ("x64", "amd64", "arm64"):
        return "x64"
    if arch in ("x86", "x32", "i386"):
        # Running 32-bit Windows via UEFI means the firmware loaded an IA32
        # boot manager, except when the ESP already carries an x64 loader
        # (64-bit firmware, 32-bit OS/media mismatch).
        if mgr == "x64" or (esp_has_bootx64 and not esp_has_bootia32):
            return "x64"
        return "ia32"
    if esp_has_bootia32 and not esp_has_bootx64:
        return "ia32"
    if esp_has_bootx64:
        return "x64"
    return "unknown"


def classify_mbr_sector(sector: bytes | None) -> str:
    """Classify sector 0: gpt (protective), hybrid, mbr, or unknown.

    Hybrid means a GPT protective entry (type 0xEE) shares the MBR with a
    real partition type. A lone 0xEE entry is a normal protective MBR.
    """
    if not sector or len(sector) < 512:
        return "unknown"
    if sector[510:512] != b"\x55\xaa":
        return "unknown"
    types: list[int] = []
    for i in range(4):
        ptype = sector[446 + i * 16 + 4]
        if ptype:
            types.append(ptype)
    if not types:
        return "unknown"
    if 0xEE in types and any(t != 0xEE for t in types):
        return "hybrid"
    if all(t == 0xEE for t in types):
        return "gpt"
    return "mbr"


def classify_partition_text(detail: str) -> str:
    """diskpart `detail disk` text → gpt, mbr, hybrid, or unknown."""
    text = detail or ""
    if re.search(r"hybrid", text, re.I):
        return "hybrid"
    gpt = bool(re.search(r"\bGPT\b|GUID", text, re.I))
    mbr = bool(re.search(r"\bMBR\b|Master Boot|amorçage principal|demarrage principal", text, re.I))
    if gpt and mbr:
        return "hybrid"
    if gpt:
        return "gpt"
    if mbr:
        return "mbr"
    return "unknown"


@dataclass(frozen=True)
class FirmwareSnapshot:
    firmware_mode: str  # uefi | bios
    firmware_bitness: str  # x64 | ia32 | legacy | unknown
    uefi_capable: bool
    cpu_bits: int
    os_arch: str  # x64 | x86 | arm64
    partition_style: str  # mbr | gpt | hybrid | unknown
    secure_boot: str  # on | off | unsupported
    esp_has_bootx64: bool = False
    esp_has_bootia32: bool = False
    bootmgr_arch: str = ""
    mbr2gpt_available: bool = True
    firmware_likely_ia32: bool = False
    force_mbr2gpt: bool = False
    keep_legacy: bool = False


@dataclass(frozen=True)
class BootPlan:
    path_id: str
    summary: str
    actions: tuple[str, ...]
    bcd_mode: str
    boot_files: tuple[str, ...]
    allow_mbr2gpt: bool
    allow_hybrid_csmwrap: bool
    suggest_openduet: bool
    secure_boot_must_off: bool
    win11_inplace: bool
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


def _plan(**kwargs: Any) -> BootPlan:
    base: dict[str, Any] = dict(
        path_id="unknown",
        summary="",
        actions=(),
        bcd_mode="NONE",
        boot_files=(),
        allow_mbr2gpt=False,
        allow_hybrid_csmwrap=False,
        suggest_openduet=False,
        secure_boot_must_off=False,
        win11_inplace=False,
        warnings=(),
        blockers=(),
    )
    base.update(kwargs)
    return BootPlan(**base)


def media_boot_file(snap: FirmwareSnapshot) -> str:
    """EFI/BIOS file the install media must expose for this firmware."""
    bits = snap.firmware_bitness
    if bits == "ia32":
        return "bootia32.efi"
    if bits == "legacy":
        return "bootmgr"
    return "bootx64.efi"


def snapshot_from_report(report: Any) -> FirmwareSnapshot:
    style_raw = str(getattr(report, "partition_style", "") or "unknown").strip().lower()
    if bool(getattr(report, "partition_hybrid", False)) or style_raw in (
        "hybrid",
        "hybrid_mbr_gpt",
        "mbr+gpt",
    ):
        style = "hybrid"
    elif style_raw in ("mbr", "gpt"):
        style = style_raw
    else:
        style = "unknown"
    is_uefi = bool(getattr(report, "is_uefi", False))
    mode = str(getattr(report, "firmware_mode", "") or "")
    if mode not in ("uefi", "bios"):
        mode = "uefi" if is_uefi else "bios"
    likely = bool(getattr(report, "firmware_likely_ia32", False))
    os_arch = str(getattr(report, "architecture", "") or "x64").lower()
    if os_arch in ("x86", "x32", "i386"):
        os_arch = "x86"
    bits = str(getattr(report, "firmware_bitness", "") or "")
    if not bits:
        bits = infer_firmware_bitness(
            is_uefi=mode == "uefi",
            os_arch=os_arch,
            esp_has_bootx64=bool(getattr(report, "esp_has_bootx64", False)),
            esp_has_bootia32=bool(getattr(report, "esp_has_bootia32", False)) or likely,
            bootmgr_arch=str(getattr(report, "bootmgr_arch") or ""),
            firmware_likely_ia32=likely,
        )
    uefi_capable = bool(getattr(report, "uefi_capable", False)) or mode == "uefi"
    if bool(getattr(report, "secure_boot", False)):
        sb = "on"
    elif mode == "bios" and not uefi_capable:
        sb = "unsupported"
    else:
        sb = "off"
    cpu_bits = 32 if getattr(report, "cpu_64bit", True) is False else 64
    if os_arch == "x64":
        cpu_bits = 64
    return FirmwareSnapshot(
        firmware_mode=mode,
        firmware_bitness=bits,
        uefi_capable=uefi_capable,
        cpu_bits=cpu_bits,
        os_arch=os_arch,
        partition_style=style,
        secure_boot=sb,
        esp_has_bootx64=bool(getattr(report, "esp_has_bootx64", False)),
        esp_has_bootia32=bool(getattr(report, "esp_has_bootia32", False)) or likely,
        bootmgr_arch=str(getattr(report, "bootmgr_arch") or ""),
        mbr2gpt_available=bool(getattr(report, "mbr2gpt_available", True)),
        firmware_likely_ia32=likely,
        force_mbr2gpt=bool(getattr(report, "force_mbr2gpt", False)) or _env_on("MAGIC_FORCE_MBR2GPT"),
        keep_legacy=bool(getattr(report, "keep_legacy", False)) or _env_on("MAGIC_KEEP_LEGACY"),
    )


def mbr2gpt_wanted(report: Any) -> bool:
    """True when MBR→GPT is the safe next step for this PC."""
    if _env_on("MAGIC_KEEP_LEGACY"):
        return False
    if _env_on("MAGIC_FORCE_MBR2GPT"):
        return True
    return decide_boot_plan(snapshot_from_report(report)).allow_mbr2gpt


def decide_boot_plan(snap: FirmwareSnapshot) -> BootPlan:
    plan = _decide(snap)
    warnings = list(plan.warnings)
    if snap.force_mbr2gpt and snap.partition_style == "mbr":
        warnings.append("MAGIC_FORCE_MBR2GPT is set — conversion is allowed even if firmware looks legacy.")
        plan = replace(plan, allow_mbr2gpt=True, warnings=tuple(warnings))
    if snap.keep_legacy:
        warnings = list(plan.warnings)
        warnings.append("MAGIC_KEEP_LEGACY is set — MBR/BIOS boot is left in place.")
        plan = replace(
            plan,
            allow_mbr2gpt=False,
            bcd_mode="BIOS" if snap.firmware_mode == "bios" else plan.bcd_mode,
            warnings=tuple(warnings),
        )
    return plan


def _decide(snap: FirmwareSnapshot) -> BootPlan:
    if snap.os_arch == "arm64":
        return _plan(
            path_id="arm64_unsupported",
            summary="ARM64 host — this chain does not pick an x64 boot path.",
            blockers=("Use Microsoft ARM64 media for an ARM device.",),
        )
    if snap.cpu_bits < 64:
        return _plan(
            path_id="cpu32_max_win10",
            summary="32-bit CPU — Windows 11 does not exist for this processor.",
            bcd_mode="UEFI" if snap.firmware_mode == "uefi" else "BIOS",
            boot_files=("bootia32.efi",) if snap.firmware_mode == "uefi" else ("bootmgr",),
            blockers=("In-place Windows 11 is impossible on a 32-bit CPU.",),
            win11_inplace=False,
        )

    # IA32 UEFI on a 64-bit CPU (Bay Trail and similar), including a 64-bit OS
    # whose ESP only has bootia32.efi.
    if snap.firmware_bitness == "ia32" or (
        snap.firmware_mode == "uefi" and snap.firmware_likely_ia32
    ):
        return _ia32_plan(snap)

    if snap.firmware_mode == "uefi" and snap.firmware_bitness == "x64":
        return _uefi_x64_plan(snap)

    if snap.firmware_mode == "bios" or snap.firmware_bitness == "legacy":
        return _bios_plan(snap)

    return _plan(
        path_id="unknown_conservative",
        summary="Firmware type is unknown — no disk conversion.",
        bcd_mode="ALL",
        warnings=("Firmware mode was not detected. MBR→GPT is skipped until it is known.",),
    )


def _ia32_plan(snap: FirmwareSnapshot) -> BootPlan:
    sb_off = snap.secure_boot == "on"
    warnings = [
        "32-bit UEFI cannot load a 64-bit Windows bootmgfw.efi. "
        "The bridge is IA32 UEFI → CSMWrap → SeaBIOS → BIOS bootmgr.",
        "Win11 install media only ships bootx64.efi. Boot that media only after the hybrid bridge is active.",
    ]
    if snap.partition_style == "gpt":
        warnings.append(
            "Disk is GPT. SeaBIOS legacy boot is more reliable on MBR; the disk is not converted automatically."
        )
    if snap.partition_style == "hybrid":
        warnings.append("Hybrid MBR/GPT detected — mbr2gpt is not used.")
    if sb_off:
        warnings.append("Secure Boot is on. Stage the bridge, but do not activate it until Secure Boot is off.")
    inplace = snap.os_arch == "x64"
    if snap.os_arch == "x86":
        warnings.append(
            "32-bit Windows cannot in-place upgrade to 64-bit Windows 11. "
            "Keep-apps stops at Windows 10 22H2 x86; Windows 11 x64 is a clean install."
        )
    return _plan(
        path_id="hybrid_ia32_csmwrap" if inplace else "hybrid_ia32_then_clean",
        summary="IA32 UEFI + 64-bit CPU — CSMWrap hybrid, legacy boot files, Secure Boot off.",
        actions=("stage_bootia32", "hybrid_csmwrap", "bcdboot_bios")
        + (("require_secure_boot_off",) if sb_off else ()),
        bcd_mode="BIOS",
        boot_files=("bootia32.efi", "bootmgr"),
        allow_mbr2gpt=False,
        allow_hybrid_csmwrap=True,
        secure_boot_must_off=sb_off,
        win11_inplace=inplace,
        warnings=tuple(warnings),
    )


def _uefi_x64_plan(snap: FirmwareSnapshot) -> BootPlan:
    warnings: list[str] = []
    actions: list[str] = ["bcdboot_uefi", "stage_bootx64"]
    allow = False
    path = "uefi_gpt_x64"
    summary = "64-bit UEFI — native bootx64.efi / bootmgfw.efi."
    if snap.os_arch == "x86":
        warnings.append(
            "64-bit firmware with a 32-bit OS. In-place Windows 11 is impossible. "
            "Clean-install media must use bootx64.efi, not bootia32.efi."
        )
        if snap.partition_style == "mbr":
            warnings.append(
                "MBR→GPT is not applied automatically while the running OS is 32-bit."
            )
        return _plan(
            path_id="clean_x64_on_x64_firmware",
            summary="64-bit UEFI firmware, 32-bit Windows — clean x64 install only.",
            actions=("stage_bootx64", "clean_install_x64"),
            bcd_mode="UEFI",
            boot_files=("bootx64.efi",),
            warnings=tuple(warnings),
            blockers=("No in-place upgrade from 32-bit Windows to 64-bit Windows 11.",),
        )
    if snap.bootmgr_arch == "x86" or (snap.esp_has_bootia32 and not snap.esp_has_bootx64):
        actions.insert(0, "repair_bootmgr_x64")
        warnings.append("ESP boot files do not match the 64-bit OS — bcdboot will rewrite them.")
    if snap.partition_style == "hybrid":
        path = "uefi_hybrid_keep"
        summary = "64-bit UEFI on a hybrid MBR/GPT disk — boot UEFI, do not run mbr2gpt."
        warnings.append("Hybrid MBR/GPT: the protective MBR is left alone and Windows stays on the GPT boot path.")
    elif snap.partition_style == "mbr":
        path = "uefi_mbr_convert"
        summary = "64-bit UEFI on MBR — convert with mbr2gpt, then UEFI boot files."
        allow = True
        actions.append("mbr2gpt")
        if not snap.mbr2gpt_available:
            warnings.append("mbr2gpt is not on this OS yet; conversion waits until a newer Windows hop provides it.")
    elif snap.partition_style == "unknown":
        path = "uefi_style_unknown"
        summary = "64-bit UEFI but partition style is unknown — boot repair only, no conversion."
        warnings.append("Partition style was not detected. MBR→GPT is skipped.")
    if snap.secure_boot == "on":
        warnings.append("Secure Boot can stay on for native 64-bit UEFI Windows boot.")
    return _plan(
        path_id=path,
        summary=summary,
        actions=tuple(actions),
        bcd_mode="UEFI",
        boot_files=("bootx64.efi",),
        allow_mbr2gpt=allow,
        win11_inplace=True,
        warnings=tuple(warnings),
    )


def _bios_plan(snap: FirmwareSnapshot) -> BootPlan:
    capable = snap.uefi_capable
    warnings: list[str] = []
    x64_os = snap.os_arch == "x64"
    if snap.os_arch == "x86":
        warnings.append(
            "32-bit Windows on BIOS/CSM cannot in-place upgrade to Windows 11. "
            "A later clean install needs 64-bit media."
        )
    if snap.partition_style == "hybrid":
        warnings.append("Hybrid MBR/GPT disk — mbr2gpt is not used.")
        return _plan(
            path_id="bios_hybrid_keep",
            summary="Legacy boot on a hybrid disk — keep the current layout.",
            actions=("bcdboot_bios", "keep_legacy"),
            bcd_mode="BIOS",
            boot_files=("bootmgr",),
            suggest_openduet=(not capable) and snap.cpu_bits == 64,
            win11_inplace=x64_os,
            warnings=tuple(warnings)
            + (
                ()
                if capable
                else (
                    "Legacy-only firmware: optional OpenDuet USB can chain-load the existing EFI loader.",
                )
            ),
        )

    if capable and not snap.keep_legacy:
        if snap.partition_style == "mbr" and x64_os:
            return _plan(
                path_id="csm_convert_mbr2gpt",
                summary="CSM/legacy boot on UEFI-capable firmware — convert MBR to GPT, then switch firmware to UEFI.",
                actions=("mbr2gpt", "bcdboot_uefi", "stage_bootx64"),
                bcd_mode="UEFI",
                boot_files=("bootx64.efi",),
                allow_mbr2gpt=True,
                win11_inplace=True,
                warnings=(
                    "After conversion the firmware must boot UEFI (CSM off). The PC is UEFI-capable but is currently in legacy mode.",
                ),
            )
        if snap.partition_style == "gpt" and x64_os:
            return _plan(
                path_id="csm_switch_firmware_uefi",
                summary="Disk is already GPT while booted in CSM — switch firmware to UEFI; do not convert.",
                actions=("bcdboot_uefi", "stage_bootx64"),
                bcd_mode="UEFI",
                boot_files=("bootx64.efi",),
                win11_inplace=True,
                warnings=("Enable UEFI boot in firmware setup. mbr2gpt is not required.",),
            )
        if not x64_os:
            return _plan(
                path_id="csm_clean_x64",
                summary="UEFI-capable firmware, currently legacy, 32-bit OS — clean x64 install, no automatic conversion.",
                actions=("clean_install_x64",),
                bcd_mode="BIOS",
                boot_files=("bootmgr", "bootx64.efi"),
                warnings=tuple(warnings),
                blockers=("No in-place upgrade from 32-bit Windows to 64-bit Windows 11.",),
            )
        return _plan(
            path_id="csm_style_unknown",
            summary="UEFI-capable firmware in legacy mode, partition style unknown — no conversion.",
            actions=("bcdboot_bios",),
            bcd_mode="BIOS",
            boot_files=("bootmgr",),
            win11_inplace=True,
            warnings=warnings + ["Partition style is unknown, so MBR→GPT is skipped."],
        )

    # Legacy-only (or keep-legacy already applied by the caller).
    suggest = snap.cpu_bits == 64
    warnings.append(
        "No UEFI firmware was detected. mbr2gpt would make the disk unbootable here, so the legacy boot path is kept."
    )
    if suggest:
        warnings.append(
            "Optional OpenDuet/OpenCore USB adds a software UEFI layer. The stick has to stay plugged in. "
            "It is prepared only when asked, and it is never written without a confirm token."
        )
    if snap.partition_style == "gpt":
        path = "legacy_gpt_openduet"
        summary = "Legacy-only firmware on a GPT disk — keep GPT and chain-load the ESP with an optional OpenDuet stick."
    else:
        path = "legacy_mbr_keep" if x64_os else "legacy_clean_x64"
        summary = (
            "Legacy-only BIOS on MBR — keep BIOS boot files and the Windows 11 bypass path."
            if x64_os
            else "Legacy-only BIOS and a 32-bit OS — keep legacy boot; Windows 11 x64 is a clean install."
        )
    return _plan(
        path_id=path,
        summary=summary,
        actions=("bcdboot_bios", "keep_legacy") + (("suggest_openduet",) if suggest else ()),
        bcd_mode="BIOS",
        boot_files=("bootmgr",),
        suggest_openduet=suggest,
        win11_inplace=x64_os,
        warnings=tuple(warnings),
        blockers=() if x64_os else ("No in-place upgrade from 32-bit Windows to 64-bit Windows 11.",),
    )
