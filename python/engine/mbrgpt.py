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


def _disk_full(code: int, output: str) -> bool:
    text = output or ""
    if int(code) == 112:
        return True
    return bool(re.search(r"\b112\b|not enough space|espace insuffisant|disk full|espace disque", text, re.I))


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


def _backup_and_trim_fonts(root: Path) -> bool:
    """Copy ESP fonts aside, then remove them. A failed copy deletes nothing."""
    folders = [path for path in _font_dirs(root) if path.is_dir()]
    if not folders:
        return True
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_root = STATE_DIR / "font-backups" / stamp
    try:
        for folder in folders:
            shutil.copytree(folder, dest_root / folder.relative_to(root))
    except Exception as exc:
        log(f"Font backup failed. Fonts were not deleted: {exc}", "ERROR")
        return False
    for folder in folders:
        for item in list(folder.rglob("*")):
            if item.is_file() and item.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                try:
                    item.unlink()
                except OSError:
                    pass
    log(f"ESP fonts backed up to {dest_root} and trimmed so bcdboot can fit", "OK")
    return True


def _repair_boot_locked(prefer_uefi: bool, sys_root: str, bcdboot: Path) -> bool:
    from .drive_map import (
        access_root,
        bcdboot_letter_plan,
        bcdboot_locale,
        bcdboot_target,
        BCD_MIN_FREE_BYTES,
        esp_bcdboot_space_plan,
        minimal_bcdboot_args,
        normalize_volume_guid_path,
    )
    from .esp_access import bcdboot_command, poll_if_due, prepare_boot_step, resolved_bcd_mode
    from .proc_guard import GateState, PostState, probe_admin, precheck, postcheck
    from .secureboot_certs import efi_write_block_reason
    from .sysreserved import mount_esp, unmount_letter
    from .diskpart_safe import assign_letter_to_volume, find_esp_candidates, free_letter, remove_letter_from_volume

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

    mounted = mount_esp()
    esp_vol_index: int | None = None
    assigned_here = False
    temp_mountvol = False
    target: str | None = None
    if mounted:
        opened = access_root(mounted) or mounted
        if normalize_volume_guid_path(opened):
            plan = bcdboot_letter_plan(opened, ())
            volume = str(plan.get("volume") or "")
            letter_only = free_letter()
            letter = f"{letter_only}:" if letter_only else str(plan.get("letter") or "")
            if letter and volume:
                code, out = _run(["mountvol", letter, volume], timeout=60)
                if code == 0 and Path(letter + "\\").exists():
                    target = letter
                    temp_mountvol = True
                    log(f"bcdboot uses temporary letter {letter} for {volume}", "OK")
                else:
                    log(f"Could not mount {volume} on {letter} for bcdboot ({code}): {(out or '')[:160]}", "ERROR")
                    return False
            else:
                log(f"No free letter for bcdboot volume {volume}", "ERROR")
                return False
        else:
            target = bcdboot_target(opened)
        log(f"Boot volume access path {target}", "OK")
    elif step is not None and step.bcd_mode == "BIOS":
        if step.mount is not None and step.mount.action in ("reuse_letter", "reuse_folder"):
            target = step.mount.path
        log("BIOS boot: mountvol /S is not used and no ESP is created.", "INFO")
    else:
        for volume in find_esp_candidates():
            if volume.letter:
                target = f"{volume.letter}:"
                break
            letter = free_letter()
            if not letter:
                break
            if assign_letter_to_volume(volume.index, letter):
                target = f"{letter}:"
                esp_vol_index = volume.index
                assigned_here = True
                log(f"ESP temporarily assigned {letter}: (vol {volume.index})", "OK")
                break

    modes = resolved_bcd_mode(prefer_uefi, step)
    ok = False
    timed_out = False
    minimal = False
    space_root = Path((target or "") + "\\") if target and len(str(target)) == 2 and str(target)[1] == ":" else None
    try:
        if space_root is not None and space_root.exists():
            try:
                free = shutil.disk_usage(space_root).free
            except OSError:
                free = BCD_MIN_FREE_BYTES
            plan_space = esp_bcdboot_space_plan(free, _font_bytes(space_root))
            minimal = bool(plan_space["minimal"])
            if plan_space["trim_fonts"] and not _backup_and_trim_fonts(space_root):
                log("NO-GO: ESP font backup failed, so bcdboot was not retried on a full volume.", "ERROR")
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
            if target and minimal:
                cmd = minimal_bcdboot_args(str(bcdboot), sys_root, target, mode, bcdboot_locale())
            elif target:
                cmd = bcdboot_command(str(bcdboot), sys_root, target, mode)
            else:
                if mode == "ALL":
                    continue
                cmd = [str(bcdboot), sys_root, "/f", mode]
            code, out = _run(cmd, timeout=180)
            log(f"bcdboot {' '.join(cmd[1:])} -> {code}")
            for line in (out or "").splitlines()[:8]:
                log(f"  {line}")
            if code == 124 or "timeout" in (out or "").lower()[:80]:
                timed_out = True
                log(out or "bcdboot timed out", "ERROR")
                break
            from .boot_safe import bcdboot_succeeded

            if bcdboot_succeeded(code, out or ""):
                ok = True
                log(f"Boot Manager repaired ({mode})", "OK")
                break
            if target and space_root is not None and _disk_full(code, out or "") and not minimal:
                if not _backup_and_trim_fonts(space_root):
                    log("NO-GO: ESP font backup failed after bcdboot error 112.", "ERROR")
                    break
                minimal = True
                cmd = minimal_bcdboot_args(str(bcdboot), sys_root, target, mode, bcdboot_locale())
                code, out = _run(cmd, timeout=180)
                log(f"bcdboot {' '.join(cmd[1:])} -> {code}")
                if bcdboot_succeeded(code, out or ""):
                    ok = True
                    log(f"Boot Manager repaired ({mode}) after freeing ESP space", "OK")
                    break
            log(f"bcdboot {mode} failed (exit {code})", "ERROR")
    finally:
        if temp_mountvol and target:
            _run(["mountvol", str(target).rstrip("\\"), "/d"], timeout=60)
            log(f"Removed temporary bcdboot letter {target}", "INFO")
        elif mounted:
            unmount_letter(mounted)
        elif assigned_here and esp_vol_index is not None and target:
            letter = target.rstrip("\\").rstrip(":")[:1]
            remove_letter_from_volume(esp_vol_index, letter)
            log(f"Removed temporary letter {letter}: (vol {esp_vol_index})", "INFO")

    if timed_out:
        return False
    if ok and step is not None and target:
        _finish_bios_boot(step, target)
    code, out = _run(["bcdedit", "/enum", "{bootmgr}"], timeout=90)
    bcd_ok = code == 0 and bool(out) and "cannot" not in (out or "").lower()
    if out:
        log("bcdedit {bootmgr}: " + out.splitlines()[0][:120])
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
