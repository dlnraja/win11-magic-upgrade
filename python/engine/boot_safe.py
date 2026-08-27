"""
Secure, validated MBR / EFI boot-layout edits.

Sensitive path — fail closed:
  1) Preflight (disk identity, BitLocker, firmware, free space on C:)
  2) BCD export backup before mutate
  3) Native repair tiers (cleanup → bcdboot → diskpart expand)
  4) Postflight validation (boot files, partition style, bootmgr)
  5) Intelligent fallbacks: BCD import rollback hints + GParted Live rescue package

GParted is NEVER auto-booted / auto-executed against disks. We only stage the
official Live ISO + human instructions when native expand fails.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .diskpart_safe import (
    ensure_select_disk,
    find_esp_candidates,
    find_system_reserved_candidates,
    get_system_disk_number,
    run_diskpart,
)
from .logutil import STATE_DIR, log

# Official GParted Live (SourceForge) — used only as last-resort rescue media.
# Pin a recent stable amd64 ISO name; URL follows SF redirect pattern.
GPARTED_VERSION = "1.6.0-3"
GPARTED_ISO_NAME = f"gparted-live-{GPARTED_VERSION}-amd64.iso"
GPARTED_ISO_URL = (
    "https://downloads.sourceforge.net/project/gparted/gparted-live-stable/"
    f"{GPARTED_VERSION}/{GPARTED_ISO_NAME}"
)


@dataclass
class BootSnapshot:
    utc: str
    firmware: str  # UEFI | BIOS | Unknown
    disk_number: int | None
    partition_style: str
    system_drive: str
    bitlocker: str
    c_free_gb: float | None
    esp_candidates: int
    srp_candidates: int
    boot_files: dict[str, bool] = field(default_factory=dict)
    bcd_backup: str | None = None
    notes: list[str] = field(default_factory=list)
    safe_to_mutate: bool = False
    block_reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        return 124, "TIMEOUT"
    except Exception as e:
        return 1, str(e)


def _firmware() -> str:
    try:
        import ctypes

        ft = ctypes.c_uint(0)
        if ctypes.windll.kernel32.GetFirmwareType(ctypes.byref(ft)):
            return {1: "BIOS", 2: "UEFI"}.get(ft.value, "Unknown")
    except Exception:
        pass
    return "Unknown"


def _bitlocker_status(drive: str) -> str:
    manage = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32" / "manage-bde.exe"
    if not manage.exists():
        return "unknown"
    code, out = _run([str(manage), "-status", drive], timeout=60)
    if code != 0:
        return "unknown"
    if re.search(r"Protection\s*(Status)?\s*:\s*On|Protection On", out, re.I):
        return "on"
    if re.search(r"Protection\s*(Status)?\s*:\s*Off|Protection Off|Fully Decrypted", out, re.I):
        return "off"
    if re.search(r"Lock Status:\s*Locked", out, re.I):
        return "locked"
    return "unknown"


def _c_free_gb() -> float | None:
    try:
        u = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        return round(u.free / (1024**3), 2)
    except Exception:
        return None


def _partition_style(disk_n: int | None) -> str:
    if disk_n is None or disk_n < 0:
        return "Unknown"
    ok, out = ensure_select_disk(int(disk_n))
    if not ok:
        return "Unknown"
    if re.search(r"GPT", out, re.I):
        return "GPT"
    if re.search(r"MBR", out, re.I):
        return "MBR"
    return "Unknown"


def inspect_boot_files_on_letter(letter: str) -> dict[str, bool]:
    root = Path(f"{letter.strip().rstrip(':')}:\\")
    checks = {
        "efi_boot_bootx64": root / "EFI" / "Boot" / "bootx64.efi",
        "efi_boot_bootia32": root / "EFI" / "Boot" / "bootia32.efi",
        "efi_ms_bootmgfw": root / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi",
        "efi_ms_bcd": root / "EFI" / "Microsoft" / "Boot" / "BCD",
        "bios_bootmgr": root / "bootmgr",
        "bios_bcd": root / "Boot" / "BCD",
    }
    return {k: p.exists() for k, p in checks.items()}


def backup_bcd() -> Path | None:
    """Export BCD store before sensitive edits."""
    out_dir = STATE_DIR / "bcd-backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = out_dir / f"bcd-export-{stamp}"
    code, out = _run(["bcdedit", "/export", str(dest)], timeout=60)
    if code == 0 and dest.exists():
        log(f"BCD backup exported: {dest}", "OK")
        # Pointer for rollback
        try:
            (out_dir / "LAST.txt").write_text(str(dest), encoding="utf-8")
        except Exception:
            pass
        return dest
    log(f"BCD export failed ({code}): {out[:200]}", "WARN")
    return None


def restore_bcd_from_last() -> bool:
    last = STATE_DIR / "bcd-backups" / "LAST.txt"
    if not last.exists():
        log("No BCD backup pointer for restore", "WARN")
        return False
    path = Path(last.read_text(encoding="utf-8").strip())
    if not path.exists():
        log(f"BCD backup missing: {path}", "ERROR")
        return False
    code, out = _run(["bcdedit", "/import", str(path)], timeout=60)
    if code == 0:
        log(f"BCD restored from {path}", "OK")
        return True
    log(f"BCD import failed: {out[:200]}", "ERROR")
    return False


def preflight_boot_edit(
    *,
    intend: str = "esp-or-mbr",
    system_disk: int | None = None,
    require_disk: bool = True,
) -> BootSnapshot:
    """
    Validate environment before any MBR/EFI mutate.
    Sets safe_to_mutate=False with block_reasons when unsafe.
    """
    log("=" * 60, "STEP")
    log(f"BOOT PREFLIGHT — {intend}", "STEP")
    drive = os.environ.get("SystemDrive", "C:")
    disk = system_disk if system_disk is not None and int(system_disk) >= 0 else get_system_disk_number()
    fw = _firmware()
    style = _partition_style(disk)
    bl = _bitlocker_status(drive)
    snap = BootSnapshot(
        utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        firmware=fw,
        disk_number=disk,
        partition_style=style,
        system_drive=drive,
        bitlocker=bl,
        c_free_gb=_c_free_gb(),
        esp_candidates=len(find_esp_candidates()),
        srp_candidates=len(find_system_reserved_candidates()),
    )

    if require_disk and (disk is None or int(disk) < 0):
        snap.block_reasons.append("system_disk_unknown")
    if bl == "locked":
        snap.block_reasons.append("bitlocker_locked")
    if snap.c_free_gb is not None and snap.c_free_gb < 2.0 and intend in (
        "esp-expand",
        "mbr2gpt",
        "esp-or-mbr",
    ):
        snap.block_reasons.append("c_free_lt_2gb")

    # Ambiguous multi-ESP without clear system disk → caution note (not hard block if disk known)
    if snap.esp_candidates > 3:
        snap.notes.append("many_esp_like_volumes")

    # Suspend BitLocker protectors when On (non-destructive)
    if bl == "on":
        manage = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32" / "manage-bde.exe"
        if manage.exists():
            log("Suspending BitLocker protectors before boot edit...", "STEP")
            _run([str(manage), "-protectors", "-disable", drive])
            snap.notes.append("bitlocker_protectors_suspended")

    bcd = backup_bcd()
    if bcd:
        snap.bcd_backup = str(bcd)
    else:
        snap.notes.append("bcd_backup_failed")
        # Soft: still allow repair-only; hard-block expands if MAGIC_REQUIRE_BCD_BACKUP=1
        if os.environ.get("MAGIC_REQUIRE_BCD_BACKUP", "").strip().lower() in ("1", "true", "yes"):
            if intend in ("esp-expand", "mbr2gpt"):
                snap.block_reasons.append("bcd_backup_required")

    snap.safe_to_mutate = len(snap.block_reasons) == 0
    if snap.safe_to_mutate:
        log(
            f"Preflight OK — disk=#{disk} style={style} fw={fw} BL={bl} C_free={snap.c_free_gb}GB",
            "OK",
        )
    else:
        log(f"Preflight BLOCKED: {', '.join(snap.block_reasons)}", "ERROR")

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "boot-preflight.json").write_text(
            json.dumps(snap.as_dict(), indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return snap


def postflight_boot_edit(
    *,
    expect_uefi: bool | None = None,
    system_disk: int | None = None,
) -> dict[str, Any]:
    """Validate boot layout after edits."""
    log("BOOT POSTFLIGHT — validating...", "STEP")
    from .sysreserved import mount_esp, unmount_letter

    result: dict[str, Any] = {
        "ok": False,
        "firmware": _firmware(),
        "disk_number": system_disk if system_disk is not None else get_system_disk_number(),
        "partition_style": "Unknown",
        "boot_files": {},
        "bcd_bootmgr": False,
        "issues": [],
    }
    disk = result["disk_number"]
    if isinstance(disk, int) and disk >= 0:
        result["partition_style"] = _partition_style(disk)

    # bcdedit bootmgr present?
    code, bout = _run(["bcdedit", "/enum", "{bootmgr}"], timeout=60)
    result["bcd_bootmgr"] = code == 0 and bool(bout) and "cannot" not in bout.lower()
    if not result["bcd_bootmgr"]:
        result["issues"].append("bcd_bootmgr_missing")

    mounted = None
    try:
        mounted = mount_esp()
        if mounted:
            letter = mounted.rstrip(":\\")[:1]
            files = inspect_boot_files_on_letter(letter)
            result["boot_files"] = files
            want_uefi = expect_uefi if expect_uefi is not None else (result["firmware"] == "UEFI")
            if want_uefi:
                if not (files.get("efi_boot_bootx64") or files.get("efi_ms_bootmgfw")):
                    result["issues"].append("missing_uefi_boot_files")
            else:
                if not (files.get("bios_bootmgr") or files.get("bios_bcd")):
                    # UEFI files on BIOS firmware is still OK for hybrid
                    if not (files.get("efi_boot_bootx64") or files.get("efi_ms_bootmgfw")):
                        result["issues"].append("missing_bios_boot_files")
        else:
            result["issues"].append("esp_unmountable")
    finally:
        if mounted:
            try:
                unmount_letter(mounted)
            except Exception:
                pass

    result["ok"] = len(result["issues"]) == 0
    if result["ok"]:
        log("Postflight OK — boot files / BCD look healthy", "OK")
    else:
        log(f"Postflight issues: {', '.join(result['issues'])}", "WARN")

    try:
        (STATE_DIR / "boot-postflight.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return result


def write_gparted_rescue_guide(
    *,
    reason: str,
    system_disk: int | None,
    mode: str,
) -> Path:
    """Human-readable GParted / alternate tool instructions (no auto-execute)."""
    rescue = STATE_DIR / "rescue"
    rescue.mkdir(parents=True, exist_ok=True)
    guide = rescue / "GParted-ESP-Rescue.txt"
    bcd_last = ""
    try:
        p = STATE_DIR / "bcd-backups" / "LAST.txt"
        if p.exists():
            bcd_last = p.read_text(encoding="utf-8").strip()
    except Exception:
        pass

    text = f"""Win11 Magic Upgrade — ESP / MBR rescue (GParted + alternatives)
