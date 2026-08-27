"""
Fix "We couldn't update the system reserved partition"
(FR: Impossible de mettre a jour la partition reservee au systeme).

Strategy (safe, no third-party Partition Magic GUI required):
  1) Detect EFI (UEFI/GPT) vs System Reserved (BIOS/MBR)
  2) Mount and free space: Boot fonts, OEM firmware dumps, junk
  3) If still too small (< ~50 MB free or partition < 260 MB):
     intelligent planner (extend in-place / smart shrink / create),
     then legacy shrink-C + new ESP, PS Storage, regenerate, GParted stage
     (Partition-Magic-style outcome; GParted never auto-executed)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .diskpart_safe import (
    assign_letter_to_volume,
    ensure_select_disk,
    ensure_select_volume,
    find_esp_candidates,
    find_system_reserved_candidates,
    find_volume_by_letter,
    free_letter as _dp_free_letter,
    get_system_disk_number,
    remove_letter_from_volume,
    run_diskpart,
    shrink_volume_letter,
)
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
    """Legacy wrapper — prefer diskpart_safe helpers for mutate ops."""
    _, out = run_diskpart(script)
    return out


def _free_letter() -> str | None:
    return _dp_free_letter(tuple(LETTER_CANDIDATES))


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
    """Find MBR System Reserved / boot partition and assign a letter (EN+FR diskpart)."""
    cands = find_system_reserved_candidates()
    if not cands:
        log("No System Reserved candidate in list volume (EN/FR)", "WARN")
        return None
    # Prefer already-lettered, then smallest
    cands.sort(key=lambda v: (0 if v.letter else 1, v.size_mb or 9999, v.index))
    for v in cands:
        if v.letter:
            root = f"{v.letter}:"
            if Path(root + "\\").exists():
                log(f"System Reserved already at {root}", "OK")
                return root
        letter = _free_letter()
        if not letter:
            log("No free letter for System Reserved", "ERROR")
            return None
        if assign_letter_to_volume(v.index, letter):
            log(f"System Reserved volume {v.index} assigned {letter}:", "OK")
            return f"{letter}:"
        log(f"Could not assign letter to volume {v.index}", "WARN")
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
    """
    Safely hide a temporary ESP/SRP letter.
    Order matters: resolve volume WHILE letter exists, remove via volume index,
    then mountvol /d. Never `select volume L` after the letter is already gone
    (causes FR: Aucun volume n'a été sélectionné).
    """
    L = letter_root.rstrip("\\").rstrip(":").upper()[:1]
    if not L:
        return

    vol = find_volume_by_letter(L)
    if vol is not None:
        # Only strip letter from clear system/EFI/boot volumes
        raw = vol.raw
        is_sys = bool(
            re.search(
                r"EFI|ESP|System|Syst[eè]me|Reserved|R[eé]serv|Hidden|Cach[eé]|Boot|FAT32",
                raw,
                re.I,
            )
        )
        if is_sys or (vol.fs or "").upper() in ("FAT32", "FAT"):
            if remove_letter_from_volume(vol.index, L):
                log(f"Removed letter {L}: from volume {vol.index}", "INFO")
        else:
            log(f"Skip diskpart remove letter {L}: (not clearly system/EFI volume)", "INFO")
    else:
        log(f"Letter {L}: already absent from list volume — mountvol cleanup only", "INFO")

    # mountvol /d for mountvol-/s mounts and leftovers (ignore errors)
    _run(["mountvol", f"{L}:", "/d"])


def create_larger_esp(
    size_mb: int = TARGET_ESP_MB,
    *,
    system_disk: int | None = None,
) -> str | None:
    """
    Shrink C: and create a NEW EFI system partition (GPT) of size_mb.
    Then bcdboot Windows onto it. Does not wipe user data on C:.
    Refuses to proceed if system disk cannot be verified (no silent disk 0).
    """
    log(f"Creating new {size_mb} MB EFI System Partition (shrink C:, no data wipe)...", "STEP")
    letter = _free_letter()
    if not letter:
        return None

    disk_n = system_disk if system_disk is not None and system_disk >= 0 else get_system_disk_number("C")
    if disk_n is None:
        log("Abort ESP expand: system disk # unknown (safety)", "ERROR")
        return None

    if not shrink_volume_letter("C", size_mb, max(300, size_mb - 50)):
        log("C: shrink failed — abort ESP expand", "ERROR")
        return None

    # Re-verify disk still matches C:
    verified = get_system_disk_number("C")
    if verified is not None and verified != disk_n:
        log(f"Abort ESP expand: disk mismatch C:→{verified} vs expected {disk_n}", "ERROR")
        return None

    ok_sel, sel_out = ensure_select_disk(int(disk_n))
    if not ok_sel:
        log(f"Abort ESP expand: cannot select disk {disk_n}: {sel_out[-160:]}", "ERROR")
        return None

    script = (
        f"select disk {int(disk_n)}\n"
        f"detail disk\n"
        f"create partition efi size={int(size_mb)}\n"
        "format fs=fat32 quick label=ESP\n"
        f"assign letter={letter}\n"
        "detail partition\n"
        "exit\n"
    )
    ok, create_out = run_diskpart(script)
    log(create_out[-500:] if create_out else "create done")
    if not ok:
        log(f"EFI create failed: {create_out[-300:]}", "ERROR")
        return None
    if create_out and re.search(r"error|failed|denied|échec|echec|erreur", create_out, re.I):
        if not re.search(r"successfully|r[eé]ussi|termin[eé]", create_out, re.I):
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
    if code != 0 and "successfully" not in bout.lower() and "reussi" not in bout.lower().replace("é", "e"):
        code2, bout2 = _run([str(bcdboot), sys_root, "/s", root, "/f", "ALL"], timeout=180)
        log(f"bcdboot ALL -> {code2}: {bout2[:300]}")
        if code2 != 0:
            log("bcdboot failed on new ESP - old ESP still present, boot should remain OK", "WARN")
            # Do NOT report success — caller must not mark expand ok
            return None

    log(f"New ESP ready at {root} with boot files", "OK")
    return root


def create_larger_system_reserved_mbr(
    size_mb: int = TARGET_ESP_MB,
    *,
    system_disk: int | None = None,
) -> str | None:
    """
    BIOS/MBR: shrink C and create a new primary NTFS system partition,
    then bcdboot /f BIOS. Old System Reserved left intact as fallback.
    Does NOT set `active` until bcdboot succeeds (avoids stealing boot flag on failure).
    """
    log(f"Creating new {size_mb} MB System partition (MBR/BIOS path)...", "STEP")
    letter = _free_letter()
    if not letter:
        return None

    disk_n = system_disk if system_disk is not None and system_disk >= 0 else get_system_disk_number("C")
    if disk_n is None:
        log("Abort MBR system expand: system disk # unknown (safety)", "ERROR")
        return None

    if not shrink_volume_letter("C", size_mb, max(300, size_mb - 50)):
        log("C: shrink failed — abort MBR system expand", "ERROR")
        return None

    verified = get_system_disk_number("C")
    if verified is not None and verified != disk_n:
        log(f"Abort MBR expand: disk mismatch C:→{verified} vs expected {disk_n}", "ERROR")
        return None

    ok_sel, sel_out = ensure_select_disk(int(disk_n))
    if not ok_sel:
        log(f"Abort MBR expand: cannot select disk {disk_n}: {sel_out[-160:]}", "ERROR")
        return None

    ok, out = run_diskpart(
        f"select disk {int(disk_n)}\n"
        f"detail disk\n"
        f"create partition primary size={int(size_mb)}\n"
        "format fs=ntfs quick label=System\n"
        f"assign letter={letter}\n"
        "exit\n"
    )
    log(out[-400:] if out else "")
    if not ok:
        log("MBR system partition create failed", "ERROR")
        return None
    root = f"{letter}:"
    if not Path(root + "\\").exists():
        return None

    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    code, bout = _run([str(bcdboot), sys_root, "/s", root, "/f", "BIOS"])
    log(f"bcdboot BIOS -> {code}: {bout[:300]}")
    if code != 0 and "successfully" not in bout.lower():
        log("bcdboot BIOS failed — leaving new partition without active flag (old SRP intact)", "WARN")
        return None

    # Mark active only after bcdboot OK — select by volume index (never letter-after-dismount)
    vol = find_volume_by_letter(letter)
    if vol is None:
        log(f"Cannot resolve volume for {letter}: to mark active", "WARN")
        return root
    ok_sel_v, _ = ensure_select_volume(vol.index)
    if not ok_sel_v:
        log(f"Cannot select volume {vol.index} for active", "WARN")
        return root
    ok_act, act_out = run_diskpart(
        f"select volume {int(vol.index)}\n"
        f"detail volume\n"
        f"active\n"
        f"exit\n"
    )
    if ok_act and not re.search(
        r"No volume selected|Aucun volume|error|failed|échec|echec|erreur",
        act_out or "",
        re.I,
    ):
        log(f"Marked volume {vol.index} ({letter}:) active after successful bcdboot", "OK")
    else:
        log(f"Could not mark active (non-fatal): {(act_out or '')[-160:]}", "WARN")
    return root


def inspect_and_fix_system_reserved(
    force_expand: bool = False,
    *,
    system_disk: int | None = None,
) -> dict:
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
        "system_disk": system_disk,
        "preflight": None,
        "postflight": None,
        "fallback": None,
    }

    # Resolve / confirm system disk early
    disk_n = system_disk if system_disk is not None and int(system_disk) >= 0 else get_system_disk_number()
    result["system_disk"] = disk_n
    if disk_n is None:
        log("System disk # unresolved — cleanup-only mode (no expand)", "WARN")
        result["actions"].append("disk_unknown_no_expand")

    # Secure preflight (BCD backup + BitLocker + free space)
    snap = None
    _prepare_fallback = None
    _postflight = None
    try:
        from .boot_safe import (
            prepare_partition_fallbacks,
            preflight_boot_edit,
            postflight_boot_edit,
        )

        _prepare_fallback = prepare_partition_fallbacks
        _postflight = postflight_boot_edit
        snap = preflight_boot_edit(
            intend="esp-or-mbr",
            system_disk=disk_n,
            require_disk=False,  # cleanup still allowed without disk#
        )
        result["preflight"] = snap.as_dict()
        if disk_n is None and snap.disk_number is not None:
            disk_n = snap.disk_number
            result["system_disk"] = disk_n
    except Exception as e:
        log(f"boot preflight skipped: {e}", "WARN")

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
            if not mounted:
                # Fallback: diskpart FAT32 ESP candidate
                for v in find_esp_candidates():
                    letter = v.letter or _free_letter()
                    if not letter:
                        break
                    if v.letter:
                        mounted = f"{v.letter}:"
                        break
                    if assign_letter_to_volume(v.index, letter):
                        mounted = f"{letter}:"
                        log(f"ESP via diskpart volume {v.index} → {letter}:", "OK")
                        break
        else:
            result["mode"] = "SystemReserved"
            mounted = find_system_reserved_letter()
            if not mounted:
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

        space_tight = info["free_mb"] < MIN_FREE_MB or info["total_mb"] < MIN_SIZE_MB_COMFORTABLE
        need_expand = space_tight or (force_expand and not prior_expanded and space_tight)
        if force_expand and not prior_expanded and not space_tight:
            log("Historical SRP error in logs but ESP space OK after cleanup — skip expand", "OK")
            need_expand = False
        if prior_expanded and not space_tight:
            need_expand = False
        if need_expand and disk_n is None:
            log("Need expand but system disk unknown — refuse (safety)", "ERROR")
            need_expand = False
            result["actions"].append("expand_refused_unknown_disk")
            result["ok"] = False

        # Hard-block expand when preflight says unsafe
        if need_expand and snap is not None and not snap.safe_to_mutate:
            # Cleanup-only blocks that apply to expand
            hard = {"system_disk_unknown", "bitlocker_locked", "c_free_lt_2gb", "bcd_backup_required"}
            if hard.intersection(snap.block_reasons):
                log(
                    "Expand refused by secure preflight: " + ",".join(snap.block_reasons),
                    "ERROR",
                )
                need_expand = False
                result["actions"].append("expand_refused_preflight")
                result["ok"] = False

        if need_expand:
            log(
                f"Partition still tight (free={info['free_mb']:.1f} MB, size={info['total_mb']:.1f} MB) "
                f"- smart Partition-Magic planner then legacy expand on disk #{disk_n}...",
                "WARN",
            )
            unmount_letter(mounted)
            mounted = None

            # 0) Intelligent move/grow/shrink planner (extend in-place, smart shrink, GRUB-aware)
            try:
                from .partition_smart import run_smart_partition_magic

                smart = run_smart_partition_magic(
                    system_disk=disk_n,
                    prefer_uefi=(result.get("mode") == "EFI" or uefi),
                    target_mb=TARGET_ESP_MB,
                )
                result["smart_partition"] = {
                    k: smart.get(k)
                    for k in ("ok", "plan", "actions", "finish", "fallback", "gparted_plan")
                }
                result["actions"].extend(smart.get("actions") or [])
                strat = ((smart.get("plan") or {}).get("strategy") or "")
                mutated = strat in (
                    "extend_boot",
                    "shrink_c_then_create",
                    "shrink_data_then_create",
                    "create_in_free",
                )
                if smart.get("ok") and mutated:
                    result["expanded"] = True
                    result["ok"] = True
                    result["actions"].append("expand_via_smart_partition")
                    log(f"Smart partition planner succeeded ({strat})", "OK")
                elif strat == "gparted_move":
                    result["actions"].append("smart_needs_gparted_move")
                    if smart.get("fallback") or smart.get("fallback_media"):
                        result["fallback"] = smart.get("fallback_media") or smart.get("fallback")
            except Exception as e:
                log(f"Smart partition planner failed: {e}", "WARN")
                result["actions"].append(f"smart_partition_fail:{type(e).__name__}")

            if not result.get("ok"):
                if result["mode"] == "EFI" or uefi:
                    new_root = create_larger_esp(TARGET_ESP_MB, system_disk=disk_n)
                    if not new_root:
                        log("EFI create failed - trying primary system partition fallback", "WARN")
                        new_root = create_larger_system_reserved_mbr(TARGET_ESP_MB, system_disk=disk_n)
                else:
                    new_root = create_larger_system_reserved_mbr(TARGET_ESP_MB, system_disk=disk_n)
                    if not new_root:
                        log("MBR system create failed - trying EFI create fallback", "WARN")
                        new_root = create_larger_esp(TARGET_ESP_MB, system_disk=disk_n)

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
                    log("Expand failed — trying PowerShell Storage (non-diskpart) then GParted rescue", "ERROR")
                    result["ok"] = False
                    result["actions"].append("expand_failed")
                    # Non-diskpart fallback before GParted
                    try:
                        from .boot_emergency import ps_storage_create_esp

                        ps = ps_storage_create_esp(
                            system_disk=disk_n,
                            prefer_uefi=(result.get("mode") == "EFI" or uefi),
                        )
                        result["ps_storage"] = {k: ps.get(k) for k in ("ok", "letter", "actions")}
                        result["actions"].extend(ps.get("actions") or [])
                        if ps.get("ok"):
                            result["expanded"] = True
                            result["ok"] = True
                            result["actions"].append("expand_via_ps_storage")
                            log("ESP/SRP expanded via PowerShell Storage (diskpart bypass)", "OK")
                    except Exception as e:
                        log(f"PS Storage expand fallback failed: {e}", "WARN")
                        result["actions"].append(f"ps_storage_fail:{type(e).__name__}")

                    if not result.get("ok"):
                        try:
                            # Dynamic regenerate from last partition backup before GParted
                            from .boot_partition_backup import dynamic_regenerate_boot_partition

                            regen = dynamic_regenerate_boot_partition(
                                prefer_uefi=(result.get("mode") == "EFI" or uefi),
                                system_disk=disk_n,
                            )
                            result["dynamic_regenerate"] = {
                                k: regen.get(k) for k in ("ok", "mode", "letter", "actions")
                            }
                            result["actions"].extend(regen.get("actions") or [])
                            if regen.get("ok"):
                                result["ok"] = True
                                result["expanded"] = True
                                result["actions"].append("expand_via_dynamic_regenerate")
                                log("Boot partition dynamically regenerated from backup", "OK")
                        except Exception as e:
                            log(f"Dynamic regenerate failed: {e}", "WARN")
                            result["actions"].append(f"dynamic_regen_fail:{type(e).__name__}")

                    if not result.get("ok") and _prepare_fallback:
                        try:
                            result["fallback"] = _prepare_fallback(
                                reason="diskpart_and_ps_storage_esp_mbr_expand_failed",
                                system_disk=disk_n,
                                mode=str(result.get("mode") or ("EFI" if uefi else "SystemReserved")),
                            )
                            result["actions"].append("gparted_rescue_staged")
                        except Exception as e:
                            log(f"GParted rescue staging failed: {e}", "WARN")
        else:
            log(f"ESP/SRP has enough free space ({info['free_mb']:.1f} MB) after cleanup", "OK")
            result["ok"] = True
            if prior_expanded:
                result["expanded"] = True

        # Postflight when we mutated or finished successfully
        if _postflight and (result.get("expanded") or result.get("ok")):
            try:
                result["postflight"] = _postflight(
                    expect_uefi=(result.get("mode") == "EFI" or uefi),
                    system_disk=disk_n,
                )
                if result.get("expanded") and result["postflight"] and not result["postflight"].get("ok"):
                    log("Postflight failed after expand — marking not OK (BCD backup kept)", "WARN")
                    result["actions"].append("postflight_fail")
                    result["ok"] = False
            except Exception as e:
                log(f"boot postflight skipped: {e}", "WARN")

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
