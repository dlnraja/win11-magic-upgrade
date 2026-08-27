"""
Emergency boot repair beyond diskpart — verification, regeneration, temporary PE/DOS.

Design:
  - Prefer Microsoft tools already on the PC (bcdedit, bcdboot, reagentc, Storage cmdlets)
  - Never wipe C: / never auto-flash USB / never auto-boot foreign ISOs
  - Temporary WinPE: BCD one-shot ramdisk entry from Winre.wim (+ boot.sdi)
  - Temporary DOS: stage FreeDOS Live ISO + guide only (operator boots media)
  - Max checks before declaring the machine safe to reboot
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log

FREEDOS_ISO_NAME = "FD13-LiveCD.zip"
# Official FreeDOS 1.3 LiveCD zip (SourceForge) — staged only, never auto-booted.
FREEDOS_ISO_URL = (
    "https://downloads.sourceforge.net/project/freedos/Official/1.3/FD13-LiveCD.zip"
)


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


def _ps(script: str, timeout: int = 240) -> tuple[int, str]:
    """Run a PowerShell snippet; returns (code, stdout+stderr)."""
    return _run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        timeout=timeout,
    )


def _firmware() -> str:
    try:
        import ctypes

        ft = ctypes.c_uint(0)
        if ctypes.windll.kernel32.GetFirmwareType(ctypes.byref(ft)):
            return {1: "BIOS", 2: "UEFI"}.get(ft.value, "Unknown")
    except Exception:
        pass
    return "Unknown"


def _bcdedit(*args: str, timeout: int = 90) -> tuple[int, str]:
    return _run(["bcdedit", *args], timeout=timeout)


# ---------------------------------------------------------------------------
# Deep verification
# ---------------------------------------------------------------------------


def deep_boot_verification(*, prefer_uefi: bool | None = None) -> dict[str, Any]:
    """
    Maximum practical boot-path checks (no reboot test):
      winload on disk, BCD device/osdevice, EFI/BIOS files, Secure Boot, firmware enum,
      WinRE status, System partition free space.
    """
    uefi = prefer_uefi if prefer_uefi is not None else (_firmware() == "UEFI")
    drive = os.environ.get("SystemDrive", "C:")
    sys_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    report: dict[str, Any] = {
        "ok": False,
        "uefi": uefi,
        "checks": {},
        "issues": [],
        "warnings": [],
        "score": 0,
        "max_score": 0,
    }

    def _check(name: str, passed: bool, *, warn: bool = False, detail: str | None = None) -> None:
        report["max_score"] += 1
        report["checks"][name] = {"ok": passed, "detail": (detail or "")[:240]}
        if passed:
            report["score"] += 1
        elif warn:
            report["warnings"].append(name)
        else:
            report["issues"].append(name)

    # 1) winload exists for firmware mode
    winload_efi = sys_root / "System32" / "winload.efi"
    winload_exe = sys_root / "System32" / "winload.exe"
    if uefi:
        _check("winload_efi_present", winload_efi.is_file(), detail=str(winload_efi.name))
    else:
        _check(
            "winload_exe_present",
            winload_exe.is_file() or winload_efi.is_file(),
            detail="winload.exe or .efi",
        )

    # 2) bootmgr / bootmgfw presence via postflight-style mount
    try:
        from .sysreserved import mount_esp, unmount_letter
        from .boot_safe import inspect_boot_files_on_letter

        mounted = mount_esp()
        try:
            if mounted:
                letter = mounted.rstrip(":\\")[:1]
                files = inspect_boot_files_on_letter(letter)
                report["checks"]["esp_files"] = files
                if uefi:
                    _check(
                        "esp_uefi_loader",
                        bool(files.get("efi_boot_bootx64") or files.get("efi_ms_bootmgfw")),
                    )
                else:
                    _check(
                        "esp_bios_or_uefi_loader",
                        bool(
                            files.get("bios_bootmgr")
                            or files.get("bios_bcd")
                            or files.get("efi_boot_bootx64")
                            or files.get("efi_ms_bootmgfw")
                        ),
                    )
                # BCD store file on ESP/BIOS
                bcd_path = Path(f"{letter}:\\EFI\\Microsoft\\Boot\\BCD") if uefi else Path(f"{letter}:\\Boot\\BCD")
                alt = Path(f"{letter}:\\EFI\\Microsoft\\Boot\\BCD")
                present = bcd_path.is_file() or alt.is_file() or Path(f"{letter}:\\Boot\\BCD").is_file()
                _check("esp_bcd_file", present, warn=True)
            else:
                _check("esp_mountable", False)
        finally:
            if mounted:
                try:
                    unmount_letter(mounted)
                except Exception:
                    pass
    except Exception as e:
        _check("esp_inspect", False, detail=str(e)[:120])

    # 3) bcdedit {bootmgr} + {current} device points at SystemDrive
    c_bm, o_bm = _bcdedit("/enum", "{bootmgr}")
    _check("bcd_bootmgr", c_bm == 0 and bool(o_bm) and "cannot" not in (o_bm or "").lower())

    c_cur, o_cur = _bcdedit("/enum", "{current}")
    if c_cur != 0:
        c_cur, o_cur = _bcdedit("/enum", "{default}")
    _check("bcd_osloader", c_cur == 0 and bool(o_cur), warn=True)

    device_ok = False
    if o_cur:
        # Accept partition=C: or boot / locate forms that still resolve
        if re.search(rf"device\s+partition={re.escape(drive)}", o_cur, re.I):
            device_ok = True
        elif re.search(r"device\s+(boot|locate)", o_cur, re.I):
            device_ok = True
            report["warnings"].append("bcd_device_boot_or_locate")
        path_ok = bool(
            re.search(r"path\s+\\[Ww]indows\\system32\\winload\.(efi|exe)", o_cur)
            or re.search(r"path\s+\\windows\\system32\\boot\\winload\.(efi|exe)", o_cur, re.I)
        )
        _check("bcd_winload_path", path_ok, warn=True)
    _check("bcd_device_systemdrive", device_ok, warn=not device_ok)

    # 4) Firmware BCD entries (UEFI NVRAM mirror) — soft
    if uefi:
        c_fw, o_fw = _bcdedit("/enum", "firmware")
        _check("bcd_firmware_enum", c_fw == 0 and bool(o_fw), warn=True, detail=(o_fw or "")[:80])

    # 5) Secure Boot (informational)
    c_sb, o_sb = _ps("try { if (Confirm-SecureBootUEFI) { 'On' } else { 'Off' } } catch { 'N/A' }")
    sb = (o_sb or "").strip().splitlines()[-1] if o_sb else "N/A"
    report["secure_boot"] = sb
    _check("secure_boot_readable", sb in ("On", "Off", "N/A"), warn=True, detail=sb)

    # 6) WinRE
    reagentc = sys_root / "System32" / "reagentc.exe"
    if reagentc.exists():
        c_re, o_re = _run([str(reagentc), "/info"], timeout=90)
        enabled = bool(re.search(r"Windows RE status:\s*Enabled", o_re or "", re.I))
        _check("winre_enabled", enabled, warn=True)
        report["winre_info_tail"] = (o_re or "")[-300:]
    else:
        _check("winre_enabled", False, warn=True, detail="reagentc_missing")

    # 7) C: free space headroom
    try:
        free_gb = round(shutil.disk_usage(drive + "\\").free / (1024**3), 2)
        report["c_free_gb"] = free_gb
        _check("c_free_ge_1gb", free_gb >= 1.0, warn=True, detail=str(free_gb))
    except Exception:
        _check("c_free_ge_1gb", False, warn=True)

    # 8) Storage module available (non-diskpart path)
    c_st, o_st = _ps("Get-Command Get-Partition -ErrorAction SilentlyContinue | Select -Expand Source")
    _check("ps_storage_available", c_st == 0 and "Storage" in (o_st or ""), warn=True)

    # Score: hard issues empty AND score >= 60% of checks
    hard = [i for i in report["issues"] if i not in report["warnings"]]
    report["ok"] = len(hard) == 0 and (
        report["max_score"] == 0 or (report["score"] / max(report["max_score"], 1)) >= 0.55
    )
    report["hard_issues"] = hard

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "boot-deep-verify.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    level = "OK" if report["ok"] else "WARN"
    log(
        f"Deep boot verify score={report['score']}/{report['max_score']} "
        f"issues={report['issues'][:6]}",
        level,
    )
    return report


# ---------------------------------------------------------------------------
# Non-diskpart: PowerShell Storage
# ---------------------------------------------------------------------------


def ps_storage_create_esp(
    *,
    size_mb: int = 512,
    system_disk: int | None = None,
    prefer_uefi: bool = True,
    run_bcdboot: bool = True,
) -> dict[str, Any]:
    """
    Create a new ESP (or MBR system) partition using Storage cmdlets — no diskpart.
    Shrinks C: only if supported size allows; never touches other disks.
    Set run_bcdboot=False when caller will restore WIM/files first, then bcdboot.
    """
    result: dict[str, Any] = {
        "ok": False,
        "created": False,
        "actions": [],
        "letter": None,
        "method": "ps_storage",
    }
    allow = os.environ.get("MAGIC_PS_STORAGE_FALLBACK", "1").strip().lower()
    if allow in ("0", "false", "no"):
        result["actions"].append("ps_storage_disabled")
        return result

    # Session cap — avoid multi-shrink / multi-ESP on retries
    try:
        cap_path = STATE_DIR / "ps-esp-create-count.txt"
        count = int(cap_path.read_text(encoding="utf-8").strip() or "0") if cap_path.exists() else 0
        if count >= 2:
            result["actions"].append("ps_storage_session_cap")
            log("PS Storage ESP create skipped — session cap (max 2)", "WARN")
            return result
    except Exception:
        count = 0

    disk_filter = ""
    if system_disk is not None and int(system_disk) >= 0:
        disk_filter = f"$diskN = {int(system_disk)}; "
    else:
        disk_filter = (
            "$sys = Get-Partition -DriveLetter ($env:SystemDrive.TrimEnd(':')) -EA Stop; "
            "$diskN = $sys.DiskNumber; "
        )

    gpt_type = "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}"  # EFI System
    size_bytes = int(size_mb) * 1024 * 1024

    script = f"""