================================================================
Generated (UTC): {datetime.now(timezone.utc).isoformat()}
Reason: {reason}
Detected mode: {mode}
System disk #: {system_disk if system_disk is not None else "unknown"}
BCD backup: {bcd_last or "(none)"}

IMPORTANT
---------
• Do NOT delete the Windows (C:) partition.
• Prefer enlarging / creating a NEW EFI (FAT32 ~512 MB) or System Reserved,
  then reboot to Windows and run:
    Win11MagicUpgrade.exe --cli --srp
  or repair boot with:
    bcdboot %SystemRoot% /s S: /f UEFI
• GParted Live is a LAST RESORT when Windows diskpart cannot shrink/create.

Recommended GParted steps (UEFI/GPT)
------------------------------------
1) Boot the GParted Live USB (secure boot may need temporary disable).
2) Select the SYSTEM disk (same # as above when known).
3) Shrink the Windows NTFS partition by ~550 MB from the BEGINNING or END
   (leave unallocated space adjacent to the EFI area if possible).
4) Create a new FAT32 partition ~512 MB, label ESP, flags: boot, esp.
5) Do NOT format C:. Apply operations, shut down, remove USB.
6) Boot Windows (old ESP may still work). Then run Magic Upgrade SRP fix /
   bcdboot so boot files are written to the new ESP.

