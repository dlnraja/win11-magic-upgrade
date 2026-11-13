"""MBR -> GPT + Boot Manager repair - no wipe, no PowerShell."""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
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
    from .proc_guard import run_bounded

    result = run_bounded(cmd, timeout=timeout)
    return result.code, result.output or result.reason


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
    The ESP path comes from a fresh drive map. A timeout or failed check
    unmounts only what this call mounted.
    """
    from .drive_map import access_root, bcdboot_target
    from .esp_access import bcdboot_command, poll_if_due, prepare_boot_step, resolved_bcd_mode
    from .proc_guard import GateState, PostState, probe_admin, precheck, postcheck
    from .secureboot_certs import efi_write_block_reason

    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    if not bcdboot.exists():
        log("bcdboot.exe missing", "ERROR")
        return False

    log("Repairing Windows Boot Manager (bcdboot, no format)...", "STEP")
    from .self_update import boot_critical_section

    with boot_critical_section():
        return _repair_boot_locked(prefer_uefi, sys_root, bcdboot)


def _font_dirs(root: Path) -> list[Path]:
    return [root / "EFI" / "Microsoft" / "Boot" / "Fonts", root / "Boot" / "Fonts"]


def _font_bytes(root: Path) -> int:
    total = 0
    for folder in _font_dirs(root):
        if not folder.is_dir():
            continue
        for item in folder.rglob("*"):
            if item.is_file() and item.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    return total


def _dir_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    files = [path] if path.is_file() else list(path.rglob("*"))
    for item in files:
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def _locale_dirs(root: Path) -> list[Path]:
    found: list[Path] = []
    for base in (root / "EFI" / "Microsoft" / "Boot", root / "Boot"):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir():
                found.append(child)
    return found


def _our_junk(root: Path) -> list[Path]:
    from .drive_map import is_our_esp_junk

    base = root / "EFI" / "MagicUpgrade"
    if not base.is_dir():
        return []
    return [item for item in base.rglob("*") if item.is_file() and is_our_esp_junk(item.relative_to(root).as_posix())]


def _backup_esp_items(root: Path, items: list[Path]) -> bool:
    """Copy items off the ESP. Nothing is deleted when a copy fails."""
    if not items:
        return True
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_root = STATE_DIR / "esp-space-backups" / stamp
    try:
        for item in items:
            target = dest_root / item.relative_to(root)
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    except Exception as exc:
        log(f"ESP space backup failed. Nothing was deleted: {exc}", "ERROR")
        return False
    log(f"ESP space backup saved to {dest_root}", "OK")
    return True


def _free_esp_for_bcdboot(root: Path, ui: str, *, force: bool = False) -> tuple[bool, bool]:
    """Back up, then drop fonts, unused locales, and our temp files.

    Returns (safe_to_continue, use_minimal_locale). A failed backup stops the repair.
    Partitions are never resized here.
    """
    from .drive_map import esp_bcdboot_space_plan, locale_dirs_to_drop

    try:
        free = shutil.disk_usage(root).free
    except OSError:
        free = 0
    fonts = _font_bytes(root)
    locales = _locale_dirs(root)
    drop_names = {name.lower() for name in locale_dirs_to_drop([item.name for item in locales], ui)}
    drop = [item for item in locales if item.name.lower() in drop_names]
    junk = _our_junk(root)
    plan = esp_bcdboot_space_plan(
        free,
        fonts,
        locale_bytes=sum(_dir_bytes(item) for item in drop),
        junk_bytes=sum(_dir_bytes(item) for item in junk),
    )
    log(
        f"ESP free {free} bytes before bcdboot. "
        "Partitions are not resized unless MAGIC_ESP_EXPAND=1, and this repair does not resize them.",
        "INFO",
    )
    use_minimal = bool(plan["minimal"] or force)
    if not use_minimal:
        return True, False
    if not plan["backup_required"] and not force:
        log("ESP has too little free space and nothing non-essential to remove.", "WARN")
        return True, True
    items: list[Path] = []
    if plan["trim_fonts"] or force:
        items.extend(path for path in _font_dirs(root) if path.is_dir())
    if plan["drop_locales"] or force:
        items.extend(drop)
    if plan["clear_junk"] or force:
        items.extend(junk)
    # force with an empty list still switches bcdboot to /l
    if items and not _backup_esp_items(root, items):
        log("NO-GO: ESP backup failed, so fonts and locales were left in place.", "ERROR")
        return False, True
    font_set = {path for path in _font_dirs(root)}
    for item in items:
        if item in font_set:
            for child in list(item.rglob("*")):
                if child.is_file() and child.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    try:
                        child.unlink()
                    except OSError:
                        pass
        elif item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass
    if items:
        log("ESP fonts, unused locales, and our temp files were backed up and trimmed for bcdboot /l.", "OK")
    return True, True


def _repair_boot_locked(prefer_uefi: bool, sys_root: str, bcdboot: Path) -> bool:
    from .drive_map import (
        access_root,
        bare_bcdboot_letter,
        bcdboot_123_remediation,
        bcdboot_disk_full,
        bcdboot_invalid_name,
        bcdboot_locale,
        bcdboot_remediation,
        bcdboot_source_root,
        minimal_bcdboot_args,
    )
    from .esp_access import bcdboot_command, open_bcdboot_letter, poll_if_due, prepare_boot_step, resolved_bcd_mode
    from .proc_guard import GateState, PostState, probe_admin, precheck, postcheck
    from .secureboot_certs import efi_write_block_reason
    from .sysreserved import mount_esp, unmount_letter

    step = prepare_boot_step("bcdboot")
    if step is not None and step.abort:
        return False
    if step is not None and step.skip_loader_overwrite:
        log(step.explanation or "ESP loader is newer; not overwriting", "WARN")
        return True

    bitlocker = "unknown"
    if step is not None and step.esp is not None:
        bitlocker = step.esp.bitlocker or "unknown"
    ok_pre, why = precheck(
        GateState(
            is_admin=probe_admin(),
            target_exists=bcdboot.is_file() and Path(sys_root).is_dir(),
            bitlocker=bitlocker,
        )
    )
    if not ok_pre:
        log(why, "ERROR")
        return False

    source = bcdboot_source_root(sys_root)
    if not source:
        log("BCD failed, error 123: %SystemRoot% is missing or quoted.", "ERROR")
        return False

    mounted = mount_esp()
    assigned_temp = False
    target: str | None = None
    if mounted:
        opened = access_root(mounted) or mounted
        target, assigned_temp = open_bcdboot_letter(opened, step)
        if target:
            log(f"Boot volume access path {target}", "OK")
    elif step is not None and step.bcd_mode == "BIOS" and step.mount is not None:
        target, assigned_temp = open_bcdboot_letter(step.mount.path, step)
        log("BIOS boot: mountvol /S is not used and no ESP is created.", "INFO")
    else:
        target, assigned_temp = open_bcdboot_letter(None, step)

    if target:
        target = bare_bcdboot_letter(target)
    modes = resolved_bcd_mode(prefer_uefi, step)
    ok = False
    timed_out = False
    minimal = False
    space_root = Path((target or "") + "\\") if target and len(str(target)) == 2 and str(target)[1] == ":" else None
    try:
        if not modes:
            log("BCD skipped: firmware mode is unknown, so bcdboot /f ALL is not run.", "ERROR")
        if space_root is not None and space_root.exists():
            room_ok, minimal = _free_esp_for_bcdboot(space_root, bcdboot_locale())
            if not room_ok:
                log("NO-GO: ESP backup failed, so bcdboot was not run on a full volume.", "ERROR")
                return False
        for mode in modes:
            fresh = poll_if_due("bcdboot")
            if fresh is not None and fresh.abort:
                log(fresh.explanation, "ERROR")
                return False
            cert_reason = efi_write_block_reason(mode)
            if cert_reason:
                log(cert_reason, "ERROR")
                continue
            if not target:
                log(f"BCD {mode} failed, error 123: /s is not a bare drive letter.", "ERROR")
                for line in bcdboot_123_remediation():
                    log(f"  {line}", "ERROR")
                break
            try:
                if minimal:
                    cmd = minimal_bcdboot_args(str(bcdboot), source, target, mode, bcdboot_locale())
                else:
                    cmd = bcdboot_command(str(bcdboot), source, target, mode)
            except ValueError as exc:
                log(f"BCD {mode} failed, error 123: {exc}", "ERROR")
                for line in bcdboot_123_remediation():
                    log(f"  {line}", "ERROR")
                break
            code, out = _run(cmd, timeout=180)
            log(f"bcdboot {' '.join(cmd[1:])} -> {code}")
            for line in (out or "").splitlines()[:8]:
                log(f"  {line}")
            if code == 124 or "timeout" in (out or "").lower()[:80]:
                timed_out = True
                log(out or "bcdboot timed out", "ERROR")
                break
            from .boot_safe import bcdboot_succeeded

            if bcdboot_invalid_name(code, out or ""):
                log(f"BCD {mode} failed, error 123", "ERROR")
                for line in bcdboot_123_remediation():
                    log(f"  {line}", "ERROR")
                break
            if bcdboot_disk_full(code, out or ""):
                log(f"BCD {mode} failed, error 112", "ERROR")
                for line in bcdboot_remediation(112):
                    log(f"  {line}", "ERROR")
                if not minimal and space_root is not None and space_root.exists():
                    room_ok, minimal = _free_esp_for_bcdboot(space_root, bcdboot_locale(), force=True)
                    if not room_ok:
                        log("NO-GO: ESP backup failed after bcdboot error 112.", "ERROR")
                        break
                    cmd = minimal_bcdboot_args(str(bcdboot), source, target, mode, bcdboot_locale())
                    code, out = _run(cmd, timeout=180)
                    log(f"bcdboot {' '.join(cmd[1:])} -> {code}")
                    if bcdboot_succeeded(code, out or ""):
                        ok = True
                        log(f"Boot Manager repaired ({mode}) after freeing ESP space", "OK")
                        break
                    if bcdboot_disk_full(code, out or ""):
                        log("BCD still error 112 after the minimal locale retry. The partition was not resized.", "ERROR")
                break
            if bcdboot_succeeded(code, out or ""):
                ok = True
                log(f"Boot Manager repaired ({mode})", "OK")
                break
            log(f"bcdboot {mode} failed (exit {code})", "ERROR")
    finally:
        if assigned_temp and target:
            _run(["mountvol", f"{target[:1]}:", "/d"], timeout=60)
            log(f"Removed temporary bcdboot letter {target}", "INFO")
        if mounted:
            unmount_letter(mounted)

    if timed_out:
        return False
    if ok and step is not None and target:
        _finish_bios_boot(step, target)
    code, out = _run(["bcdedit", "/enum", "{bootmgr}"], timeout=90)
    bcd_ok = code == 0 and bool(out) and "cannot" not in (out or "").lower()
    log(f"re-verify bcdedit {{bootmgr}}: exit {code}", "OK" if bcd_ok and ok else "ERROR")
    if out:
        log("bcdedit {bootmgr}: " + out.splitlines()[0][:120])
    if not bcd_ok:
        ok = False
    passed, reason = postcheck(
        PostState(files_written=ok, bcd_present=bcd_ok if ok else None, esp_ok=True if ok else None)
    )
    if ok and not passed:
        log(reason, "ERROR")
        return False
    return ok


def _finish_bios_boot(step: object, target: str) -> None:
    """Write MBR boot code and, if needed, one active flag. Never creates an ESP."""
    from .drive_map import drive_letter_of
    from .proc_guard import run_bounded

    actions = set(getattr(step, "actions", ()) or ())
    letter = drive_letter_of(target)
    if letter and ({"bootsect_nt60", "bootsect_volume_only"} & actions):
        exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "bootsect.exe"
        if not exe.is_file():
            log("bootsect.exe is missing. The volume boot sector was left unchanged.", "WARN")
        else:
            cmd = [str(exe), "/nt60", f"{letter}:"]
            if "bootsect_nt60" in actions:
                cmd.append("/mbr")
            result = run_bounded(cmd, timeout=60)
            if result.ok:
                log(f"bootsect {' '.join(cmd[1:])} ok", "OK")
            else:
                log(f"bootsect failed: {result.reason or result.output[:200]}", "WARN")
    if letter and "mark_active" in actions:
        from .diskpart_safe import run_diskpart

        ok_act, out = run_diskpart(f"select volume {letter}\nactive\nexit\n", timeout=60)
        if ok_act:
            log(f"Marked {letter}: active after BIOS bcdboot", "OK")
        else:
            log(f"Could not mark {letter}: active: {(out or '')[:200]}", "WARN")


def convert_mbr_to_gpt(disk_number: int) -> tuple[bool, int, str]:
    from .esp_access import prepare_boot_step
    from .proc_guard import GateState, precheck, probe_admin

    step = prepare_boot_step("mbr2gpt")
    if step is not None and step.abort:
        return False, -1, step.explanation or "drive map has no safe mbr2gpt target"
    if step is not None and step.strategy in ("hybrid_mbr_gpt", "bios_mbr", "opencore", "openduet", "ia32_hybrid"):
        return False, -1, step.explanation or f"mbr2gpt does not match the current boot plan ({step.strategy})"
    if step is not None and step.windows is not None and not step.windows_ambiguous:
        if int(step.windows.disk) >= 0 and int(step.windows.disk) != int(disk_number):
            log(
                f"Windows volume is on disk {step.windows.disk} after rescan "
                f"(argument was {disk_number}). Using the fresh map.",
                "WARN",
            )
            disk_number = int(step.windows.disk)
    if disk_number is None or int(disk_number) < 0:
        return False, -1, "system disk # unknown — refuse mbr2gpt (safety)"

    bitlocker = "unknown"
    if step is not None and step.windows is not None:
        bitlocker = step.windows.bitlocker or "unknown"
    ok_pre, why = precheck(GateState(is_admin=probe_admin(), bitlocker=bitlocker, target_exists=True))
    if not ok_pre:
        return False, -1, why

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
