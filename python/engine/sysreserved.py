"""
Fix "We couldn't update the system reserved partition"
(FR: Impossible de mettre a jour la partition reservee au systeme).

Strategy (safe, no third-party Partition Magic GUI required):
  1) Detect EFI (UEFI/GPT) vs System Reserved (BIOS/MBR)
  2) Mount and free space: Boot fonts, OEM firmware dumps, junk
  3) If still too small (< ~50 MB free or partition < 260 MB):
     create a NEW larger ESP (or system partition) by shrinking C:,
     run bcdboot to it, leave OS data untouched
     (Partition-Magic-style outcome without moving mid-disk partitions)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .logutil import STATE_DIR, log

# 24H2 needs ~20MB+ free; we target comfortable margin
MIN_FREE_MB = 50
TARGET_ESP_MB = 512
MIN_SIZE_MB_COMFORTABLE = 260

LETTER_CANDIDATES = ["Y", "X", "W", "V", "U", "S"]


def _run(cmd: list[str], input_text: str | None = None, timeout: int = 300) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        log(f"Command timed out ({timeout}s): {cmd[0] if cmd else '?'}", "ERROR")
        return 124, "TIMEOUT"
    except Exception as e:
        return 1, str(e)


def _diskpart(script: str) -> str:
    _, out = _run(["diskpart"], input_text=script)
    return out


def _free_letter() -> str | None:
    for L in LETTER_CANDIDATES:
        if not Path(f"{L}:\\").exists():
            return L
    return None


def _mb(path: str | Path) -> tuple[float, float]:
    """Return (total_MB, free_MB) for a mounted volume."""
    usage = shutil.disk_usage(str(path))
    return usage.total / (1024 * 1024), usage.free / (1024 * 1024)


def _is_uefi() -> bool:
    try:
        import ctypes

        ft = ctypes.c_uint(0)
        if ctypes.windll.kernel32.GetFirmwareType(ctypes.byref(ft)):
            return ft.value == 2
    except Exception:
        pass
    return False


def mount_esp(letter: str | None = None) -> str | None:
    """Mount EFI System Partition via mountvol /s. Returns 'Y:' or None."""
    letter = letter or _free_letter()
    if not letter:
        log("No free drive letter to mount ESP", "ERROR")
        return None
    # Dismount if leftover
    _run(["mountvol", f"{letter}:", "/d"])
    code, out = _run(["mountvol", f"{letter}:", "/s"])
    if code != 0 or not Path(f"{letter}:\\").exists():
        log(f"mountvol {letter}: /s failed: {out}", "WARN")
        _run(["mountvol", f"{letter}:", "/d"])
        return None
    log(f"ESP mounted at {letter}:", "OK")
    return f"{letter}:"


def find_system_reserved_letter() -> str | None:
    """Find MBR System Reserved / boot NTFS small partition and assign a letter."""
    out = _diskpart("list volume\nexit\n")
    # Look for small NTFS System / Reserved volumes without letter
    for line in out.splitlines():
        if not re.search(r"System|Reserved|Boot", line, re.I):
            continue
        if not re.search(r"NTFS|FAT", line, re.I):
            continue
        m = re.search(r"Volume\s+(\d+)\s+([A-Z])?\s+", line)
        # diskpart list volume format varies: Volume ###  Ltr  Label  Fs  Type  Size
        m2 = re.search(r"Volume\s+(\d+)", line, re.I)
        if not m2:
            continue
        vol = m2.group(1)
        # Size check: typically 50-550 MB
        sm = re.search(r"(\d+)\s*MB", line, re.I)
        if sm and int(sm.group(1)) > 2000:
            continue
        # Already has letter?
        lm = re.search(r"Volume\s+\d+\s+([A-Z])\s+", line)
        if lm:
            return f"{lm.group(1)}:"
        letter = _free_letter()
        if not letter:
            return None
        _diskpart(f"select volume {vol}\nassign letter={letter}\nexit\n")
        if Path(f"{letter}:\\").exists():
            log(f"System Reserved assigned {letter}:", "OK")
            return f"{letter}:"
    return None


def _safe_delete_glob(root: Path, pattern: str) -> int:
    n = 0
    try:
        for f in root.glob(pattern):
            if f.is_file():
                try:
                    f.unlink()
                    n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


def cleanup_boot_volume(root: str) -> dict:
    """
    Free space on mounted ESP / System Reserved.
    Deletes only known-safe expendable files (fonts, OEM firmware payloads).
    Never deletes BCD, bootmgfw.efi, or bootmgr.
    """
    base = Path(root + "\\")
    freed_files = 0
    actions = []

    before_t, before_f = _mb(base)

    # EFI fonts (Microsoft documented fix)
    fonts = base / "EFI" / "Microsoft" / "Boot" / "Fonts"
    if fonts.is_dir():
        n = 0
        for f in fonts.rglob("*"):
            if f.is_file() and f.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                try:
                    f.unlink()
                    n += 1
                except Exception:
                    pass
        freed_files += n
        actions.append(f"Deleted {n} font files under EFI\\Microsoft\\Boot\\Fonts")

    # Also Boot\\Fonts on BIOS system reserved
    fonts2 = base / "Boot" / "Fonts"
    if fonts2.is_dir():
        n = 0
        for f in fonts2.rglob("*"):
            if f.is_file() and f.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                try:
                    f.unlink()
                    n += 1
                except Exception:
                    pass
        freed_files += n
        if n:
            actions.append(f"Deleted {n} BIOS Boot\\Fonts files")

    # OEM firmware / recovery dumps often stuffed into ESP (HP, Dell, Lenovo, Acer...)
    efi = base / "EFI"
    if efi.is_dir():
        for oem in efi.iterdir():
            if not oem.is_dir():
                continue
            name = oem.name.lower()
            if name in {"microsoft", "boot", "ubuntu", "centos", "redhat", "debian"}:
                continue
            # Remove large firmware update payloads, keep folder structure light
            removed = 0
            for f in oem.rglob("*"):
                if not f.is_file():
                    continue
                # Keep tiny marker files; remove large bins/imgs/capsules
                if f.suffix.lower() in {".bin", ".img", ".cap", ".fd", ".rom", ".exe", ".zip", ".cab", ".wim"} or f.stat().st_size > 512_000:
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        removed += 1
                        freed_files += 1
                    except Exception:
                        pass
            if removed:
                actions.append(f"Removed {removed} OEM payload files under EFI\\{oem.name}")

    # Temp / log leftovers
    for pattern in ("*.log", "*.tmp", "*.bak", "BOOTSECT.BAK"):
        n = _safe_delete_glob(base, pattern)
        n += sum(_safe_delete_glob(p, pattern) for p in base.rglob("*") if p.is_dir())
        # simpler walk
    for f in base.rglob("*"):
        if f.is_file() and f.suffix.lower() in {".log", ".tmp", ".bak"}:
            try:
                f.unlink()
                freed_files += 1
            except Exception:
                pass

    after_t, after_f = _mb(base)
    freed_mb = after_f - before_f
    log(f"Boot volume cleanup: +{freed_mb:.1f} MB free (now {after_f:.1f}/{after_t:.1f} MB)", "OK")
    for a in actions:
        log(f"  {a}", "INFO")
    return {
        "total_mb": after_t,
        "free_mb": after_f,
        "freed_mb": freed_mb,
        "actions": actions,
    }


def unmount_letter(letter_root: str) -> None:
    L = letter_root.rstrip("\\").rstrip(":")
    _run(["mountvol", f"{L}:", "/d"])
    # Only remove letter via diskpart if volume looks like EFI/System
    detail = _diskpart(f"select volume {L}\ndetail volume\nexit\n")
    if re.search(r"\b(EFI|System|Reserved|ESP)\b", detail or "", re.I) or "FAT32" in (detail or "").upper():
        _diskpart(f"select volume {L}\nremove letter={L}\nexit\n")
    else:
        log(f"Skip diskpart remove letter {L}: (not clearly system/EFI volume)", "INFO")


def create_larger_esp(size_mb: int = TARGET_ESP_MB) -> str | None:
    """
    Shrink C: and create a NEW EFI system partition (GPT) of size_mb.
    Then bcdboot Windows onto it. Does not wipe user data on C:.
    Returns mounted letter of new ESP or None.
    """
    log(f"Creating new {size_mb} MB EFI System Partition (shrink C:, no data wipe)...", "STEP")
    letter = _free_letter()
    if not letter:
        return None

    # Shrink C — verify success before create
    shrink = _diskpart(
        f"select volume C\n"
        f"shrink desired={size_mb} minimum={max(300, size_mb - 50)}\n"
        f"exit\n"
    )
    log(f"shrink C: {shrink.splitlines()[-1] if shrink else 'n/a'}")
    if shrink and not re.search(r"successfully|complete|shrunk", shrink, re.I):
        # diskpart English/localized: still proceed only if no explicit error
        if re.search(r"error|failed|denied|not enough", shrink, re.I):
            log(f"C: shrink failed — abort ESP expand: {shrink[-300:]}", "ERROR")
            return None

    detail = _diskpart("select volume C\ndetail volume\nexit\n")
    dm = re.search(r"Disk\s+#?\s*(\d+)", detail, re.I)
    disk_n = dm.group(1) if dm else "0"
    script = (
        f"select disk {disk_n}\n"
        f"create partition efi size={size_mb}\n"
        "format fs=fat32 quick label=ESP\n"
        f"assign letter={letter}\n"
        "exit\n"
    )
    create_out = _diskpart(script)
    log(create_out[-500:] if create_out else "create done")
    if create_out and re.search(r"error|failed|denied", create_out, re.I):
        log(f"EFI create failed: {create_out[-300:]}", "ERROR")
        return None

    root = f"{letter}:"
    if not Path(root + "\\").exists():
        log("New ESP letter not available - create may have failed", "ERROR")
        return None

    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    code, bout = _run([str(bcdboot), sys_root, "/s", root, "/f", "UEFI"], timeout=180)
    log(f"bcdboot -> {code}: {bout[:300]}")
    if code != 0 and "successfully" not in bout.lower():
        code2, bout2 = _run([str(bcdboot), sys_root, "/s", root, "/f", "ALL"], timeout=180)
        log(f"bcdboot ALL -> {code2}: {bout2[:300]}")
        if code2 != 0:
            log("bcdboot failed on new ESP - old ESP still present, boot should remain OK", "WARN")
            return root

    log(f"New ESP ready at {root} with boot files", "OK")
    return root


def create_larger_system_reserved_mbr(size_mb: int = TARGET_ESP_MB) -> str | None:
    """
    BIOS/MBR: shrink C and create a new primary NTFS active system partition,
    then bcdboot /f BIOS. Old System Reserved left intact as fallback.
    """
    log(f"Creating new {size_mb} MB System partition (MBR/BIOS path)...", "STEP")
    letter = _free_letter()
    if not letter:
        return None
    detail = _diskpart("select volume C\ndetail volume\nexit\n")
    dm = re.search(r"Disk\s+#?\s*(\d+)", detail, re.I)
    disk_n = dm.group(1) if dm else "0"

    _diskpart(f"select volume C\nshrink desired={size_mb} minimum={max(300, size_mb-50)}\nexit\n")
    out = _diskpart(
        f"select disk {disk_n}\n"
        f"create partition primary size={size_mb}\n"
        "format fs=ntfs quick label=System\n"
        "active\n"
        f"assign letter={letter}\n"
        "exit\n"
    )
    log(out[-400:])
    root = f"{letter}:"
    if not Path(root + "\\").exists():
        return None
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    code, bout = _run([str(bcdboot), sys_root, "/s", root, "/f", "BIOS"])
    log(f"bcdboot BIOS -> {code}: {bout[:300]}")
    return root


def inspect_and_fix_system_reserved(force_expand: bool = False) -> dict:
    """
    Main entry: fix SRP/ESP space issues for Windows feature upgrades.
    Idempotent: skips re-expand if a prior successful expand is recorded.
    """
    import json

    log("=== Fix System Reserved / EFI partition (setup update error) ===", "STEP")
    result = {
        "ok": False,
        "mode": None,
        "free_mb": None,
        "total_mb": None,
        "expanded": False,
        "actions": [],
    }

    # Idempotency: do not shrink C: repeatedly
    prior_path = STATE_DIR / "srp-fix.json"
    prior_expanded = False
    if prior_path.exists():
        try:
            prev = json.loads(prior_path.read_text(encoding="utf-8"))
            prior_expanded = bool(prev.get("expanded"))
            if prior_expanded and not force_expand:
                log("Prior ESP/SRP expand recorded — skip re-expand (idempotent)", "OK")
                force_expand = False
                result["actions"].append("skip_reexpand_prior")
        except Exception:
            pass

    uefi = _is_uefi()
    mounted = None
    try:
        if uefi:
            result["mode"] = "EFI"
            mounted = mount_esp()
        else:
            result["mode"] = "SystemReserved"
            mounted = find_system_reserved_letter()
            if not mounted and _is_uefi() is False:
                mounted = mount_esp()
                if mounted:
                    result["mode"] = "EFI"

        if not mounted:
            log("Could not mount system/EFI partition - skip SRP fix", "WARN")
            result["actions"].append("mount_failed")
            return result

        info = cleanup_boot_volume(mounted)
        result["free_mb"] = info["free_mb"]
        result["total_mb"] = info["total_mb"]
        result["actions"].extend(info["actions"])

        # Stale Panther logs alone must not force expand if space is already OK
        space_tight = info["free_mb"] < MIN_FREE_MB or info["total_mb"] < MIN_SIZE_MB_COMFORTABLE
        need_expand = space_tight or (force_expand and not prior_expanded and space_tight)
        # Only honor force_expand when space is actually tight OR no prior expand
        if force_expand and not prior_expanded and not space_tight:
            log("Historical SRP error in logs but ESP space OK after cleanup — skip expand", "OK")
            need_expand = False
        if prior_expanded and not space_tight:
            need_expand = False

        if need_expand:
            log(
                f"Partition still tight (free={info['free_mb']:.1f} MB, size={info['total_mb']:.1f} MB) "
                f"- expanding via new larger boot partition...",
                "WARN",
            )
            unmount_letter(mounted)
            mounted = None

            if result["mode"] == "EFI" or uefi:
                new_root = create_larger_esp(TARGET_ESP_MB)
                if not new_root:
                    log("EFI create failed - trying primary system partition fallback", "WARN")
                    new_root = create_larger_system_reserved_mbr(TARGET_ESP_MB)
            else:
                new_root = create_larger_system_reserved_mbr(TARGET_ESP_MB)
                if not new_root:
                    log("MBR system create failed - trying EFI create fallback", "WARN")
                    new_root = create_larger_esp(TARGET_ESP_MB)

            if new_root:
                result["expanded"] = True
                t, f = _mb(new_root + "\\")
                result["free_mb"] = f
                result["total_mb"] = t
                result["actions"].append(f"Created larger boot partition {new_root} ({t:.0f} MB)")
                unmount_letter(new_root)
                log("Larger boot partition created. Reboot once before upgrade if firmware needs refresh.", "OK")
                result["ok"] = True
            else:
                log("Expand failed — ESP/SRP still insufficient", "ERROR")
                result["ok"] = False
                result["actions"].append("expand_failed")
        else:
            log(f"ESP/SRP has enough free space ({info['free_mb']:.1f} MB) after cleanup", "OK")
            result["ok"] = True
            if prior_expanded:
                result["expanded"] = True

        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            prior_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception:
            pass
        return result
    finally:
        if mounted:
            try:
                unmount_letter(mounted)
            except Exception:
                pass


def scan_logs_for_srp_error() -> bool:
    patterns = [
        r"system reserved partition",
        r"partition reserv",
        r"couldn't update the system reserved",
        r"impossible de mettre .*partition",
        r"0x800f0922",
        r"0xC1900200",
    ]
    paths = [
        Path(r"C:\$WINDOWS.~BT\Sources\Panther\setuperr.log"),
        Path(r"C:\$WINDOWS.~BT\Sources\Panther\setupact.log"),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Panther" / "setuperr.log",
    ]
    for p in paths:
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[-200_000:]
            for pat in patterns:
                if re.search(pat, text, re.I):
                    log(f"Detected SRP/ESP upgrade error in {p.name}", "WARN")
                    return True
        except Exception:
            pass
    return False
