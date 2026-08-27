"""MBR -> GPT + Boot Manager repair - no wipe, no PowerShell."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .logutil import STATE_DIR, log

CODES = {
    0: "Success",
    6: "Volume encrypted - suspend BitLocker first",
    7: "Disk layout invalid (<=3 partitions needed)",
    8: "EFI partition create failed",
    9: "Boot files install failed",
    100: "GPT OK but some BCD not restored",
}


def _run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode, out


def _diskpart(script: str) -> str:
    r = subprocess.run(
        ["diskpart"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return ((r.stdout or "") + (r.stderr or "")).strip()


def suspend_bitlocker() -> None:
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
    letter = os.environ.get("SystemDrive", "C:")[:1]
    out = _diskpart(f"select disk {disk_number}\nlist partition\nexit\n")
    # Count primary-like partitions
    parts = len(re.findall(r"Partition\s+\d+", out, re.I))
    if parts >= 4:
        log(f"Disk shows ~{parts} partitions - disabling WinRE to free a slot", "WARN")
        reagentc = Path(os.environ["SystemRoot"]) / "System32" / "reagentc.exe"
        if reagentc.exists():
            code, o = _run([str(reagentc), "/disable"])
            log(f"reagentc /disable -> {code}: {o[:200]}")

    # Shrink for EFI (~260-350 MB)
    shrink = _diskpart(f"select volume {letter}\nshrink desired=350 minimum=260\nexit\n")
    log(f"shrink: {shrink.splitlines()[-1] if shrink else 'n/a'}")


def repair_boot_manager(prefer_uefi: bool = True) -> bool:
    """
    Rebuild Windows Boot Manager / BCD without formatting.
    After MBR2GPT: prefer UEFI; also try ALL if UEFI-only fails.
    """
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    if not bcdboot.exists():
        log("bcdboot.exe missing", "ERROR")
        return False

    log("Repairing Windows Boot Manager (bcdboot, no format)...", "STEP")

    # Try to find EFI system partition and assign a temporary letter
    esp_letter = None
    detail = _diskpart("list volume\nexit\n")
    # Look for FAT32 System / Hidden EFI volumes
    for line in detail.splitlines():
        if re.search(r"FAT32|System", line, re.I) and re.search(r"Hidden|System|EFI", line, re.I):
            m = re.search(r"Volume\s+(\d+)", line, re.I)
            if m:
                vol = m.group(1)
                # Pick a free letter S: or T:
                for letter in ("S", "T", "R", "Q"):
                    if not Path(f"{letter}:\\").exists():
                        _diskpart(f"select volume {vol}\nassign letter={letter}\nexit\n")
                        if Path(f"{letter}:\\").exists():
                            esp_letter = letter
                            log(f"ESP temporarily assigned {letter}:", "OK")
                        break
                break

    modes = ["UEFI", "ALL"] if prefer_uefi else ["ALL", "UEFI", "BIOS"]
    ok = False
    for mode in modes:
        if esp_letter:
            cmd = [str(bcdboot), sys_root, "/s", f"{esp_letter}:", "/f", mode]
        else:
            # Without explicit ESP, let bcdboot choose (works post-mbr2gpt often)
            cmd = [str(bcdboot), sys_root, "/f", mode]
            if mode != "UEFI":
                # /f without /s is invalid for some modes - skip ALL without /s
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

    # Remove temporary ESP letter (hide again)
    if esp_letter:
        _diskpart(f"select volume {esp_letter}\nremove letter={esp_letter}\nexit\n")
        log(f"Removed temporary letter {esp_letter}:", "INFO")

    # Verify BCD has a Windows Boot Manager entry
    code, out = _run(["bcdedit", "/enum", "{bootmgr}"])
    if out:
        log("bcdedit {bootmgr}: " + out.splitlines()[0][:120])
    return ok


def convert_mbr_to_gpt(disk_number: int) -> tuple[bool, int, str]:
    mbr2gpt = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "mbr2gpt.exe"
    if not mbr2gpt.exists():
        return False, -1, "mbr2gpt.exe missing (need Win10 1703+)"

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
