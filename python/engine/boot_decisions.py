"""Boot and firmware decisions that stay read-only unless a flag is set."""
from __future__ import annotations

import os
import re
from pathlib import Path


def ca2023_card(*, status: str, error: str = "") -> dict:
    updated = (status or "").strip().lower() == "updated"
    return {
        "go": updated and not error,
        "status": status or "unknown",
        "error": error,
        "message": (
            "Secure Boot CA 2023 is updated."
            if updated and not error
            else "Secure Boot CA 2023 is not updated. Install the OEM BIOS update before writing db or the boot manager."
        ),
    }


def classify_800f0922(text: str) -> str:
    """esp, dbx, or unknown. ESP fixes must not run for a DBX failure."""
    blob = text or ""
    if not re.search(r"0x800f0922", blob, re.I):
        return "unknown"
    if re.search(r"DBX|Secure ?Boot|KB5012170", blob, re.I):
        return "dbx"
    if re.search(r"EFI|ESP|insufficient", blob, re.I):
        return "esp"
    return "unknown"


def bootrec_fixboot_allowed(firmware: str) -> bool:
    """bootrec /fixboot is an MBR tool. UEFI uses bcdboot."""
    return (firmware or "").strip().upper() not in ("UEFI", "EFI")


def bcdboot_ready(*, has_template: bool, filesystem: str, esp_type: bool, free_mb: float, firmware: str) -> tuple[bool, str]:
    if not has_template:
        return False, "The source is missing System32\\Config\\BCD-Template."
    fw = (firmware or "").upper()
    fs = (filesystem or "").upper()
    if fw in ("UEFI", "EFI"):
        if fs != "FAT32" or not esp_type:
            return False, "The UEFI target must be a FAT32 EFI system partition."
    elif fw in ("BIOS", "MBR", "LEGACY"):
        if fs != "NTFS":
            return False, "The BIOS target must be an active NTFS system partition."
    if free_mb < 10:
        return False, f"The target has {free_mb:.1f} MB free. bcdboot needs at least 10 MB."
    return True, ""


def storage_spaces_warning(*, unhealthy: bool, refs: bool) -> str:
    if not unhealthy and not refs:
        return ""
    parts = []
    if unhealthy:
        parts.append("A Storage Spaces pool is not healthy.")
    if refs:
        parts.append("A ReFS or Dev Drive volume is present.")
    return " ".join(parts) + " Back up before the upgrade. The pool is not upgraded automatically."


def collect_boot_info() -> str:
    """Read-only summary. Missing tools are reported, not treated as a failure."""
    import subprocess

    system = os.environ.get("SystemRoot", r"C:\Windows")
    bcdedit = str(Path(system) / "System32" / "bcdedit.exe")
    reagentc = str(Path(system) / "System32" / "reagentc.exe")

    def grab(cmd: list[str]) -> str:
        if not Path(cmd[0]).is_file():
            return "(unavailable)"
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"(unavailable: {exc})"
        return ((proc.stdout or "") + (proc.stderr or ""))[:2000] or "(empty)"

    return boot_info_text(
        disks="(drive map is collected by the running app)",
        esp="(ESP is not mounted by this read-only summary)",
        bcd=grab([bcdedit, "/enum", "all", "/v"]),
        firmware=grab([bcdedit, "/enum", "firmware"]),
        reagentc=grab([reagentc, "/info"]),
        secure_boot=os.environ.get("MAGIC_SECURE_BOOT", "unknown"),
        bitlocker=os.environ.get("MAGIC_BITLOCKER", "unknown"),
    )


def boot_info_text(*, disks: str, esp: str, bcd: str, firmware: str, reagentc: str, secure_boot: str, bitlocker: str) -> str:
    """Read-only Boot-Info summary. Callers pass already collected text."""
    return "\n".join(
        [
            "Boot-Info (read-only)",
            "Disks:",
            disks or "(none)",
            "ESP:",
            esp or "(none)",
            "BCD:",
            bcd or "(none)",
            "Firmware:",
            firmware or "(none)",
            "WinRE:",
            reagentc or "(none)",
            f"Secure Boot: {secure_boot or 'unknown'}",
            f"BitLocker: {bitlocker or 'unknown'}",
        ]
    )


def stale_boot_entries(order: list[str], live_guids: set[str]) -> list[str]:
    """Firmware entries whose partition GUID is not on this PC."""
    stale = []
    for entry in order:
        token = entry.strip().lower()
        if token in ("{bootmgr}", "{fwbootmgr}", ""):
            continue
        if token not in {item.lower() for item in live_guids}:
            stale.append(entry)
    return stale


def remove_stale_allowed() -> bool:
    return os.environ.get("MAGIC_REMOVE_STALE_BOOT", "").strip().lower() in ("1", "true", "yes")
