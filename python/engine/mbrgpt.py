"""MBR -> GPT via mbr2gpt.exe — no PowerShell."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .logutil import STATE_DIR, log

CODES = {
    0: "Success",
    6: "Volume encrypted — suspend BitLocker first",
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


def suspend_bitlocker() -> None:
    manage = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "manage-bde.exe"
    if not manage.exists():
        return
    drive = os.environ.get("SystemDrive", "C:")
    code, out = _run([str(manage), "-status", drive])
    if "Protection On" in out or "Protection Status: On" in out:
        log("Suspending BitLocker...", "STEP")
        _run([str(manage), "-protectors", "-disable", drive])


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
        # Try shrink via diskpart for EFI room
        log("Validation failed — attempting shrink for EFI space...", "WARN")
        letter = os.environ.get("SystemDrive", "C:")[:1]
        script = f"select volume {letter}\nshrink desired=350\nexit\n"
        subprocess.run(
            ["diskpart"],
            input=script,
            text=True,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # Disable WinRE to free a partition slot
        reagentc = Path(os.environ["SystemRoot"]) / "System32" / "reagentc.exe"
        if reagentc.exists():
            _run([str(reagentc), "/disable"])
        code, out = _run(
            [
                str(mbr2gpt),
                "/validate",
                f"/disk:{disk_number}",
                "/allowFullOS",
                f"/logs:{log_dir}",
            ]
        )
        if code != 0:
            msg = CODES.get(code, f"code {code}")
            return False, code, msg

    log(f"Converting disk {disk_number} MBR -> GPT (no wipe)...", "STEP")
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
        sys_root = os.environ.get("SystemRoot", r"C:\Windows")
        bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
        if bcdboot.exists():
            _run([str(bcdboot), sys_root, "/f", "UEFI"])
        log("Conversion OK. Set firmware to UEFI (disable CSM) before reboot if needed.", "OK")
        return True, code, CODES.get(code, "OK")
    return False, code, CODES.get(code, f"code {code}")
