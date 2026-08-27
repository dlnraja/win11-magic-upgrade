"""MBR -> GPT + Boot Manager repair - no wipe, no PowerShell."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .diskpart_safe import (
    assign_letter_to_volume,
    ensure_select_disk,
    find_esp_candidates,
    find_volume_by_letter,
    free_letter,
    get_system_disk_number,
    remove_letter_from_volume,
    run_diskpart,
    shrink_volume_letter,
)
from .logutil import STATE_DIR, log
from .sysreserved import mount_esp, unmount_letter

CODES = {
    0: "Success",
    6: "Volume encrypted - suspend BitLocker first",
    7: "Disk layout invalid (<=3 partitions needed)",
    8: "EFI partition create failed",
    9: "Boot files install failed",
    100: "GPT OK but some BCD not restored",
}


def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
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
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except Exception as e:
        return 1, str(e)


def suspend_bitlocker() -> None:
    # Prefer OEM-aware path (Device Encryption + Toshiba warnings)
    try:
        from .oem_adapt import get_oem_profile, prepare_encryption_for_mutate

        oem = get_oem_profile()
        enc = prepare_encryption_for_mutate(oem)
        if enc.get("blocked"):
            log("BitLocker/HDD encryption locked — cannot suspend; unlock first", "ERROR")
        return
    except Exception:
        pass
    manage = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "manage-bde.exe"
    if not manage.exists():
        return
    drive = os.environ.get("SystemDrive", "C:")
    code, out = _run([str(manage), "-status", drive])
    if "Protection On" in out or "Protection Status: On" in out:
        log("Suspending BitLocker...", "STEP")
        _run([str(manage), "-protectors", "-disable", drive])


def prepare_layout_for_mbr2gpt(disk_number: int) -> None:
    """Non-destructive layout prep: shrink OS ~350MB, disable WinRE if needed."""
    log("Preparing partition layout for MBR2GPT (no wipe)...", "STEP")
    letter = os.environ.get("SystemDrive", "C:")[:1].upper()

    # OEM: always try WinRE disable early on crowded Acer/HP/Lenovo layouts
    force_winre = False
    try:
        from .oem_adapt import get_oem_profile

        oem = get_oem_profile()
        force_winre = bool(oem.mbr2gpt_disable_winre_first and oem.family in (
            "acer", "asus", "toshiba", "hp", "dell", "lenovo",
        ))
        if oem.msdm_present:
            log("OEM digital license (MSDM/OA3) detected — keeping disk (no wipe)", "OK")
    except Exception:
        pass

    ok, out = ensure_select_disk(int(disk_number))
    if not ok:
        log(f"Cannot select disk {disk_number} for mbr2gpt prep — abort shrink", "ERROR")
        return
    # Count partitions on that disk
    ok_lp, lp = run_diskpart(f"select disk {int(disk_number)}\nlist partition\nexit\n")
    parts = len(re.findall(r"Partition\s+\d+", lp or "", re.I)) if ok_lp or lp else 0
    if parts >= 4 or force_winre:
        log(f"Disk shows ~{parts} partitions - disabling WinRE to free a slot (OEM-aware)", "WARN")
        reagentc = Path(os.environ["SystemRoot"]) / "System32" / "reagentc.exe"
        if reagentc.exists():
            code, o = _run([str(reagentc), "/disable"])
            log(f"reagentc /disable -> {code}: {o[:200]}")

    # Verify OS volume is on the target disk before shrink
    sys_disk = get_system_disk_number(letter)
    if sys_disk is not None and int(sys_disk) != int(disk_number):
        log(
            f"Refuse shrink: SystemDrive {letter}: is disk #{sys_disk}, not #{disk_number}",
            "ERROR",
        )
        return

    if not shrink_volume_letter(letter, 350, 260):
        log(f"shrink {letter}: failed or unverified (mbr2gpt may still proceed)", "WARN")
    else:
        log(f"shrink {letter}: OK for EFI space", "OK")


def repair_boot_manager(prefer_uefi: bool = True) -> bool:
    """
    Rebuild Windows Boot Manager / BCD without formatting.
    Prefer mountvol /s (safe), else diskpart ESP assign with verified volume index.
    """
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    if not bcdboot.exists():
        log("bcdboot.exe missing", "ERROR")
        return False

    log("Repairing Windows Boot Manager (bcdboot, no format)...", "STEP")

    esp_letter: str | None = None
    esp_vol_index: int | None = None
    used_mountvol = False

    # 1) Preferred: mountvol /s
    mounted = mount_esp()
    if mounted:
        esp_letter = mounted.rstrip(":\\")[:1]
        used_mountvol = True
        log(f"ESP via mountvol /s → {esp_letter}:", "OK")
    else:
        # 2) diskpart FAT32/ESP candidates (EN+FR)
        for v in find_esp_candidates():
            if v.letter:
                esp_letter = v.letter
                esp_vol_index = v.index
                break
            letter = free_letter(("S", "T", "R", "Q", "Y", "X"))
            if not letter:
                break
            if assign_letter_to_volume(v.index, letter):
                esp_letter = letter
                esp_vol_index = v.index
                log(f"ESP temporarily assigned {letter}: (vol {v.index})", "OK")
                break

    modes = ["UEFI", "ALL"] if prefer_uefi else ["ALL", "UEFI", "BIOS"]
    ok = False
    for mode in modes:
        if esp_letter:
            cmd = [str(bcdboot), sys_root, "/s", f"{esp_letter}:", "/f", mode]
        else:
            cmd = [str(bcdboot), sys_root, "/f", mode]
            if mode == "ALL":
                continue
        code, out = _run(cmd)
        log(f"bcdboot {' '.join(cmd[1:])} -> {code}")
        for line in out.splitlines()[:8]:
            log(f"  {line}")
        if code == 0 or "successfully" in out.lower() or "BFSVC" in out:
            ok = True
            log(f"Boot Manager repaired ({mode})", "OK")
            break

    # Remove temporary letter safely
    if esp_letter:
        if used_mountvol:
            unmount_letter(f"{esp_letter}:")
        elif esp_vol_index is not None:
            remove_letter_from_volume(esp_vol_index, esp_letter)
            log(f"Removed temporary letter {esp_letter}: (vol {esp_vol_index})", "INFO")
        else:
            # Resolve by letter then remove
            v = find_volume_by_letter(esp_letter)
            if v:
                remove_letter_from_volume(v.index, esp_letter)
            else:
                unmount_letter(f"{esp_letter}:")

    code, out = _run(["bcdedit", "/enum", "{bootmgr}"])
    if out:
        log("bcdedit {bootmgr}: " + out.splitlines()[0][:120])
    return ok


def convert_mbr_to_gpt(disk_number: int) -> tuple[bool, int, str]:
    if disk_number is None or int(disk_number) < 0:
        return False, -1, "system disk # unknown — refuse mbr2gpt (safety)"

    mbr2gpt = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "mbr2gpt.exe"
    if not mbr2gpt.exists():
        return False, -1, "mbr2gpt.exe missing (need Win10 1703+)"

    # Cross-check disk hosts SystemDrive
    sys_disk = get_system_disk_number()
    if sys_disk is not None and int(sys_disk) != int(disk_number):
        msg = f"disk mismatch: SystemDrive on #{sys_disk}, requested #{disk_number}"
        log(msg, "ERROR")
        return False, -1, msg

    suspend_bitlocker()
    log_dir = STATE_DIR / "mbr2gpt-logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log(f"Validating disk {disk_number} for MBR->GPT...", "STEP")
    code, out = _run(
        [
            str(mbr2gpt),
            "/validate",
            f"/disk:{disk_number}",
            "/allowFullOS",
            f"/logs:{log_dir}",
        ]
    )
    for line in out.splitlines():
        log(f"mbr2gpt validate: {line}")
    if code != 0:
        prepare_layout_for_mbr2gpt(disk_number)
        code, out = _run(
            [
                str(mbr2gpt),
                "/validate",
                f"/disk:{disk_number}",
                "/allowFullOS",
                f"/logs:{log_dir}",
            ]
        )
        for line in out.splitlines():
            log(f"mbr2gpt validate retry: {line}")
        if code != 0:
            msg = CODES.get(code, f"code {code}")
            return False, code, msg

    log(f"Converting disk {disk_number} MBR -> GPT (data preserved)...", "STEP")
    code, out = _run(
        [
            str(mbr2gpt),
            "/convert",
            f"/disk:{disk_number}",
            "/allowFullOS",
            f"/logs:{log_dir}",
        ]
    )
    for line in out.splitlines():
        log(f"mbr2gpt convert: {line}")
    if code in (0, 100):
        repair_boot_manager(prefer_uefi=True)
        log(
            "Conversion OK. IMPORTANT: enter firmware setup and set boot mode to UEFI "
            "(disable CSM/Legacy) before reboot if the PC still uses Legacy BIOS.",
            "WARN",
        )
        return True, code, CODES.get(code, "OK")
    return False, code, CODES.get(code, f"code {code}")
