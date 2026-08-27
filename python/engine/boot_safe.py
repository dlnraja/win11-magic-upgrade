"""
Secure, validated MBR / EFI boot-layout edits.

Sensitive path — fail closed:
  1) Preflight (disk identity, BitLocker, firmware, free space on C:)
  2) BCD export backup before mutate
  3) Native repair tiers (cleanup → bcdboot → diskpart expand)
  4) Postflight + deep verification scorecard
  5) Intelligent fallbacks: BCD heal, bootrec, PowerShell Storage (non-diskpart),
     emergency BCD regenerate, temporary WinRE/WinPE ramdisk, FreeDOS + GParted stage

GParted / FreeDOS are NEVER auto-booted / auto-executed against disks. We only stage
official media + human instructions when native expand fails.
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


_CRITICAL_REL_PATHS = (
    Path("EFI") / "Microsoft" / "Boot" / "bootmgfw.efi",
    Path("EFI") / "Microsoft" / "Boot" / "BCD",
    Path("EFI") / "Microsoft" / "Boot" / "bootmgr.efi",
    Path("EFI") / "Boot" / "bootx64.efi",
    Path("EFI") / "Boot" / "bootia32.efi",
    Path("bootmgr"),
    Path("Boot") / "BCD",
)


def snapshot_esp_critical_files() -> Path | None:
    """
    Copy critical boot files from the current ESP/SRP to a local snapshot
    (no PII — binary boot artifacts only). Used to restore if an edit fails.
    """
    from .sysreserved import mount_esp, unmount_letter

    out_root = STATE_DIR / "boot-snapshots"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = out_root / stamp
    dest.mkdir(parents=True, exist_ok=True)
    mounted = None
    copied = 0
    try:
        mounted = mount_esp()
        if not mounted:
            log("ESP snapshot skipped — cannot mount", "WARN")
            return None
        src_root = Path(f"{mounted.rstrip(':\\')}:\\")
        for rel in _CRITICAL_REL_PATHS:
            src = src_root / rel
            if not src.is_file():
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, target)
                copied += 1
            except Exception as e:
                log(f"snapshot skip {rel}: {e}", "INFO")
        meta = {
            "utc": stamp,
            "copied": copied,
            "firmware": _firmware(),
            "files": [str(p).replace("\\", "/") for p in _CRITICAL_REL_PATHS if (dest / p).is_file()],
        }
        (dest / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        (out_root / "LAST.txt").write_text(str(dest), encoding="utf-8")
        log(f"ESP/SRP critical snapshot: {copied} files → {dest}", "OK")
        return dest if copied else None
    finally:
        if mounted:
            try:
                unmount_letter(mounted)
            except Exception:
                pass


def restore_esp_critical_files() -> bool:
    """Restore last ESP file snapshot onto the currently mountable ESP."""
    from .sysreserved import mount_esp, unmount_letter

    last = STATE_DIR / "boot-snapshots" / "LAST.txt"
    if not last.exists():
        return False
    src_root = Path(last.read_text(encoding="utf-8").strip())
    if not src_root.is_dir():
        return False
    mounted = None
    restored = 0
    try:
        mounted = mount_esp()
        if not mounted:
            log("Cannot mount ESP to restore snapshot", "ERROR")
            return False
        dst_root = Path(f"{mounted.rstrip(':\\')}:\\")
        for rel in _CRITICAL_REL_PATHS:
            src = src_root / rel
            if not src.is_file():
                continue
            dst = dst_root / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                restored += 1
            except Exception as e:
                log(f"restore {rel}: {e}", "WARN")
        log(f"Restored {restored} critical boot files onto ESP", "OK" if restored else "WARN")
        return restored > 0
    finally:
        if mounted:
            try:
                unmount_letter(mounted)
            except Exception:
                pass


def rewrite_boot_files_from_windows(*, prefer_uefi: bool | None = None) -> bool:
    """Last-resort: bcdboot from running Windows tree onto current ESP (keeps PC bootable)."""
    from .mbrgpt import repair_boot_manager

    uefi = prefer_uefi if prefer_uefi is not None else (_firmware() == "UEFI")
    ok = repair_boot_manager(prefer_uefi=uefi)
    if ok:
        log("bcdboot rewrite from Windows — boot path re-anchored", "OK")
    else:
        log("bcdboot rewrite failed", "ERROR")
    return ok


def _bcdedit(*args: str, timeout: int = 90) -> tuple[int, str]:
    return _run(["bcdedit", *args], timeout=timeout)


def heal_bcd_store(*, prefer_uefi: bool | None = None) -> list[str]:
    """
    Intelligent bcdedit repairs that avoid leaving a broken {default}/{current}.
    Never deletes the Windows boot entry; only heals paths/devices and recovery flags.
    """
    actions: list[str] = []
    uefi = prefer_uefi if prefer_uefi is not None else (_firmware() == "UEFI")
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    drive = os.environ.get("SystemDrive", "C:")
    winload = r"\Windows\system32\winload.efi" if uefi else r"\Windows\system32\winload.exe"

    log("BCD heal — bcdedit store repair...", "STEP")

    # Export a fresh backup before mutating BCD further
    if backup_bcd():
        actions.append("bcd_backup_before_heal")

    # Ensure bootmgr is readable
    code, out = _bcdedit("/enum", "{bootmgr}")
    if code != 0 or not out or "cannot find" in out.lower():
        # Recreate via bcdboot is preferred; soft nudge here
        actions.append("bootmgr_enum_failed")
    else:
        actions.append("bootmgr_ok")

    # Soft settings that reduce "failed boot → recovery loop" noise
    for args, tag in (
        (("/set", "{bootmgr}", "timeout", "10"), "timeout10"),
        (("/set", "{bootmgr}", "displaybootmenu", "Yes"), "bootmenu"),
        (("/set", "{current}", "bootstatuspolicy", "IgnoreAllFailures"), "ignore_boot_failures"),
        (("/set", "{current}", "recoveryenabled", "Yes"), "recovery_enabled"),
        (("/set", "{default}", "bootstatuspolicy", "IgnoreAllFailures"), "default_ignore_fail"),
    ):
        c, o = _bcdedit(*args)
        if c == 0:
            actions.append(tag)
        else:
            # Non-fatal — entry may not exist yet
            log(f"bcdedit {' '.join(args)} skipped: {(o or '')[:120]}", "INFO")

    # Point OS loader device at SystemDrive when broken (common after ESP swap)
    # device partition=C:  /  osdevice partition=C:  /  path winload
    for ident in ("{current}", "{default}"):
        for name, value in (
            ("device", f"partition={drive}"),
            ("osdevice", f"partition={drive}"),
            ("path", winload),
            ("systemroot", r"\Windows"),
        ):
            c, o = _bcdedit("/set", ident, name, value)
            if c == 0:
                actions.append(f"set_{ident.strip('{}')}_{name}")
            else:
                # Avoid spamming — one note per ident
                if name == "device":
                    log(f"bcdedit set {ident} {name}: {(o or '')[:100]}", "INFO")

    # Describe for logs (sanitized later)
    c2, enum_out = _bcdedit("/enum", "{current}")
    if c2 == 0 and enum_out:
        actions.append("enum_current_ok")
        try:
            (STATE_DIR / "bcd-enum-current.txt").write_text(
                enum_out[:4000], encoding="utf-8", errors="replace"
            )
        except Exception:
            pass

    log(f"BCD heal actions: {', '.join(actions[-12:])}", "OK" if actions else "WARN")
    return actions


def try_bootrec_repair() -> list[str]:
    """
    bootrec.exe is normally WinRE-only; try if present (some full OS images ship it).
    Safe flags only — no disk wipe.
    """
    actions: list[str] = []
    bootrec = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "bootrec.exe"
    if not bootrec.exists():
        # Also check SysWOW / Repair folder
        alt = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Repair" / "bootrec.exe"
        if alt.exists():
            bootrec = alt
        else:
            return actions

    log("bootrec available — attempting non-destructive repair...", "STEP")
    for flag in ("/fixboot", "/rebuildbcd"):
        # /fixmbr only on MBR disks — skip by default to avoid GPT harm
        code, out = _run([str(bootrec), flag], timeout=180)
        actions.append(f"bootrec{flag}:{code}")
        log(f"bootrec {flag} -> {code}: {(out or '')[:200]}")
        if code != 0 and "Access is denied" in (out or ""):
            break
    return actions


def try_bootrec_fixmbr_if_bios() -> list[str]:
    """Only run /fixmbr on BIOS/MBR firmware — never on pure UEFI/GPT blindly."""
    if _firmware() == "UEFI":
        return []
    bootrec = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "bootrec.exe"
    if not bootrec.exists():
        return []
    code, out = _run([str(bootrec), "/fixmbr"], timeout=120)
    log(f"bootrec /fixmbr -> {code}: {(out or '')[:160]}")
    return [f"bootrec_fixmbr:{code}"]


def ensure_winre_enabled() -> dict[str, Any]:
    """Enable Windows RE (ramdisk Winre.wim) — the safe 'lite Windows' recovery environment."""
    info: dict[str, Any] = {"enabled": False, "location": None, "actions": []}
    reagentc = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "reagentc.exe"
    if not reagentc.exists():
        info["actions"].append("reagentc_missing")
        return info

    code, out = _run([str(reagentc), "/info"], timeout=90)
    info["info_tail"] = (out or "")[-500:]
    if re.search(r"Windows RE status:\s*Enabled", out or "", re.I):
        info["enabled"] = True
        info["actions"].append("winre_already_enabled")
    else:
        c2, o2 = _run([str(reagentc), "/enable"], timeout=180)
        info["actions"].append(f"reagentc_enable:{c2}")
        log(f"reagentc /enable -> {c2}: {(o2 or '')[:200]}")
        _, out3 = _run([str(reagentc), "/info"], timeout=90)
        info["enabled"] = bool(re.search(r"Windows RE status:\s*Enabled", out3 or "", re.I))
        info["info_tail"] = (out3 or "")[-500:]

    m = re.search(r"Windows RE location:\s*(.+)", info.get("info_tail") or out or "", re.I)
    if m:
        info["location"] = m.group(1).strip()[:260]
    return info


def stage_temporary_winre_ramdisk_boot(*, one_shot: bool = True) -> dict[str, Any]:
    """
    Stage a TEMPORARY boot into Windows RE (Winre.wim ramdisk) — lite repair OS.
    Does NOT replace the normal Windows boot entry.
    - one_shot=True → reagentc /boottore (next reboot enters WinRE once, then back to Windows)
    - Never auto-reboots here; caller decides after guarantee_bootable.
    """
    result: dict[str, Any] = {"staged": False, "mode": None, "actions": []}
    allow = os.environ.get("MAGIC_WINRE_FALLBACK", "1").strip().lower()
    if allow in ("0", "false", "no"):
        result["actions"].append("winre_fallback_disabled")
        return result

    winre = ensure_winre_enabled()
    result["winre"] = {k: winre.get(k) for k in ("enabled", "location", "actions")}
    result["actions"].extend(winre.get("actions") or [])

    if not winre.get("enabled"):
        # Locate Winre.wim and try setreimage
        candidates = [
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Recovery" / "Winre.wim",
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Recovery" / "Winre.wim",
        ]
        reagentc = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "reagentc.exe"
        for wim in candidates:
            if wim.is_file() and reagentc.exists():
                # /setreimage /path <dir> — directory containing Winre.wim
                c, o = _run([str(reagentc), "/setreimage", "/path", str(wim.parent)], timeout=120)
                result["actions"].append(f"setreimage:{c}")
                log(f"reagentc /setreimage {wim.parent} -> {c}: {(o or '')[:160]}")
                c2, _ = _run([str(reagentc), "/enable"], timeout=180)
                result["actions"].append(f"reagentc_enable_after_set:{c2}")
                winre = ensure_winre_enabled()
                result["winre"] = {k: winre.get(k) for k in ("enabled", "location", "actions")}
                break

    if not winre.get("enabled"):
        result["actions"].append("winre_unavailable")
        return result

    reagentc = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "reagentc.exe"
    if one_shot and reagentc.exists():
        # Next reboot → WinRE once (ramdisk), then normal Windows — ideal temporary fix env
        c, o = _run([str(reagentc), "/boottore"], timeout=60)
        result["actions"].append(f"boottore:{c}")
        if c == 0:
            result["staged"] = True
            result["mode"] = "reagentc_boottore_oneshot"
            log("Temporary WinRE ramdisk boot staged (one-shot via reagentc /boottore)", "OK")
        else:
            log(f"reagentc /boottore failed: {(o or '')[:200]}", "WARN")

    # Also ensure recovery sequence is linked (multi-fallback)
    _bcdedit("/set", "{current}", "recoveryenabled", "Yes")
    result["actions"].append("recoveryenabled_yes")

    # Write operator guide
    try:
        guide = STATE_DIR / "rescue" / "WinRE-Temporary-Boot.txt"
        guide.parent.mkdir(parents=True, exist_ok=True)
        guide.write_text(
            "\n".join(
                [
                    "Win11 Magic Upgrade — temporary WinRE (ramdisk) repair boot",
                    "=" * 56,
                    f"Staged: {result.get('staged')} mode={result.get('mode')}",
                    f"WinRE enabled: {winre.get('enabled')} loc={winre.get('location')}",
                    "",
                    "What this does:",
                    "- Uses Microsoft Windows Recovery Environment (Winre.wim) in RAM",
                    "- One-shot: next reboot enters WinRE, then returns to normal Windows",
                    "- Does NOT wipe disks; does NOT replace your Windows boot entry",
                    "",
                    "In WinRE you can run:",
                    "  Startup Repair | bcdboot C:\\Windows /s S: /f UEFI | diskpart | bootrec",
                    "",
                    "Disable one-shot staging: MAGIC_WINRE_FALLBACK=0",
                    "Force staging even when boot looks OK: MAGIC_WINRE_BOOT=1",
                ]
            ),
            encoding="utf-8",
        )
        result["guide"] = str(guide)
    except Exception:
        pass
    return result


def intelligent_boot_repair(*, prefer_uefi: bool | None = None, system_disk: int | None = None) -> dict[str, Any]:
    """
    Full intelligent fallback ladder after ESP/MBR errors:
      BCD import → ESP snapshot → bcdboot → bcdedit heal → bootrec →
      deep verify → emergency regenerate → PS Storage (non-diskpart) →
      WinRE / WinPE ramdisk → FreeDOS media stage
    """
    uefi = prefer_uefi if prefer_uefi is not None else (_firmware() == "UEFI")
    log("=" * 60, "STEP")
    log("INTELLIGENT BOOT REPAIR — multi-tool fallbacks", "STEP")
    summary: dict[str, Any] = {"actions": [], "uefi": uefi, "system_disk": system_disk}

    if restore_bcd_from_last():
        summary["actions"].append("bcd_import")
    if restore_esp_critical_files():
        summary["actions"].append("esp_files_restored")
    if rewrite_boot_files_from_windows(prefer_uefi=uefi):
        summary["actions"].append("bcdboot_rewrite")

    # Extra bcdboot ALL pass
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    if bcdboot.exists():
        for mode in (("ALL", "UEFI", "BIOS") if uefi else ("ALL", "BIOS", "UEFI")):
            code, out = _run([str(bcdboot), sys_root, "/f", mode], timeout=180)
            summary["actions"].append(f"bcdboot_{mode}:{code}")
            if code == 0 or "successfully" in (out or "").lower():
                break

    summary["actions"].extend(heal_bcd_store(prefer_uefi=uefi))
    summary["actions"].extend(try_bootrec_repair())
    summary["actions"].extend(try_bootrec_fixmbr_if_bios())

    # Deep checks + emergency suite (non-diskpart, PE/DOS) when still uncertain
    try:
        from .boot_emergency import (
            deep_boot_verification,
            run_emergency_repair_suite,
            stage_temporary_winpe_ramdisk,
        )

        deep = deep_boot_verification(prefer_uefi=uefi)
        summary["deep_verify"] = {
            "ok": deep.get("ok"),
            "score": deep.get("score"),
            "max_score": deep.get("max_score"),
            "issues": deep.get("issues"),
        }
        summary["actions"].append(f"deep_verify:{deep.get('score')}/{deep.get('max_score')}")

        if not deep.get("ok"):
            emerg = run_emergency_repair_suite(
                prefer_uefi=uefi,
                system_disk=system_disk,
                try_ps_storage_expand=True,
            )
            summary["emergency"] = {
                k: emerg.get(k)
                for k in ("bootable", "safe_reboot_path", "winre", "winpe", "freedos", "ps_storage")
            }
            summary["actions"].extend(emerg.get("actions") or [])
            winre_stage = emerg.get("winre") or {}
            # Normalize winre_stage shape for guarantee_bootable
            if isinstance(winre_stage, dict) and "staged" in winre_stage:
                summary["winre_stage"] = {
                    "staged": winre_stage.get("staged"),
                    "mode": winre_stage.get("mode"),
                    "actions": [],
                }
            summary["winpe_stage"] = emerg.get("winpe") or {}
        else:
            # Still stage WinRE/WinPE as soft safety net when MAGIC_WINRE_BOOT / MAGIC_WINPE_BOOT
            winre_stage = stage_temporary_winre_ramdisk_boot(one_shot=True)
            summary["winre_stage"] = winre_stage
            summary["actions"].extend(winre_stage.get("actions") or [])
            force_pe = os.environ.get("MAGIC_WINPE_BOOT", "").strip().lower() in ("1", "true", "yes")
            if force_pe or os.environ.get("MAGIC_WINRE_BOOT", "").strip().lower() in ("1", "true", "yes"):
                pe = stage_temporary_winpe_ramdisk(one_shot=True, prefer_uefi=uefi)
                summary["winpe_stage"] = pe
                summary["actions"].extend(pe.get("actions") or [])
    except Exception as e:
        log(f"Emergency suite skipped: {e}", "WARN")
        summary["actions"].append(f"emergency_skip:{type(e).__name__}")
        winre_stage = stage_temporary_winre_ramdisk_boot(one_shot=True)
        summary["winre_stage"] = winre_stage
        summary["actions"].extend(winre_stage.get("actions") or [])

    if "winre_stage" not in summary:
        winre_stage = stage_temporary_winre_ramdisk_boot(one_shot=True)
        summary["winre_stage"] = winre_stage
        summary["actions"].extend(winre_stage.get("actions") or [])
    else:
        winre_stage = summary.get("winre_stage") or {}

    post = postflight_boot_edit(expect_uefi=uefi, system_disk=system_disk)
    summary["postflight"] = post
    summary["bootable"] = bool(post.get("ok") or post.get("bcd_bootmgr"))
    pe_staged = bool((summary.get("winpe_stage") or {}).get("staged"))
    # If still flaky but WinRE/WinPE staged, mark recoverable
    if not summary["bootable"] and (winre_stage.get("staged") or pe_staged):
        summary["recoverable_via_winre"] = bool(winre_stage.get("staged"))
        summary["recoverable_via_winpe"] = pe_staged
        log(
            "Windows boot uncertain — temporary WinRE/WinPE ramdisk staged for next reboot",
            "WARN",
        )
    elif summary["bootable"]:
        log("Intelligent repair: boot verified OK", "OK")
    return summary


def guarantee_bootable(
    *,
    expect_uefi: bool | None = None,
    system_disk: int | None = None,
    force_restore: bool = False,
) -> dict[str, Any]:
    """
    ALWAYS leave the machine able to reboot into Windows (or one-shot WinRE/WinPE).
    Called after success OR failure of ESP/MBR edits.
    """
    log("=" * 60, "STEP")
    log("GUARANTEE BOOTABLE — restore if needed", "STEP")
    uefi = expect_uefi if expect_uefi is not None else (_firmware() == "UEFI")
    actions: list[str] = []
    post = postflight_boot_edit(expect_uefi=uefi, system_disk=system_disk)
    winre_stage: dict[str, Any] = {}
    winpe_stage: dict[str, Any] = {}
    deep_verify: dict[str, Any] = {}
    repair: dict[str, Any] = {}

    force_winre = os.environ.get("MAGIC_WINRE_BOOT", "").strip().lower() in ("1", "true", "yes")
    force_pe = os.environ.get("MAGIC_WINPE_BOOT", "").strip().lower() in ("1", "true", "yes")

    if post.get("ok") and not force_restore and not force_winre and not force_pe:
        actions.append("postflight_ok")
        out = {
            "bootable": True,
            "safe_reboot_path": True,
            "actions": actions,
            "postflight": post,
            "restored": False,
            "winre_stage": {},
            "winpe_stage": {},
            "deep_verify": {},
        }
        _write_bootable_status(out)
        return out

    # Progressive restore + intelligent multi-tool ladder
    if force_restore or not post.get("ok") or force_winre or force_pe:
        repair = intelligent_boot_repair(prefer_uefi=uefi, system_disk=system_disk)
        actions.extend(repair.get("actions") or [])
        post = repair.get("postflight") or postflight_boot_edit(expect_uefi=uefi, system_disk=system_disk)
        winre_stage = repair.get("winre_stage") or {}
        winpe_stage = repair.get("winpe_stage") or {}
        deep_verify = repair.get("deep_verify") or {}

    bootable = bool(post.get("ok") or post.get("bcd_bootmgr"))
    # One-shot WinRE / WinPE / emergency suite counts as safe temporary path
    emerg_safe = bool((repair.get("emergency") or {}).get("safe_reboot_path"))
    safe_reboot_path = bool(
        bootable
        or winre_stage.get("staged")
        or winpe_stage.get("staged")
        or emerg_safe
    )

    out = {
        "bootable": bootable,
        "safe_reboot_path": safe_reboot_path,
        "actions": actions,
        "postflight": post,
        "winre_stage": winre_stage,
        "winpe_stage": winpe_stage,
        "deep_verify": deep_verify,
        "restored": any(
            a.startswith(p)
            for a in actions
            for p in (
                "bcd_import",
                "esp_files",
                "bcdboot",
                "bcdedit",
                "bootrec",
                "reagentc",
                "boottore",
                "set_",
                "deep_verify",
                "ps_create",
                "create_osloader",
                "bootsequence",
                "removed_tiny",
            )
        )
        or bool(actions),
    }
    if bootable:
        log("PC is bootable (verified) — safe to reboot", "OK")
    elif winre_stage.get("staged") or winpe_stage.get("staged"):
        log(
            "Windows uncertain — next reboot enters temporary WinRE/WinPE ramdisk (then back to Windows)",
            "WARN",
        )
    else:
        log(
            "CRITICAL: could not verify bootability — do NOT force reboot without WinRE/GParted/FreeDOS media",
            "ERROR",
        )
    _write_bootable_status(out)
    return out


def _write_bootable_status(data: dict[str, Any]) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "bootable-status.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def safe_reboot_after_boot_op(
    *,
    success: bool,
    reason: str,
    seconds: int = 50,
    system_disk: int | None = None,
    expect_uefi: bool | None = None,
) -> dict[str, Any]:
    """
    Reboot only if guarantee_bootable says OK (Windows bootable OR one-shot WinRE/WinPE staged).
    Success or failure of the partition op must not brick the PC.
    """
    from .autonomy import schedule_reboot

    g = guarantee_bootable(expect_uefi=expect_uefi, system_disk=system_disk, force_restore=not success)
    result = {"scheduled": False, "bootable": g.get("bootable"), "guarantee": g, "success_op": success}
    ok_path = bool(g.get("bootable") or g.get("safe_reboot_path"))
    if not ok_path:
        log("Reboot SKIPPED — no verified Windows boot and no WinRE/WinPE one-shot staged", "ERROR")
        return result
    if g.get("bootable"):
        tag = "after-boot-op-ok" if success else "after-boot-op-failed-but-restored"
    elif (g.get("winpe_stage") or {}).get("staged"):
        tag = "after-boot-op-winpe-oneshot"
        log("Scheduling reboot into temporary WinPE ramdisk (one-shot), then Windows", "WARN")
    else:
        tag = "after-boot-op-winre-oneshot"
        log("Scheduling reboot into temporary WinRE ramdisk (one-shot), then Windows", "WARN")
    schedule_reboot(seconds=seconds, reason=f"{reason} [{tag}]"[:500])
    result["scheduled"] = True
    return result


def report_boot_failure_autodiag(
    *,
    kind: str,
    message: str,
    op_result: dict[str, Any] | None = None,
    guarantee: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    """
    File sanitized GitHub Issue (+ optional PR) with boot recovery facts only.
    Never includes usernames, hostnames, full paths under Users, etc.
    """
    from .gh_report import report_failure_to_github
    from .sanitize import sanitize_obj

    extra: dict[str, Any] = {
        "bootable_status": sanitize_obj((guarantee or {}).get("bootable")),
        "restore_actions": sanitize_obj((guarantee or {}).get("actions")),
        "postflight_issues": sanitize_obj(
            ((guarantee or {}).get("postflight") or {}).get("issues")
            or ((op_result or {}).get("postflight") or {}).get("issues")
        ),
        "op_ok": sanitize_obj((op_result or {}).get("ok")),
        "op_actions": sanitize_obj((op_result or {}).get("actions")),
        "preflight_blocks": sanitize_obj(
            ((op_result or {}).get("preflight") or {}).get("block_reasons")
        ),
        "fallback_tools": sanitize_obj(((op_result or {}).get("fallback") or {}).get("tools")),
        "has_gparted_guide": bool(((op_result or {}).get("fallback") or {}).get("guide")),
        "has_bcd_backup": bool((STATE_DIR / "bcd-backups" / "LAST.txt").exists()),
        "has_esp_snapshot": bool((STATE_DIR / "boot-snapshots" / "LAST.txt").exists()),
        "winre_staged": bool((guarantee or {}).get("winre_stage", {}).get("staged")),
        "winpe_staged": bool((guarantee or {}).get("winpe_stage", {}).get("staged")),
        "deep_verify_score": sanitize_obj(((guarantee or {}).get("deep_verify") or {}).get("score")),
        "safe_reboot_path": sanitize_obj((guarantee or {}).get("safe_reboot_path")),
    }
    # Attach sanitized JSON snippets from state (already machine facts)
    for name in (
        "boot-preflight.json",
        "boot-postflight.json",
        "bootable-status.json",
        "boot-deep-verify.json",
        "boot-emergency.json",
        "srp-fix.json",
    ):
        p = STATE_DIR / name
        if not p.exists():
            continue
        try:
            extra[name.replace(".json", "").replace("-", "_")] = sanitize_obj(
                json.loads(p.read_text(encoding="utf-8"))
            )
        except Exception:
            pass

    return report_failure_to_github(
        kind=kind,
        message=message,
        report=report,
        srp_result=op_result if isinstance(op_result, dict) else None,
        extra=extra,
    )


def run_esp_srp_with_restore(
    *,
    force_expand: bool = False,
    system_disk: int | None = None,
    retries: int = 2,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Intelligent ESP/SRP fix with snapshot → try/refix → always restore bootability
    → sanitized GitHub report on persistent failure.
    """
    from .sysreserved import inspect_and_fix_system_reserved

    log("=== Secure ESP/SRP op (snapshot → fix → restore → report) ===", "STEP")
    snapshot_esp_critical_files()
    # Ensure BCD backup even if caller skipped preflight
    backup_bcd()

    last: dict[str, Any] = {"ok": False}
    attempts = max(1, int(retries))
    for i in range(attempts):
        force = force_expand or i > 0
        log(f"ESP/SRP attempt {i + 1}/{attempts} (force_expand={force})", "STEP")
        try:
            last = inspect_and_fix_system_reserved(force_expand=force, system_disk=system_disk)
        except Exception as e:
            last = {"ok": False, "actions": [f"exception:{type(e).__name__}"], "error": str(e)[:300]}
            log(f"ESP/SRP attempt error: {e}", "ERROR")
        if isinstance(last, dict) and last.get("ok"):
            break
        # Between retries: restore bootability then retry expand
        guarantee_bootable(system_disk=system_disk, force_restore=True)

    gu = guarantee_bootable(
        system_disk=system_disk if system_disk is not None else (last or {}).get("system_disk"),
        expect_uefi=((last or {}).get("mode") == "EFI") if last else None,
        force_restore=not bool((last or {}).get("ok")),
    )
    out = dict(last or {})
    out["bootable"] = gu.get("bootable")
    out["guarantee"] = gu
    out["attempts"] = attempts

    if not out.get("ok"):
        if not out.get("fallback"):
            try:
                out["fallback"] = prepare_partition_fallbacks(
                    reason="esp_srp_failed_after_retries",
                    system_disk=out.get("system_disk"),
                    mode=str(out.get("mode") or "unknown"),
                )
            except Exception:
                pass
        try:
            links = report_boot_failure_autodiag(
                kind="esp-srp-failed",
                message="ESP/SRP fix failed after retries — PC kept bootable via restore",
                op_result=out,
                guarantee=gu,
                report=report,
            )
            out["autodiag"] = links
        except Exception as e:
            log(f"autodiag after ESP fail: {e}", "WARN")

    return out


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

    # Critical ESP file snapshot for restore-on-failure
    try:
        if snapshot_esp_critical_files():
            snap.notes.append("esp_snapshot_ok")
        else:
            snap.notes.append("esp_snapshot_empty")
    except Exception as e:
        snap.notes.append(f"esp_snapshot_skip")
        log(f"ESP snapshot: {e}", "WARN")

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
    # Deep verification scorecard (informational — intelligent_boot_repair acts on it)
    try:
        from .boot_emergency import deep_boot_verification

        deep = deep_boot_verification(prefer_uefi=expect_uefi)
        result["deep"] = {
            "ok": deep.get("ok"),
            "score": deep.get("score"),
            "max_score": deep.get("max_score"),
            "issues": deep.get("issues"),
            "warnings": deep.get("warnings"),
        }
    except Exception as e:
        result["deep_error"] = type(e).__name__

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
    """Stage guide + optional GParted/FreeDOS media after native + PS Storage failed."""
    guide = write_gparted_rescue_guide(reason=reason, system_disk=system_disk, mode=mode)
    iso = download_gparted_iso()
    freedos = None
    try:
        from .boot_emergency import stage_freedos_rescue_media

        freedos = stage_freedos_rescue_media(reason=reason)
    except Exception as e:
        log(f"FreeDOS stage: {e}", "WARN")
    out = {
        "guide": str(guide),
        "iso": str(iso) if iso else None,
        "freedos": freedos,
        "tools": [
            "ps-storage",
            "bcdboot",
            "bcdedit",
            "bootrec",
            "winre-ramdisk",
            "winpe-bcd-ramdisk",
            "gparted-live",
            "freedos-live",
            "mbr2gpt",
        ],
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
    gu = guarantee_bootable(expect_uefi=prefer_uefi, system_disk=snap.disk_number, force_restore=not repaired)
    result["postflight"] = gu.get("postflight") or {}
    result["guarantee"] = gu
    result["ok"] = bool(gu.get("bootable") and (repaired or gu.get("restored")))
    if repaired and not (result["postflight"] or {}).get("ok"):
        log("bcdboot reported OK but postflight found issues — restore path applied", "WARN")
    return result


def validated_mbr_to_gpt(disk_number: int) -> tuple[bool, int, str, dict[str, Any]]:
    """Preflight → mbr2gpt → guarantee bootable → report on fail; never leave PC unbootable."""
    from .mbrgpt import convert_mbr_to_gpt

    meta: dict[str, Any] = {}
    snap = preflight_boot_edit(intend="mbr2gpt", system_disk=disk_number, require_disk=True)
    meta["preflight"] = snap.as_dict()
    if not snap.safe_to_mutate:
        gu = guarantee_bootable(expect_uefi=False, system_disk=disk_number)
        meta["guarantee"] = gu
        return False, -1, "preflight blocked: " + ",".join(snap.block_reasons), meta

    ok, code, msg = convert_mbr_to_gpt(disk_number)
    meta["mbr2gpt"] = {"ok": ok, "code": code, "msg": msg}

    # ALWAYS restore/verify bootability (success or fail)
    gu = guarantee_bootable(expect_uefi=ok, system_disk=disk_number, force_restore=not ok)
    meta["guarantee"] = gu
    meta["postflight"] = gu.get("postflight") or {}

    if ok and gu.get("bootable"):
        if not (meta["postflight"] or {}).get("ok"):
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
    try:
        meta["autodiag"] = report_boot_failure_autodiag(
            kind="mbr2gpt-failed",
            message=f"MBR→GPT failed ({msg}) — PC kept bootable via restore"
            if gu.get("bootable")
            else f"MBR→GPT failed ({msg}) — restore attempted",
            op_result={"ok": ok, "actions": [], "fallback": meta.get("fallback"), "preflight": meta.get("preflight")},
            guarantee=gu,
        )
    except Exception as e:
        log(f"mbr2gpt autodiag: {e}", "WARN")

    # If conversion claimed OK but not bootable — treat as failure after restore
    if ok and not gu.get("bootable"):
        return False, code, "converted_but_not_bootable_after_restore", meta
    return False, code, msg, meta