$ErrorActionPreference = 'Stop'
{disk_filter}
$c = Get-Partition -DriveLetter ($env:SystemDrive.TrimEnd(':')) -EA Stop
if ($c.DiskNumber -ne $diskN) {{ throw "C: not on expected disk $diskN" }}
$supp = Get-PartitionSupportedSize -DiskNumber $diskN -PartitionNumber $c.PartitionNumber
$need = [uint64]{size_bytes}
$target = [uint64]($c.Size - $need)
if ($target -lt $supp.SizeMin -or $target -gt $supp.SizeMax) {{
  throw "Shrink not supported (need free $need bytes)"
}}
Resize-Partition -DiskNumber $diskN -PartitionNumber $c.PartitionNumber -Size $target
$partStyle = (Get-Disk -Number $diskN).PartitionStyle
if ($partStyle -eq 'GPT') {{
  $np = New-Partition -DiskNumber $diskN -Size $need -GptType '{gpt_type}'
}} else {{
  $np = New-Partition -DiskNumber $diskN -Size $need -MbrType 0x0C
}}
$letter = $null
foreach ($L in 83..90) {{
  $ch = [char]$L
  if (-not (Test-Path ("$ch" + ':\\'))) {{ $letter = "$ch"; break }}
}}
if (-not $letter) {{ throw 'No free drive letter' }}
Add-PartitionAccessPath -DiskNumber $diskN -PartitionNumber $np.PartitionNumber -AccessPath ($letter + ':')
Format-Volume -DriveLetter $letter -FileSystem FAT32 -NewFileSystemLabel 'SYSTEM' -Force -Confirm:$false | Out-Null
Write-Output ("OK|" + $letter + "|" + $partStyle + "|" + $diskN)
"""
    log(f"PowerShell Storage ESP create (~{size_mb} MB) on disk filter...", "STEP")
    code, out = _ps(script, timeout=360)
    result["actions"].append(f"ps_create_esp:{code}")
    result["output_tail"] = (out or "")[-400:]
    m = re.search(r"OK\|([A-Z])\|(\w+)\|(\d+)", out or "")
    if code == 0 and m:
        letter = m.group(1)
        result["letter"] = letter
        result["partition_style"] = m.group(2)
        result["disk"] = int(m.group(3))
        result["created"] = True
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            (STATE_DIR / "ps-esp-create-count.txt").write_text(str(count + 1), encoding="utf-8")
        except Exception:
            pass
        if not run_bcdboot:
            result["ok"] = True  # created+formatted; caller applies payload then bcdboot
            result["actions"].append("bcdboot_deferred")
            log(f"PS Storage partition {letter}: created (bcdboot deferred)", "OK")
            return result
        sys_root = os.environ.get("SystemRoot", r"C:\Windows")
        bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
        mode = "UEFI" if prefer_uefi or m.group(2).upper() == "GPT" else "BIOS"
        if bcdboot.exists():
            c2, o2 = _run([str(bcdboot), sys_root, "/s", f"{letter}:", "/f", mode], timeout=180)
            result["actions"].append(f"bcdboot_{mode}:{c2}")
            if c2 != 0:
                c3, o3 = _run([str(bcdboot), sys_root, "/s", f"{letter}:", "/f", "ALL"], timeout=180)
                result["actions"].append(f"bcdboot_ALL:{c3}")
                result["ok"] = c3 == 0
            else:
                result["ok"] = True
        else:
            result["ok"] = False
            result["actions"].append("bcdboot_missing")
        log(
            f"PS Storage new boot partition {letter}: style={m.group(2)} ok={result['ok']}",
            "OK" if result["ok"] else "WARN",
        )
    else:
        log(f"PS Storage ESP create failed: {(out or '')[:240]}", "WARN")
    return result


def ps_mount_esp_letter() -> str | None:
    """Mount ESP via Storage Add-PartitionAccessPath (no diskpart)."""
    script = r"""