BIOS/MBR alternative
--------------------
Create a ~512 MB NTFS primary, set boot flag only AFTER copying boot files
with bcdboot /f BIOS from Windows.

Other tools (if GParted unavailable)
------------------------------------
• Windows RE → diskpart (same safety rules: never default disk 0)
• Official Microsoft mbr2gpt.exe for MBR→GPT (Magic Upgrade wraps this)
• Rufus + GParted ISO to build the USB
• Vendor partition tools ONLY if you trust them; avoid shareware "partition magic" clones

ISO staging folder
------------------
{rescue}
If download succeeded, look for: {GPARTED_ISO_NAME}

Env overrides
-------------
MAGIC_GPARTED_FALLBACK=1   allow ISO download when native expand fails (default on)
MAGIC_GPARTED_FALLBACK=0   skip download; guide only
MAGIC_REQUIRE_BCD_BACKUP=1 refuse expand/convert if bcdedit /export fails
"""
    guide.write_text(text, encoding="utf-8")
    # Also drop a Desktop copy for visibility
    try:
        desk = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Win11MagicUpgrade-GParted-Rescue.txt"
        if desk.parent.exists():
            shutil.copy2(guide, desk)
    except Exception:
        pass
    log(f"GParted rescue guide written: {guide}", "OK")
    return guide


def download_gparted_iso() -> Path | None:
    """
    Download official GParted Live ISO into the rescue folder.
    Disabled with MAGIC_GPARTED_FALLBACK=0. Never mounts or flashes automatically.
    """
    flag = os.environ.get("MAGIC_GPARTED_FALLBACK", "1").strip().lower()
    if flag in ("0", "false", "no"):
        log("GParted ISO download skipped (MAGIC_GPARTED_FALLBACK=0)", "INFO")
        return None

    rescue = STATE_DIR / "rescue"
    rescue.mkdir(parents=True, exist_ok=True)
    dest = rescue / GPARTED_ISO_NAME
    if dest.exists() and dest.stat().st_size > 50_000_000:
        log(f"GParted ISO already present: {dest}", "OK")
        return dest

    log(f"Downloading GParted Live ISO (rescue only): {GPARTED_ISO_NAME}", "STEP")
    try:
        req = urllib.request.Request(
            GPARTED_ISO_URL,
            headers={"User-Agent": "Win11MagicUpgrade-Rescue/1.21"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f, length=1024 * 1024)
        if dest.stat().st_size < 10_000_000:
            dest.unlink(missing_ok=True)
            log("GParted download too small — discarded", "WARN")
            return None
        log(f"GParted ISO ready: {dest} ({dest.stat().st_size // (1024*1024)} MB)", "OK")
        # Sidecar URL note
        (rescue / "GParted-ISO-SOURCE.txt").write_text(
            f"URL: {GPARTED_ISO_URL}\nFile: {dest}\nVerify checksum on gparted.org if possible.\n",
            encoding="utf-8",
        )
        return dest
    except Exception as e:
        log(f"GParted ISO download failed: {e}", "WARN")
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def prepare_partition_fallbacks(
    *,
    reason: str,
    system_disk: int | None,
    mode: str,
) -> dict[str, Any]:
    """Stage guide + optional GParted ISO after native tools failed."""
    guide = write_gparted_rescue_guide(reason=reason, system_disk=system_disk, mode=mode)
    iso = download_gparted_iso()
    out = {
        "guide": str(guide),
        "iso": str(iso) if iso else None,
        "tools": ["gparted-live", "windows-re-diskpart", "mbr2gpt", "bcdboot"],
    }
    try:
        (STATE_DIR / "rescue" / "LAST.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    except Exception:
        pass
    return out


def validated_repair_boot_manager(*, prefer_uefi: bool = True) -> dict[str, Any]:
    """Preflight → repair → postflight wrapper around mbrgpt.repair_boot_manager."""
    from .mbrgpt import repair_boot_manager

    snap = preflight_boot_edit(intend="bcdboot-repair", require_disk=False)
    result = {
        "ok": False,
        "preflight": snap.as_dict(),
        "repaired": False,
        "postflight": {},
    }
    # Soft blocks only for locked BitLocker
    if "bitlocker_locked" in snap.block_reasons:
        result["error"] = "bitlocker_locked"
        return result

    repaired = repair_boot_manager(prefer_uefi=prefer_uefi)
    result["repaired"] = repaired
    post = postflight_boot_edit(expect_uefi=prefer_uefi, system_disk=snap.disk_number)
    result["postflight"] = post
    result["ok"] = bool(repaired and post.get("ok"))
    if repaired and not post.get("ok"):
        log("bcdboot reported OK but postflight found issues — keep BCD backup", "WARN")
    return result


def validated_mbr_to_gpt(disk_number: int) -> tuple[bool, int, str, dict[str, Any]]:
    """Preflight → mbr2gpt → postflight; prepare GParted rescue on hard fail."""
    from .mbrgpt import convert_mbr_to_gpt

    meta: dict[str, Any] = {}
    snap = preflight_boot_edit(intend="mbr2gpt", system_disk=disk_number, require_disk=True)
    meta["preflight"] = snap.as_dict()
    if not snap.safe_to_mutate:
        return False, -1, "preflight blocked: " + ",".join(snap.block_reasons), meta

    ok, code, msg = convert_mbr_to_gpt(disk_number)
    meta["mbr2gpt"] = {"ok": ok, "code": code, "msg": msg}
    post = postflight_boot_edit(expect_uefi=True, system_disk=disk_number)
    meta["postflight"] = post

    if ok:
        # Soft: conversion OK even if postflight warns (firmware may still be Legacy)
        if not post.get("ok"):
            meta["fallback"] = prepare_partition_fallbacks(
                reason="mbr2gpt_ok_but_postflight_issues",
                system_disk=disk_number,
                mode="UEFI/GPT",
            )
        return True, code, msg, meta

    meta["fallback"] = prepare_partition_fallbacks(
        reason=f"mbr2gpt_failed:{msg}",
        system_disk=disk_number,
        mode="MBR→GPT",
    )
    return False, code, msg, meta
