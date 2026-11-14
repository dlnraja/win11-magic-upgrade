"""MBR2GPT layout checks and the CSM/GOP warning.

Conversion itself stays a separate step. These checks refuse a known-bad layout
and tell the user not to disable CSM when the GPU may have no UEFI GOP.
"""
from __future__ import annotations

import os

RETURN_CODES = {
    0: "Success",
    1: "MBR2GPT failed. Read the /logs setuperr.log before retrying.",
    2: "MBR2GPT could not find the OS partition.",
    3: "MBR2GPT could not find space for the EFI system partition.",
    6: "A volume is encrypted. Suspend BitLocker, then validate again.",
    7: "The disk layout is not valid for MBR2GPT.",
    8: "The EFI system partition could not be created.",
    9: "Boot files could not be installed.",
    10: "The disk geometry is not supported.",
    100: "The disk is GPT but some BCD entries were not restored. Regenerate BCD, then verify.",
}


def explain_code(code: int) -> str:
    return RETURN_CODES.get(int(code), f"mbr2gpt returned {code}")


def layout_problems(
    *,
    primary_count: int = 0,
    has_logical: bool = False,
    has_active: bool = True,
    cross_disk_boot: bool = False,
    free_start_ok: bool = True,
    free_end_ok: bool = True,
    sector_4k: bool = False,
) -> list[str]:
    problems: list[str] = []
    if primary_count > 3:
        problems.append("more than 3 primary partitions")
    if has_logical:
        problems.append("extended or logical partitions are present")
    if not has_active:
        problems.append("no active system partition")
    if cross_disk_boot:
        problems.append("boot files are on a different disk from Windows")
    if not free_start_ok:
        problems.append("not enough free space at the start of the disk")
    if not free_end_ok:
        problems.append("not enough free space at the end of the disk")
    if sector_4k:
        problems.append("4K sectors need a 260 MB EFI system partition, not 100 MB")
    return problems


def csm_advice(*, gop: bool | None = None, integrated_gpu: bool = False) -> str:
    override = os.environ.get("MAGIC_CSM_OVERRIDE", "").strip().lower() in ("1", "true", "yes")
    if gop is False and not override:
        fallback = " Connect the display to the motherboard output." if integrated_gpu else ""
        return (
            "The GPU may have no UEFI GOP. Do not disable CSM until a picture is confirmed."
            + fallback
            + " Clearing CMOS restores the previous firmware mode."
        )
    if gop is None and not override:
        return (
            "UEFI is required after MBR2GPT. Disable CSM only if the display still works. "
            "Older cards can go black with CSM off. Clearing CMOS is the recovery."
        )
    return "Set the firmware to UEFI and disable CSM/Legacy before the next boot."


def allow_conversion(problems: list[str]) -> bool:
    return not problems