$ErrorActionPreference = 'Stop'
$esp = Get-Partition | Where-Object {
  $_.GptType -eq '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' -or $_.IsSystem -eq $true
} | Select-Object -First 1
if (-not $esp) { throw 'No ESP/system partition' }
if ($esp.DriveLetter) { Write-Output ($esp.DriveLetter.ToString() + ':'); exit 0 }
$letter = $null
foreach ($L in 83..90) {
  $ch = [char]$L
  if (-not (Test-Path ("$ch" + ':\'))) { $letter = "$ch"; break }
}
if (-not $letter) { throw 'No free letter' }
Add-PartitionAccessPath -DiskNumber $esp.DiskNumber -PartitionNumber $esp.PartitionNumber -AccessPath ($letter + ':')
Write-Output ($letter + ':')
"""
    code, out = _ps(script, timeout=120)
    if code != 0:
        return None
    for line in (out or "").splitlines():
        line = line.strip()
        if re.fullmatch(r"[A-Z]:", line):
            log(f"ESP mounted via Storage → {line}", "OK")
            return line
    return None


# ---------------------------------------------------------------------------
# Emergency BCD / ESP regeneration
# ---------------------------------------------------------------------------


def emergency_regenerate_bcd(*, prefer_uefi: bool | None = None) -> dict[str, Any]:
    """
    Aggressive but contained: export BCD, rewrite via bcdboot ALL/UEFI/BIOS,
    optionally recreate corrupt ESP BCD file after backup, heal devices.
    Does NOT format the ESP.
    """
    from .boot_safe import backup_bcd, heal_bcd_store, rewrite_boot_files_from_windows

    uefi = prefer_uefi if prefer_uefi is not None else (_firmware() == "UEFI")
    out: dict[str, Any] = {"ok": False, "actions": []}
    log("EMERGENCY BCD REGENERATION", "STEP")

    if backup_bcd():
        out["actions"].append("bcd_export")

    # Soft-delete corrupt BCD *file* on ESP only if unreadable / tiny, then bcdboot
    try:
        from .sysreserved import mount_esp, unmount_letter

        mounted = mount_esp()
        try:
            if mounted:
                letter = mounted.rstrip(":\\")[:1]
                candidates = [
                    Path(f"{letter}:\\EFI\\Microsoft\\Boot\\BCD"),
                    Path(f"{letter}:\\Boot\\BCD"),
                ]
                for p in candidates:
                    if not p.is_file():
                        continue
                    try:
                        sz = p.stat().st_size
                    except Exception:
                        sz = 0
                    # Tiny / empty BCD is almost always corrupt
                    if sz < 1024:
                        bak = STATE_DIR / "bcd-backups" / f"corrupt-BCD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                        bak.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(p, bak)
                            p.unlink()
                            out["actions"].append(f"removed_tiny_bcd:{p.name}")
                            log(f"Removed corrupt tiny BCD ({sz} B) — backup {bak.name}", "WARN")
                        except Exception as e:
                            log(f"Cannot remove tiny BCD: {e}", "WARN")
        finally:
            if mounted:
                try:
                    unmount_letter(mounted)
                except Exception:
                    pass
    except Exception as e:
        out["actions"].append(f"esp_bcd_scan_skip:{type(e).__name__}")

    if rewrite_boot_files_from_windows(prefer_uefi=uefi):
        out["actions"].append("bcdboot_rewrite")

    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    if bcdboot.exists():
        for mode in (("ALL", "UEFI", "BIOS") if uefi else ("ALL", "BIOS", "UEFI")):
            c, o = _run([str(bcdboot), sys_root, "/f", mode], timeout=180)
            out["actions"].append(f"bcdboot_{mode}:{c}")
            if c == 0 or "successfully" in (o or "").lower():
                break

    out["actions"].extend(heal_bcd_store(prefer_uefi=uefi))

    # Rebuild boot menu order: Windows first
    _bcdedit("/displayorder", "{current}", "/addfirst")
    out["actions"].append("displayorder_current_first")

    verify = deep_boot_verification(prefer_uefi=uefi)
    out["verify"] = {
        "ok": verify.get("ok"),
        "score": verify.get("score"),
        "max_score": verify.get("max_score"),
        "issues": verify.get("issues"),
    }
    out["ok"] = bool(verify.get("ok") or verify.get("score", 0) >= 4)
    log(f"Emergency regenerate done ok={out['ok']}", "OK" if out["ok"] else "WARN")
    return out


# ---------------------------------------------------------------------------
# Temporary WinPE (ramdisk BCD) + FreeDOS stage
# ---------------------------------------------------------------------------


def _find_winre_wim() -> Path | None:
    roots = [
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Recovery" / "Winre.wim",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Recovery" / "Winre.wim",
    ]
    for p in roots:
        if p.is_file() and p.stat().st_size > 1_000_000:
            return p
    # Search common recovery partition letters is too invasive — skip
    return None


def _find_boot_sdi() -> Path | None:
    sys_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidates = [
        sys_root / "System32" / "Recovery" / "boot.sdi",
        sys_root / "Boot" / "DVD" / "EFI" / "boot.sdi",
        sys_root / "Boot" / "DVD" / "PCAT" / "boot.sdi",
        sys_root / "System32" / "boot.sdi",
    ]
    for p in candidates:
        if p.is_file() and p.stat().st_size > 1000:
            return p
    return None


def _find_install_boot_wim() -> Path | None:
    """Optional lightweight PE: boot.wim from a previously attached ISO under state/cache."""
    search_roots = [
        STATE_DIR / "iso",
        STATE_DIR / "cache",
        Path(os.environ.get("SystemDrive", "C:")) / "ESD",
    ]
    for root in search_roots:
        if not root.exists():
            continue
        for p in root.rglob("boot.wim"):
            try:
                if p.is_file() and p.stat().st_size > 50_000_000:
                    return p
            except Exception:
                continue
    return None


def stage_temporary_winpe_ramdisk(
    *,
    one_shot: bool = True,
    prefer_uefi: bool | None = None,
) -> dict[str, Any]:
    """
    Create a TEMPORARY BCD osloader that boots Winre.wim (or boot.wim) as WinPE ramdisk.
    Uses bcdedit /bootsequence for one-shot next reboot — then Windows again.
    Falls back gracefully if boot.sdi / WIM missing.
    staged=True ONLY when one-shot /bootsequence succeeds.
    """
    result: dict[str, Any] = {
        "staged": False,
        "menu_entry": False,
        "mode": None,
        "actions": [],
        "guid": None,
    }
    allow = os.environ.get("MAGIC_WINPE_FALLBACK", "1").strip().lower()
    if allow in ("0", "false", "no"):
        result["actions"].append("winpe_fallback_disabled")
        return result

    # Replace any previous temp entry first
    result["actions"].extend(cleanup_temporary_winpe_bcd())

    uefi = prefer_uefi if prefer_uefi is not None else (_firmware() == "UEFI")
    wim = _find_winre_wim() or _find_install_boot_wim()
    sdi = _find_boot_sdi()
    if not wim:
        result["actions"].append("no_wim")
        log("No Winre.wim/boot.wim for temporary WinPE ramdisk", "WARN")
        return result
    if not sdi:
        result["actions"].append("no_boot_sdi")
        log("boot.sdi missing — cannot craft BCD ramdisk WinPE entry", "WARN")
        return result

    result["wim"] = str(wim)
    result["sdi"] = str(sdi)
    drive = wim.drive.rstrip("\\")  # e.g. C:
    # Paths for ramdisk= syntax use [C:]\path\file.wim
    wim_rel = str(wim)[2:] if re.match(r"^[A-Za-z]:", str(wim)) else str(wim)
    if not wim_rel.startswith("\\"):
        wim_rel = "\\" + wim_rel
    sdi_rel = str(sdi)[2:] if re.match(r"^[A-Za-z]:", str(sdi)) else str(sdi)
    if not sdi_rel.startswith("\\"):
        sdi_rel = "\\" + sdi_rel

    log(f"Staging temporary WinPE ramdisk from {wim.name}...", "STEP")

    # Ensure {ramdiskoptions}
    c0, o0 = _bcdedit("/enum", "{ramdiskoptions}")
    if c0 != 0 or "cannot" in (o0 or "").lower():
        c1, o1 = _bcdedit("/create", "{ramdiskoptions}", "/d", "Win11Magic Ramdisk Options")
        result["actions"].append(f"create_ramdiskoptions:{c1}")
    _bcdedit("/set", "{ramdiskoptions}", "ramdisksdidevice", f"partition={drive}")
    _bcdedit("/set", "{ramdiskoptions}", "ramdisksdipath", sdi_rel)
    result["actions"].append("ramdiskoptions_set")

    # Create osloader entry
    c2, o2 = _bcdedit("/create", "/d", "Win11Magic Temp WinPE", "/application", "osloader")
    result["actions"].append(f"create_osloader:{c2}")
    guid_m = re.search(r"\{[0-9a-fA-F-]{36}\}", o2 or "")
    if c2 != 0 or not guid_m:
        # Sometimes /create prints elsewhere
        log(f"bcdedit /create osloader failed: {(o2 or '')[:200]}", "WARN")
        return result
    guid = guid_m.group(0)
    result["guid"] = guid

    ramdisk_dev = f"ramdisk=[{drive}]{wim_rel},{{ramdiskoptions}}"
    winload = r"\windows\system32\boot\winload.efi" if uefi else r"\windows\system32\boot\winload.exe"
    for name, value in (
        ("device", ramdisk_dev),
        ("osdevice", ramdisk_dev),
        ("path", winload),
        ("systemroot", r"\windows"),
        ("detecthal", "Yes"),
        ("winpe", "Yes"),
        ("description", "Win11Magic Temp WinPE (auto)"),
    ):
        c, o = _bcdedit("/set", guid, name, value)
        if c != 0:
            result["actions"].append(f"set_{name}_fail:{c}")
            log(f"bcdedit set {name} failed: {(o or '')[:120]}", "WARN")
        else:
            result["actions"].append(f"set_{name}")

    _bcdedit("/displayorder", guid, "/addlast")
    result["actions"].append("displayorder_addlast")

    if one_shot:
        c3, o3 = _bcdedit("/bootsequence", guid)
        result["actions"].append(f"bootsequence:{c3}")
        if c3 == 0:
            result["staged"] = True
            result["mode"] = "bcd_winpe_ramdisk_oneshot"
            log("Temporary WinPE ramdisk one-shot staged via bcdedit /bootsequence", "OK")
        else:
            log(f"/bootsequence failed: {(o3 or '')[:160]}", "WARN")
            # Menu entry only — NOT a safe one-shot reboot path
            result["staged"] = False
            result["menu_entry"] = True
            result["mode"] = "bcd_winpe_ramdisk_menu"
    else:
        result["staged"] = False
        result["menu_entry"] = True
        result["mode"] = "bcd_winpe_ramdisk_menu"

    # Persist guid for cleanup later
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "temp-winpe-bcd.json").write_text(
            json.dumps(
                {
                    "guid": guid,
                    "utc": datetime.now(timezone.utc).isoformat(),
                    "mode": result["mode"],
                    "wim": wim.name,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        guide = STATE_DIR / "rescue" / "WinPE-Temporary-Boot.txt"
        guide.parent.mkdir(parents=True, exist_ok=True)
        guide.write_text(
            "\n".join(
                [
                    "Win11 Magic Upgrade — temporary WinPE ramdisk boot",
                    "=" * 56,
                    f"Staged: {result.get('staged')} mode={result.get('mode')}",
                    f"BCD guid: {guid}",
                    f"WIM: {wim}",
                    f"SDI: {sdi}",
                    "",
                    "Next reboot (one-shot) enters this lite WinPE, then Windows.",
                    "In PE: bcdboot, bootrec, diskpart, Startup Repair.",
                    "Disable: MAGIC_WINPE_FALLBACK=0",
                    f"Cleanup entry later: bcdedit /delete {guid} /f",
                ]
            ),
            encoding="utf-8",
        )
        result["guide"] = str(guide)
    except Exception:
        pass
    return result


def cleanup_temporary_winpe_bcd() -> list[str]:
    """Remove previously staged WinPE BCD entry if present."""
    actions: list[str] = []
    meta = STATE_DIR / "temp-winpe-bcd.json"
    if not meta.exists():
        return actions
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        guid = data.get("guid")
        if guid and re.fullmatch(r"\{[0-9a-fA-F-]{36}\}", guid):
            c, o = _bcdedit("/delete", guid, "/f")
            actions.append(f"delete_winpe:{c}")
            log(f"Cleaned temp WinPE BCD {guid} -> {c}", "OK" if c == 0 else "WARN")
        meta.unlink(missing_ok=True)
    except Exception as e:
        actions.append(f"cleanup_fail:{type(e).__name__}")
    return actions


def stage_freedos_rescue_media(*, reason: str = "boot_emergency") -> dict[str, Any]:
    """
    Stage FreeDOS Live media as a temporary DOS-like repair environment.
    Download only — NEVER auto-write USB / NEVER change boot order to it.
    """
    result: dict[str, Any] = {"staged": False, "actions": [], "iso": None, "guide": None}
    allow = os.environ.get("MAGIC_FREEDOS_FALLBACK", "1").strip().lower()
    if allow in ("0", "false", "no"):
        result["actions"].append("freedos_disabled")
        return result

    rescue = STATE_DIR / "rescue"
    rescue.mkdir(parents=True, exist_ok=True)
    dest = rescue / FREEDOS_ISO_NAME
    guide = rescue / "FreeDOS-Temporary-Boot.txt"

    if not dest.exists() or dest.stat().st_size < 1_000_000:
        try:
            log(f"Downloading FreeDOS Live staging package ({FREEDOS_ISO_NAME})...", "STEP")
            req = urllib.request.Request(
                FREEDOS_ISO_URL,
                headers={"User-Agent": "Win11MagicUpgrade-Rescue/1.26"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
                shutil.copyfileobj(resp, f, length=1024 * 1024)
            result["actions"].append("freedos_downloaded")
        except Exception as e:
            log(f"FreeDOS download skipped/failed: {e}", "WARN")
            result["actions"].append(f"freedos_download_fail:{type(e).__name__}")
            # Still write guide pointing at official URL
    else:
        result["actions"].append("freedos_already_cached")

    if dest.exists() and dest.stat().st_size > 1_000_000:
        result["staged"] = True
        result["iso"] = str(dest)

    guide.write_text(
        f"""Win11 Magic Upgrade — temporary FreeDOS (DOS-like) repair media
================================================================
Reason: {reason}
Cached: {dest if dest.exists() else '(download failed — use URL below)'}
URL: {FREEDOS_ISO_URL}

IMPORTANT
---------
• This is OPTIONAL last-resort media when WinRE/WinPE cannot run.
• Do NOT auto-boot from the app. Flash manually (Rufus / balenaEtcher).
• Prefer WinRE (reagentc /boottore) or temporary WinPE BCD ramdisk first.
• FreeDOS cannot run bcdboot/bcdedit — use it only for low-level disk tools
  you intentionally bring, or to regain a boot menu / firmware setup path.

Disable staging: MAGIC_FREEDOS_FALLBACK=0
""",
        encoding="utf-8",
    )
    result["guide"] = str(guide)
    # Desktop copy (short)
    try:
        desk = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Win11MagicUpgrade-FreeDOS-Rescue.txt"
        if desk.parent.is_dir():
            shutil.copy2(guide, desk)
    except Exception:
        pass
    log(f"FreeDOS rescue staged={result['staged']}", "OK" if result["staged"] else "WARN")
    return result


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_emergency_repair_suite(
    *,
    prefer_uefi: bool | None = None,
    system_disk: int | None = None,
    try_ps_storage_expand: bool = False,
    skip_partition_restore: bool = False,
) -> dict[str, Any]:
    """
    Full automated emergency path when diskpart / normal repair is insufficient:
      (optional partition restore) → regenerate BCD → PS Storage → WinRE then WinPE → FreeDOS
    Prefer a single one-shot PE path (WinRE first; WinPE only if WinRE failed).
    """
    uefi = prefer_uefi if prefer_uefi is not None else (_firmware() == "UEFI")
    log("=" * 60, "STEP")
    log("EMERGENCY REPAIR SUITE — non-diskpart + PE/DOS fallbacks", "STEP")
    summary: dict[str, Any] = {"actions": [], "uefi": uefi, "system_disk": system_disk}

    before = deep_boot_verification(prefer_uefi=uefi)
    summary["verify_before"] = {
        "ok": before.get("ok"),
        "score": before.get("score"),
        "issues": before.get("issues"),
    }

    # Avoid re-applying stale backup over a repair already done this pass
    if not skip_partition_restore:
        try:
            from .boot_partition_backup import ensure_partition_backup_then_repair

            part = ensure_partition_backup_then_repair(
                prefer_uefi=uefi, system_disk=system_disk, force_backup=False
            )
            summary["partition_repair"] = {
                "ok": part.get("ok"),
                "actions": (part.get("actions") or [])[-10:],
            }
            summary["actions"].extend(part.get("actions") or [])
        except Exception as e:
            summary["actions"].append(f"partition_repair_skip:{type(e).__name__}")
    else:
        summary["actions"].append("partition_restore_skipped_already_done")

    regen = emergency_regenerate_bcd(prefer_uefi=uefi)
    summary["regenerate"] = {k: regen.get(k) for k in ("ok", "actions")}
    summary["actions"].extend(regen.get("actions") or [])

    if try_ps_storage_expand and not regen.get("ok"):
        ps = ps_storage_create_esp(system_disk=system_disk, prefer_uefi=uefi)
        summary["ps_storage"] = {k: ps.get(k) for k in ("ok", "letter", "actions")}
        summary["actions"].extend(ps.get("actions") or [])

    from .boot_safe import stage_temporary_winre_ramdisk_boot

    # Clear stale WinPE before staging anything new
    summary["actions"].extend(cleanup_temporary_winpe_bcd())

    winre = stage_temporary_winre_ramdisk_boot(one_shot=True)
    summary["winre"] = {k: winre.get(k) for k in ("staged", "mode")}
    summary["actions"].extend(winre.get("actions") or [])

    winpe: dict[str, Any] = {"staged": False, "mode": None, "guid": None}
    # Only one one-shot: WinPE if WinRE failed (avoid /boottore vs /bootsequence fight)
    if not winre.get("staged"):
        winpe = stage_temporary_winpe_ramdisk(one_shot=True, prefer_uefi=uefi)
        summary["actions"].extend(winpe.get("actions") or [])
    else:
        summary["actions"].append("winpe_skipped_winre_oneshot_active")
    summary["winpe"] = {k: winpe.get(k) for k in ("staged", "mode", "guid")}

    if not winre.get("staged") and not winpe.get("staged"):
        dos = stage_freedos_rescue_media(reason="winre_and_winpe_unavailable")
        summary["freedos"] = {k: dos.get(k) for k in ("staged", "iso")}
        summary["actions"].extend(dos.get("actions") or [])
    else:
        summary["freedos"] = {"staged": False, "skipped": "pe_available"}

    after = deep_boot_verification(prefer_uefi=uefi)
    summary["verify_after"] = {
        "ok": after.get("ok"),
        "score": after.get("score"),
        "issues": after.get("issues"),
    }
    # bootable only from real verify — not a loose score that authorizes blind reboot
    summary["bootable"] = bool(after.get("ok"))
    summary["safe_reboot_path"] = bool(
        summary["bootable"] or winre.get("staged") or winpe.get("staged")
    )

    try:
        (STATE_DIR / "boot-emergency.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    log(
        f"Emergency suite done bootable={summary['bootable']} "
        f"safe_path={summary['safe_reboot_path']}",
        "OK" if summary["safe_reboot_path"] else "ERROR",
    )
    return summary
