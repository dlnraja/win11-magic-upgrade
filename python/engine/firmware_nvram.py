"""Create the UEFI NVRAM boot entry that bcdboot /s does not write.

Microsoft's BCDBoot reference says /s updates the files on the target volume
and does not create the firmware entry. A second bcdboot without /s, using
/f UEFI or /f ALL, writes that entry. /p keeps an existing Linux order.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

Runner = Callable[..., tuple[int, str]]


def firmware_has_bootmgr(text: str) -> bool:
    blob = text or ""
    if not re.search(r"Windows Boot Manager", blob, re.I):
        return False
    return bool(re.search(r"displayorder\s+\{bootmgr\}", blob, re.I))


def nvram_bcdboot_args(exe: str, windows: str, *, mode: str = "UEFI", preserve_order: bool = False) -> list[str]:
    chosen = "ALL" if (mode or "").upper() == "ALL" else "UEFI"
    argv = [exe, windows, "/f", chosen]
    if preserve_order:
        argv.append("/p")
    return argv


def displayorder_addfirst_args() -> list[str]:
    return ["bcdedit", "/set", "{fwbootmgr}", "displayorder", "{bootmgr}", "/addfirst"]


def ensure_fallback_loader(esp_root: Path) -> bool:
    """Copy bootmgfw.efi to \\EFI\\Boot\\bootx64.efi only when the fallback is missing."""
    source = esp_root / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi"
    dest = esp_root / "EFI" / "Boot" / "bootx64.efi"
    if dest.is_file():
        return True
    if not source.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest.is_file()


def ensure_firmware_entry(
    *,
    exe: str,
    windows: str,
    mode: str,
    runner: Runner,
    linux_present: bool = False,
    enum_text: str | None = None,
) -> dict:
    """Return ok when {bootmgr} is in the firmware display order."""
    if enum_text is None:
        _code, enum_text = runner(["bcdedit", "/enum", "firmware"], timeout=30)
    if firmware_has_bootmgr(enum_text or ""):
        return {"ok": True, "action": "already-present", "enum": enum_text}
    if (mode or "").upper() == "BIOS":
        return {"ok": True, "action": "bios-skip", "enum": enum_text}
    code, output = runner(
        nvram_bcdboot_args(exe, windows, mode=mode, preserve_order=linux_present),
        timeout=120,
    )
    _code, after = runner(["bcdedit", "/enum", "firmware"], timeout=30)
    if firmware_has_bootmgr(after or ""):
        return {"ok": True, "action": "bcdboot-nvram", "code": code, "enum": after}
    if not linux_present:
        runner(displayorder_addfirst_args(), timeout=30)
        _code, after = runner(["bcdedit", "/enum", "firmware"], timeout=30)
        if firmware_has_bootmgr(after or ""):
            return {"ok": True, "action": "addfirst", "enum": after}
    return {"ok": False, "action": "missing", "code": code, "enum": after, "output": output}
