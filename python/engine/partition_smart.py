"""
Intelligent Partition-Magic-style layout for ESP / System Reserved.

Safe Windows-native path (no auto-run of GParted):
  1) Map system disk partitions + free regions
  2) Prefer in-place GROW of the boot partition into adjacent free space
  3) Else smart SHRINK (C: first, optional large data NTFS) then create/grow
  4) Fix Windows boot (bcdboot / BCD heal) for the new size/location
  5) Detect Linux GRUB on ESP and preserve + write repair scripts
  6) Stage GParted/qparted automation scripts when native move is impossible
  7) Autonomous fallbacks + finish verification scorecard

GParted / Partition Magic GUIs are NEVER auto-executed against disks.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .diskpart_safe import get_system_disk_number, shrink_volume_letter
from .logutil import STATE_DIR, log

TARGET_BOOT_MB = 512
MIN_BOOT_MB = 260
GROW_EXTRA_MB = 64  # grow "a bit" beyond minimum when free space allows
MIN_C_FREE_AFTER_MB = 8192  # keep >= 8 GB free on C: after shrink
MIN_DATA_FREE_AFTER_MB = 4096
MAX_BOOT_PART_BYTES = 2 * 1024 * 1024 * 1024  # ESP/SRP never multi-GB
LINUX_EFI_VENDORS = (
    "ubuntu",
    "debian",
    "fedora",
    "centos",
    "rhel",
    "arch",
    "manjaro",
    "opensuse",
    "suse",
    "gentoo",
    "kali",
    "pop",
    "elementary",
    "linuxmint",
    "neon",
    "grub",
)


def _norm_letter(value: str | None) -> str | None:
    """Normalize 'Y', 'Y:', 'Y:\\' -> 'Y'. Reject empty/invalid."""
    if value is None:
        return None
    s = str(value).strip().upper().replace("/", "\\")
    s = s.rstrip("\\").rstrip(":")
    if len(s) != 1 or not ("A" <= s <= "Z"):
        return None
    return s


def _letter_root(value: str | None) -> str | None:
    L = _norm_letter(value)
    return f"{L}:" if L else None


def _valid_disk(n: Any) -> int | None:
    try:
        i = int(n)
    except (TypeError, ValueError):
        return None
    return i if i >= 0 else None


def _valid_part(n: Any) -> int | None:
    try:
        i = int(n)
    except (TypeError, ValueError):
        return None
    return i if i >= 1 else None


def _valid_mb(n: Any, *, minimum: int = 1, maximum: int = 8192) -> int | None:
    try:
        i = int(n)
    except (TypeError, ValueError):
        return None
    if i < minimum or i > maximum:
        return None
    return i


def _enabled() -> bool:
    return os.environ.get("MAGIC_SMART_PARTITION", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _allow_data_shrink() -> bool:
    return os.environ.get("MAGIC_SMART_SHRINK_DATA", "1").strip().lower() not in (
        "0",
        "false",
        "no",
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
    except Exception as e:
        return 1, str(e)


def _ps(script: str, timeout: int = 240) -> tuple[int, str]:
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


def map_system_disk(system_disk: int | None = None) -> dict[str, Any]:
    """Return partition map for the Windows system disk (JSON via Storage cmdlets)."""
    disk_n = _valid_disk(system_disk)
    if disk_n is None:
        disk_n = get_system_disk_number("C")
        disk_n = _valid_disk(disk_n)
    out: dict[str, Any] = {"ok": False, "disk": disk_n, "partitions": [], "style": None}
    if disk_n is None:
        out["error"] = "system_disk_unknown"
        return out

    script = f"""
$ErrorActionPreference = 'Stop'
$diskN = {int(disk_n)}
$d = Get-Disk -Number $diskN -EA Stop
$parts = Get-Partition -DiskNumber $diskN | Sort-Object Offset | ForEach-Object {{
  $v = $null
  try {{ $v = Get-Volume -Partition $_ -EA SilentlyContinue }} catch {{}}
  $suppMin = $null; $suppMax = $null
  try {{
    $s = Get-PartitionSupportedSize -DiskNumber $diskN -PartitionNumber $_.PartitionNumber -EA SilentlyContinue
    if ($s) {{ $suppMin = [uint64]$s.SizeMin; $suppMax = [uint64]$s.SizeMax }}
  }} catch {{}}
  [PSCustomObject]@{{
    Number = $_.PartitionNumber
    Offset = [uint64]$_.Offset
    Size = [uint64]$_.Size
    Type = [string]$_.Type
    GptType = [string]$_.GptType
    MbrType = if ($_.MbrType -ne $null) {{ [int]$_.MbrType }} else {{ $null }}
    DriveLetter = if ($_.DriveLetter) {{ $_.DriveLetter.ToString() }} else {{ '' }}
    IsSystem = [bool]$_.IsSystem
    IsBoot = [bool]$_.IsBoot
    IsActive = [bool]$_.IsActive
    FileSystem = if ($v) {{ [string]$v.FileSystem }} else {{ '' }}
    SizeRemaining = if ($v) {{ [uint64]$v.SizeRemaining }} else {{ [uint64]0 }}
    SizeMin = $suppMin
    SizeMax = $suppMax
  }}
}}
$obj = [PSCustomObject]@{{
  Disk = $diskN
  Style = [string]$d.PartitionStyle
  Size = [uint64]$d.Size
  Allocated = [uint64]$d.AllocatedSize
  Partitions = @($parts)
}}
$obj | ConvertTo-Json -Depth 6 -Compress
"""
    code, raw = _ps(script, timeout=180)
    if code != 0 or not raw.strip():
        out["error"] = (raw or "map_failed")[:300]
        return out
    try:
        # PowerShell may emit warnings before JSON — take last '{' line chunk
        jtxt = raw
        idx = raw.find("{")
        if idx >= 0:
            jtxt = raw[idx:]
        data = json.loads(jtxt)
    except Exception as e:
        out["error"] = f"json:{e}"
        out["raw_tail"] = raw[-400:]
        return out

    parts = data.get("Partitions") or data.get("partitions") or []
    if isinstance(parts, dict):
        parts = [parts]
    norm = []
    for p in parts:
        size = int(p.get("Size") or 0)
        rem = int(p.get("SizeRemaining") or 0)
        gpt = (p.get("GptType") or "").lower()
        ptype = (p.get("Type") or "").lower()
        letter = _norm_letter(p.get("DriveLetter")) or ""
        efi_guid = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b" in gpt or gpt.startswith("{c12a7328")
        # ESP: EFI GUID, or small System/IsSystem partition that is NOT C:
        is_esp = bool(
            efi_guid
            or (
                (ptype == "system" or bool(p.get("IsSystem")))
                and 0 < size <= MAX_BOOT_PART_BYTES
                and letter != "C"
            )
        )
        part_n = _valid_part(p.get("Number"))
        if part_n is None:
            continue
        norm.append(
            {
                "number": part_n,
                "offset": int(p.get("Offset") or 0),
                "size": size,
                "size_mb": size / (1024 * 1024),
                "free_mb": rem / (1024 * 1024),
                "type": p.get("Type"),
                "gpt": p.get("GptType"),
                "letter": letter,
                "fs": (p.get("FileSystem") or "").upper(),
                "is_system": bool(p.get("IsSystem")),
                "is_boot": bool(p.get("IsBoot")),
                "is_active": bool(p.get("IsActive")),
                "is_esp": is_esp,
                "size_min": int(p["SizeMin"]) if p.get("SizeMin") is not None else None,
                "size_max": int(p["SizeMax"]) if p.get("SizeMax") is not None else None,
            }
        )
    # Free regions between partitions / end of disk
    free_regions = []
    disk_size = int(data.get("Size") or 0)
    cursor = 0
    for p in sorted(norm, key=lambda x: x["offset"]):
        if p["offset"] > cursor + 1024 * 1024:  # >1MB gap
            gap = p["offset"] - cursor
            free_regions.append(
                {
                    "offset": cursor,
                    "size": gap,
                    "size_mb": gap / (1024 * 1024),
                    "before_part": p["number"],
                }
            )
        cursor = max(cursor, p["offset"] + p["size"])
    if disk_size > cursor + 1024 * 1024:
        gap = disk_size - cursor
        free_regions.append(
            {
                "offset": cursor,
                "size": gap,
                "size_mb": gap / (1024 * 1024),
                "before_part": None,
            }
        )

    out.update(
        {
            "ok": True,
            "style": data.get("Style") or data.get("style"),
            "disk_size": disk_size,
            "partitions": norm,
            "free_regions": free_regions,
        }
    )
    return out


def _find_boot_partition(layout: dict[str, Any], prefer_uefi: bool) -> dict[str, Any] | None:
    parts = layout.get("partitions") or []
    if prefer_uefi:
        # Prefer real EFI GUID, then small FAT System
        for p in parts:
            gpt = (p.get("gpt") or "").lower()
            if "c12a7328" in gpt:
                return p
        for p in parts:
            if p.get("is_esp") and (p.get("fs") or "") in ("FAT32", "FAT", ""):
                if p.get("size_mb", 9999) <= 2048:
                    return p
        for p in parts:
            if p.get("is_system") and (p.get("fs") or "") in ("FAT32", "FAT"):
                if p.get("letter") != "C" and p.get("size_mb", 9999) <= 2048:
                    return p
    for p in parts:
        if p.get("letter") == "C":
            continue
        if p.get("is_active") or (p.get("is_system") and p.get("fs") == "NTFS"):
            if p.get("size_mb", 9999) < 2048:
                return p
    for p in parts:
        if p.get("is_esp") or (p.get("is_system") and p.get("letter") != "C"):
            if p.get("size_mb", 9999) <= 2048:
                return p
    return None


def _c_partition(layout: dict[str, Any]) -> dict[str, Any] | None:
    for p in layout.get("partitions") or []:
        if p.get("letter") == "C":
            return p
    return None


def plan_smart_layout(
    layout: dict[str, Any],
    *,
    prefer_uefi: bool = True,
    target_mb: int = TARGET_BOOT_MB,
) -> dict[str, Any]:
    """
    Decide the safest operation sequence to get a comfortable boot partition.
    Strategies (ordered):
      extend_boot          - grow existing ESP/SRP into adjacent free space
      shrink_c_then_extend - shrink C: (creates free after C), then new ESP or extend if adjacent
      shrink_data_then_create - shrink a large data NTFS (optional), then create
      gparted_move         - native cannot move mid-disk; stage GParted scripts
    """
    plan: dict[str, Any] = {
        "strategy": "none",
        "steps": [],
        "need_mb": target_mb,
        "boot": None,
        "reasons": [],
        "gparted": False,
    }
    if not layout.get("ok"):
        plan["reasons"].append("layout_unavailable")
        plan["strategy"] = "fallback_legacy"
        return plan

    boot = _find_boot_partition(layout, prefer_uefi)
    c_part = _c_partition(layout)
    plan["boot"] = boot
    plan["c"] = {"number": c_part["number"], "free_mb": c_part["free_mb"]} if c_part else None

    boot_mb = float(boot["size_mb"]) if boot else 0.0
    boot_free = float(boot["free_mb"]) if boot else 0.0
    need_grow = boot is None or boot_mb < MIN_BOOT_MB or boot_free < 50
    if not need_grow:
        plan["strategy"] = "noop_space_ok"
        plan["reasons"].append("boot_partition_comfortable")
        return plan

    want = max(target_mb, int(boot_mb) + GROW_EXTRA_MB) if boot else target_mb
    grow_by = max(0, int(want - boot_mb)) if boot else target_mb
    plan["need_mb"] = max(grow_by, target_mb // 2) if boot else target_mb
    plan["target_boot_mb"] = want

    free_regions = layout.get("free_regions") or []
    # Adjacent free after boot partition?
    if boot:
        boot_end = boot["offset"] + boot["size"]
        for fr in free_regions:
            if abs(int(fr["offset"]) - boot_end) < 1024 * 1024 and fr["size_mb"] >= 32:
                plan["strategy"] = "extend_boot"
                plan["steps"] = [
                    {
                        "op": "extend",
                        "part": boot["number"],
                        "add_mb": min(int(fr["size_mb"]), max(grow_by, GROW_EXTRA_MB)),
                        "free_region_mb": fr["size_mb"],
                    }
                ]
                plan["reasons"].append("adjacent_free_after_boot")
                return plan

    # Free space after C: (typical after shrink) — create new boot partition
    if c_part:
        c_end = c_part["offset"] + c_part["size"]
        for fr in free_regions:
            if abs(int(fr["offset"]) - c_end) < 1024 * 1024 and fr["size_mb"] >= target_mb * 0.9:
                plan["strategy"] = "create_in_free"
                plan["steps"] = [
                    {
                        "op": "create_boot",
                        "size_mb": target_mb,
                        "uefi": prefer_uefi,
                        "free_mb": fr["size_mb"],
                    }
                ]
                plan["reasons"].append("free_after_c")
                return plan

    # Shrink C: if enough free
    if c_part and c_part["free_mb"] > MIN_C_FREE_AFTER_MB + target_mb + 500:
        plan["strategy"] = "shrink_c_then_create"
        plan["steps"] = [
            {"op": "shrink", "letter": "C", "mb": target_mb + 40},
            {"op": "create_boot", "size_mb": target_mb, "uefi": prefer_uefi},
        ]
        plan["reasons"].append("shrink_c")
        return plan

    # Optional smart shrink of other large NTFS data volumes
    if _allow_data_shrink():
        candidates = []
        for p in layout.get("partitions") or []:
            if p.get("letter") in ("", "C"):
                continue
            if p.get("is_esp") or p.get("is_system") or p.get("is_boot"):
                continue
            if (p.get("fs") or "") != "NTFS":
                continue
            if p.get("size_mb", 0) < 20000:  # skip small volumes
                continue
            if p.get("free_mb", 0) < MIN_DATA_FREE_AFTER_MB + target_mb + 1000:
                continue
            # Prefer partitions that end near where we need space (after them = free for create)
            candidates.append(p)
        candidates.sort(key=lambda x: (-x["free_mb"], -x["size_mb"]))
        if candidates:
            p = candidates[0]
            plan["strategy"] = "shrink_data_then_create"
            plan["steps"] = [
                {"op": "shrink", "letter": p["letter"], "mb": target_mb + 40, "part": p["number"]},
                {"op": "create_boot", "size_mb": target_mb, "uefi": prefer_uefi},
            ]
            plan["reasons"].append(f"shrink_data:{p['letter']}")
            return plan

    # Free space exists but not adjacent — need move (GParted)
    large_free = [fr for fr in free_regions if fr["size_mb"] >= target_mb]
    if large_free and boot:
        plan["strategy"] = "gparted_move"
        plan["gparted"] = True
        plan["steps"] = [
            {
                "op": "gparted_move_grow",
                "boot_part": boot["number"],
                "free_mb": large_free[0]["size_mb"],
                "target_mb": want,
            }
        ]
        plan["reasons"].append("free_not_adjacent_need_move")
        return plan

    plan["strategy"] = "fallback_legacy"
    plan["reasons"].append("no_smart_path")
    return plan


def try_extend_boot_partition(
    *,
    disk: int,
    part_number: int,
    add_mb: int,
) -> dict[str, Any]:
    """Grow existing partition into following free space (Resize-Partition / diskpart extend)."""
    result: dict[str, Any] = {"ok": False, "actions": [], "method": None}
    disk_n = _valid_disk(disk)
    part_n = _valid_part(part_number)
    add = _valid_mb(add_mb, minimum=16, maximum=4096)
    if disk_n is None or part_n is None or add is None:
        result["actions"].append(
            f"bad_params:disk={disk!r},part={part_number!r},add_mb={add_mb!r}"
        )
        log("Extend refused: invalid disk/part/add_mb parameters", "ERROR")
        return result
    add_bytes = int(add) * 1024 * 1024
    script = f"""
$ErrorActionPreference = 'Stop'
$diskN = {int(disk_n)}
$pn = {int(part_n)}
$p = Get-Partition -DiskNumber $diskN -PartitionNumber $pn -EA Stop
$supp = Get-PartitionSupportedSize -DiskNumber $diskN -PartitionNumber $pn
$target = [uint64]($p.Size + {add_bytes})
if ($target -gt $supp.SizeMax) {{ $target = [uint64]$supp.SizeMax }}
if ($target -le $p.Size) {{ throw 'No grow room (SizeMax)' }}
Resize-Partition -DiskNumber $diskN -PartitionNumber $pn -Size $target
Write-Output ("OK|" + $p.Size + "|" + $target)
"""
    log(f"Smart extend disk {disk_n} part {part_n} +~{add} MB", "STEP")
    code, out = _ps(script, timeout=300)
    result["actions"].append(f"ps_extend:{code}")
    result["output_tail"] = (out or "")[-300:]
    if code == 0 and "OK|" in (out or ""):
        result["ok"] = True
        result["method"] = "resize_partition"
        log("Boot partition extended in-place", "OK")
        return result

    from .diskpart_safe import run_diskpart

    ok2, dout2 = run_diskpart(
        f"select disk {int(disk_n)}\n"
        f"select partition {int(part_n)}\n"
        f"extend size={int(add)}\n"
        "exit\n"
    )
    result["actions"].append(f"diskpart_extend:{ok2}")
    result["output_tail"] = (dout2 or "")[-300:]
    if ok2 and not re.search(r"error|failed|échec|echec|denied", dout2 or "", re.I):
        result["ok"] = True
        result["method"] = "diskpart_extend"
        log("Boot partition extended via diskpart", "OK")
    else:
        log(f"Extend failed: {(out or dout2 or '')[:200]}", "WARN")
    return result


def detect_linux_efi(esp_root: str | None = None) -> dict[str, Any]:
    """Scan ESP for Linux / GRUB loaders (preserve during Windows boot repair)."""
    info: dict[str, Any] = {"found": False, "vendors": [], "paths": [], "esp": esp_root}
    root = _letter_root(esp_root) if esp_root else None
    mounted_here = False
    try:
        if not root:
            from .sysreserved import mount_esp

            root = _letter_root(mount_esp())
            mounted_here = bool(root)
        if not root:
            return info
        if not Path(root + "\\").exists():
            info["error"] = "esp_letter_not_mounted"
            return info
        efi = Path(root + "\\EFI")
        if not efi.is_dir():
            return info
        for child in efi.iterdir():
            if not child.is_dir():
                continue
            name = child.name.lower()
            if name in ("microsoft", "boot", "dell", "hp", "lenovo", "asus", "acer", "toshiba", "dynabook", "msi", "oem", "samsung", "sony", "fujitsu"):
                continue
            try:
                from .oem_adapt import OEM_EFI_VENDORS

                if name in OEM_EFI_VENDORS:
                    continue
            except Exception:
                pass
            if name in LINUX_EFI_VENDORS or any(v in name for v in LINUX_EFI_VENDORS):
                info["vendors"].append(child.name)
                for pattern in ("grubx64.efi", "shimx64.efi", "mmx64.efi", "grub.efi"):
                    p = child / pattern
                    if p.is_file():
                        info["paths"].append(str(p))
        info["found"] = bool(info["vendors"] or info["paths"])
        info["esp"] = root
        if info["found"]:
            log(f"Linux/GRUB EFI detected: {', '.join(info['vendors']) or info['paths']}", "INFO")
    except Exception as e:
        info["error"] = type(e).__name__
    finally:
        if mounted_here and root:
            try:
                from .sysreserved import unmount_letter

                unmount_letter(root)
            except Exception:
                pass
    return info


def fix_boot_for_new_layout(
    *,
    prefer_uefi: bool = True,
    boot_letter: str | None = None,
    preserve_grub: bool = True,
    unmount_target: bool = True,
) -> dict[str, Any]:
    """
    Rewrite Windows boot files for the (possibly moved/grown) ESP and keep GRUB if present.
    boot_letter: 'Y' / 'Y:' — must be mounted when provided.
    unmount_target: if True, dismount letters we mounted (or caller letter if unmount_target).
    """
    out: dict[str, Any] = {"ok": False, "actions": [], "grub": None, "bcdboot": None}
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    if not sys_root or not Path(sys_root).is_dir():
        out["actions"].append("bad_SystemRoot")
        return out
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"

    target = _letter_root(boot_letter)
    mounted_here = False
    if target and not Path(target + "\\").exists():
        out["actions"].append(f"boot_letter_not_mounted:{target}")
        target = None

    grub = detect_linux_efi(target)
    out["grub"] = grub
    if preserve_grub and grub.get("found"):
        out["actions"].append("grub_detected_preserve")
        _write_grub_repair_scripts(grub)

    targets: list[str] = []
    if target:
        targets.append(target)
    else:
        try:
            from .sysreserved import mount_esp

            m = _letter_root(mount_esp())
            if m:
                targets.append(m)
                mounted_here = True
        except Exception:
            pass

    mode = "UEFI" if prefer_uefi else "BIOS"
    if not bcdboot.is_file():
        out["actions"].append("bcdboot_missing")
    elif targets:
        for t in targets:
            # bcdboot requires "Y:" form (drive + colon, no trailing slash)
            c, o = _run([str(bcdboot), sys_root, "/s", t, "/f", mode], timeout=180)
            out["actions"].append(f"bcdboot_{mode}_{t}:{c}")
            out["bcdboot"] = {"code": c, "out": (o or "")[:200], "target": t, "mode": mode}
            if c != 0:
                c2, o2 = _run([str(bcdboot), sys_root, "/s", t, "/f", "ALL"], timeout=180)
                out["actions"].append(f"bcdboot_ALL_{t}:{c2}")
                out["ok"] = c2 == 0
                out["bcdboot"] = {"code": c2, "out": (o2 or "")[:200], "target": t, "mode": "ALL"}
            else:
                out["ok"] = True
    else:
        c, o = _run([str(bcdboot), sys_root, "/f", mode], timeout=180)
        out["actions"].append(f"bcdboot_default_{mode}:{c}")
        out["ok"] = c == 0
        if not out["ok"]:
            c2, _ = _run([str(bcdboot), sys_root, "/f", "ALL"], timeout=180)
            out["actions"].append(f"bcdboot_default_ALL:{c2}")
            out["ok"] = c2 == 0

    try:
        from .boot_safe import heal_bcd_store

        out["actions"].extend(heal_bcd_store(prefer_uefi=prefer_uefi))
    except Exception as e:
        out["actions"].append(f"heal_fail:{type(e).__name__}")

    if prefer_uefi:
        c, _enum = _run(["bcdedit", "/enum", "firmware"], timeout=60)
        out["actions"].append(f"bcdedit_firmware:{c}")
        if grub.get("found"):
            out["actions"].append("note:keep_grub_firmware_entry")

    # Only unmount ESP we mounted here, or caller letter when asked
    if unmount_target:
        to_drop = []
        if mounted_here:
            to_drop.extend(targets)
        elif target and boot_letter:
            to_drop.append(target)
        for t in to_drop:
            try:
                from .sysreserved import unmount_letter

                unmount_letter(t)
            except Exception:
                pass

    return out


def _write_grub_repair_scripts(grub: dict[str, Any]) -> Path:
    rescue = STATE_DIR / "rescue"
    rescue.mkdir(parents=True, exist_ok=True)
    path = rescue / "GRUB-EFI-Repair.txt"
    vendors = ", ".join(grub.get("vendors") or []) or "(unknown)"
    paths = "\n".join(f"  - {p}" for p in (grub.get("paths") or [])) or "  (scan EFI\\* after remount)"
    text = f"""Win11 Magic Upgrade — GRUB / Linux EFI repair after partition move
==================================================================
Generated (UTC): {datetime.now(timezone.utc).isoformat()}
Detected vendors: {vendors}
Known loaders:
{paths}

Windows side (already attempted by Magic Upgrade)
-------------------------------------------------
• bcdboot rewrote Microsoft Boot Manager on the ESP
• Linux folders under EFI\\ were NOT deleted

If Linux no longer boots
------------------------
1) Boot a Linux live USB (or GParted Live terminal).
2) Mount the ESP, e.g.:
     sudo mkdir -p /mnt/esp
     sudo mount /dev/sdXY /mnt/esp   # FAT32 ESP
3) Reinstall GRUB to the ESP (example Ubuntu/Debian):
     sudo grub-install --target=x86_64-efi --efi-directory=/mnt/esp --bootloader-id=ubuntu
     sudo update-grub
4) Or from an installed system chroot after mounting root + ESP.
5) In UEFI firmware setup, ensure the Linux entry still exists; if not,
   use efibootmgr -c -d /dev/sdX -p Y -l '\\\\EFI\\\\ubuntu\\\\shimx64.efi' -L ubuntu

Windows recovery if Windows entry missing
-----------------------------------------
  bcdboot %SystemRoot% /s S: /f UEFI
  (S: = mounted ESP)

Env: MAGIC_GRUB_PRESERVE=0 to skip GRUB detection scripts (not recommended).
"""
    path.write_text(text, encoding="utf-8")
    try:
        desk = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Win11MagicUpgrade-GRUB-Repair.txt"
        if desk.parent.exists():
            shutil.copy2(path, desk)
    except Exception:
        pass
    log(f"GRUB repair guide: {path}", "OK")
    return path


def write_gparted_smart_scripts(
    plan: dict[str, Any],
    layout: dict[str, Any],
    *,
    reason: str = "",
) -> dict[str, Any]:
    """
    Stage human + scripted GParted/qparted/parted procedures for move/grow
    when Windows cannot relocate partitions. Never auto-executes them.
    """
    rescue = STATE_DIR / "rescue"
    rescue.mkdir(parents=True, exist_ok=True)
    disk = layout.get("disk")
    boot = plan.get("boot") or _find_boot_partition(layout, True)
    target_mb = int(plan.get("target_boot_mb") or TARGET_BOOT_MB)
    style = layout.get("style") or "GPT"

    guide = rescue / "GParted-Smart-Move-Grow.txt"
    sh = rescue / "gparted-smart-plan.sh"
    cmd = rescue / "repair_boot_after_gparted.cmd"

    parts_txt = []
    for p in layout.get("partitions") or []:
        parts_txt.append(
            f"  #{p['number']:>2} offset={p['offset']//(1024*1024):>8}MB "
            f"size={p['size_mb']:.0f}MB fs={p.get('fs') or '-':<5} "
            f"L={p.get('letter') or '-'} esp={p.get('is_esp')} sys={p.get('is_system')}"
        )
    free_txt = []
    for fr in layout.get("free_regions") or []:
        free_txt.append(f"  free @{fr['offset']//(1024*1024)}MB size={fr['size_mb']:.0f}MB")

    guide_text = f"""Win11 Magic Upgrade — SMART GParted / qparted / parted plan
===========================================================
UTC: {datetime.now(timezone.utc).isoformat()}
Reason: {reason or plan.get('strategy')}
System disk #: {disk}
Partition style: {style}
Strategy: {plan.get('strategy')}
Target boot size: ~{target_mb} MB

Current map
-----------
{chr(10).join(parts_txt) or '  (unavailable)'}
Free regions:
{chr(10).join(free_txt) or '  (none mapped)'}

Goals (Partition-Magic style, SAFE order)
-----------------------------------------
1) Do NOT delete the Windows (C:) NTFS partition.
2) Move intervening partitions ONLY if required so free space becomes
   ADJACENT to the EFI / System Reserved partition.
3) Grow the boot partition to ~{target_mb} MB (or create a new FAT32 ESP
   with flags boot,esp if grow/move is too risky).
4) Apply, reboot to Windows, then run repair_boot_after_gparted.cmd
   (or: Win11MagicUpgrade.exe --cli --srp).

Recommended GParted GUI clicks
------------------------------
• Select disk #{disk if disk is not None else '?'} (verify size matches Windows).
• If free space is after C: but ESP is before C:: either
    A) Shrink C: from the LEFT (difficult) — prefer creating NEW ESP in free space, OR
    B) Create new FAT32 ~{target_mb} MB, flags: boot + esp; leave old ESP.
• If free space is elsewhere: Move the partition(s) BETWEEN boot and free
  space carefully (right-click Move/Resize). Then Resize boot to grow into free.
• NTFS moves: allow gparted to run check; do not interrupt power.

qparted / KDE Partition Manager
-------------------------------
Same order: shrink/move data → grow or create ESP → Apply.

After Windows boots
-------------------
1) Run:  %LOCALAPPDATA%\\Win11MagicUpgrade\\rescue\\repair_boot_after_gparted.cmd
2) Or:   Win11MagicUpgrade.exe --cli --srp
3) If dual-boot Linux: see GRUB-EFI-Repair.txt

NEVER auto-run: Magic Upgrade only stages this plan + ISO; you boot the USB.
"""
    guide.write_text(guide_text, encoding="utf-8")

    # Shell hints for parted (informational — user runs in GParted terminal)
    boot_n = boot["number"] if boot else "N"
    sh_text = f"""#!/bin/sh
# INFORMATIONAL ONLY — review before running inside GParted Live terminal.
# Disk: /dev/sdX must match Windows disk #{disk}
# DO NOT paste blindly. Confirm with: sudo parted -l
set -e
DISK="${{1:-/dev/sdX}}"
echo "Target disk: $DISK (Windows disk #{disk})"
sudo parted -s "$DISK" unit MiB print
echo "Plan: grow or create ESP to ~{target_mb}MiB (boot partition was #{boot_n})"
echo "Example create ESP at end of free space (EDIT numbers first):"
echo "  sudo parted -s $DISK mkpart ESP fat32 STARTMiB ENDMiB"
echo "  sudo parted -s $DISK set N esp on"
echo "  sudo parted -s $DISK set N boot on"
echo "Then reboot to Windows and run repair_boot_after_gparted.cmd"
"""
    sh.write_text(sh_text, encoding="utf-8")

    cmd_text = r"""@echo off
REM Repair Windows + EFI boot after GParted / qparted move/grow
setlocal
echo Win11 Magic Upgrade - post-GParted boot repair
echo.
where Win11MagicUpgrade.exe >nul 2>&1
if %ERRORLEVEL%==0 (
  Win11MagicUpgrade.exe --cli --srp
  goto :verify
)
REM Fallback: bcdboot to default system partition
bcdboot %SystemRoot% /f UEFI
if errorlevel 1 bcdboot %SystemRoot% /f ALL
bcdedit /enum {bootmgr}
:verify
echo.
echo If Linux dual-boot fails, open Desktop Win11MagicUpgrade-GRUB-Repair.txt
pause
"""
    cmd.write_text(cmd_text, encoding="ascii", errors="replace")

    try:
        desk = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Win11MagicUpgrade-GParted-Smart.txt"
        if desk.parent.exists():
            shutil.copy2(guide, desk)
    except Exception:
        pass

    out = {
        "guide": str(guide),
        "shell": str(sh),
        "repair_cmd": str(cmd),
    }
    log(f"GParted smart plan staged: {guide}", "OK")
    return out


def finish_partition_verification(
    *,
    prefer_uefi: bool = True,
    system_disk: int | None = None,
    expect_expanded: bool = False,
) -> dict[str, Any]:
    """End-of-job scorecard: layout, ESP size, BCD, boot files, GRUB, deep boot."""
    result: dict[str, Any] = {
        "ok": False,
        "score": 0,
        "max_score": 10,
        "checks": [],
        "issues": [],
        "warnings": [],
    }

    def _check(name: str, ok: bool, weight: int = 1, warn: bool = False) -> None:
        result["checks"].append({"name": name, "ok": ok})
        if ok:
            result["score"] += weight
        elif warn:
            result["warnings"].append(name)
        else:
            result["issues"].append(name)

    layout = map_system_disk(system_disk)
    _check("layout_mapped", bool(layout.get("ok")), 1)
    boot = _find_boot_partition(layout, prefer_uefi) if layout.get("ok") else None
    _check("boot_partition_found", boot is not None, 2)
    if boot:
        _check("boot_size_ge_260mb", boot["size_mb"] >= MIN_BOOT_MB, 2)
        if expect_expanded:
            _check("boot_size_ge_400mb", boot["size_mb"] >= 400, 1, warn=True)
        _check("boot_free_ge_40mb", boot.get("free_mb", 0) >= 40, 1, warn=True)

    # ESP files / BCD via postflight + deep
    try:
        from .boot_safe import postflight_boot_edit

        pf = postflight_boot_edit(expect_uefi=prefer_uefi, system_disk=system_disk)
        result["postflight"] = {"ok": pf.get("ok"), "issues": pf.get("issues")}
        _check("postflight_ok", bool(pf.get("ok")), 2)
    except Exception as e:
        result["warnings"].append(f"postflight:{type(e).__name__}")

    try:
        from .boot_emergency import deep_boot_verification

        deep = deep_boot_verification(prefer_uefi=prefer_uefi)
        result["deep"] = {
            "ok": deep.get("ok"),
            "score": deep.get("score"),
            "issues": deep.get("issues"),
        }
        _check("deep_boot_ok", bool(deep.get("ok")), 2)
    except Exception as e:
        result["warnings"].append(f"deep:{type(e).__name__}")

    grub = detect_linux_efi()
    result["grub"] = grub
    if grub.get("found"):
        _check("grub_efi_present", True, 0)  # informational
        result["warnings"].append("dual_boot_grub_verify_manually")

    result["ok"] = len(result["issues"]) == 0 and result["score"] >= 6
    result["layout_summary"] = {
        "disk": layout.get("disk"),
        "style": layout.get("style"),
        "boot_mb": boot["size_mb"] if boot else None,
        "strategy_hint": None,
    }
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "partition-smart-finish.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    log(
        f"Smart partition finish: ok={result['ok']} score={result['score']}/{result['max_score']} "
        f"issues={result['issues']}",
        "OK" if result["ok"] else "WARN",
    )
    return result


def create_boot_in_unallocated(
    *,
    disk: int,
    size_mb: int,
    prefer_uefi: bool = True,
    partition_style: str | None = None,
) -> str | None:
    """
    Create a new ESP/system partition in existing unallocated space (no shrink).
    Used after smart shrink of C: or a data volume.
    partition_style: 'GPT' / 'MBR' — if MBR, never use 'create partition efi'.
    """
    from .diskpart_safe import ensure_select_disk, free_letter, run_diskpart

    disk_n = _valid_disk(disk)
    size = _valid_mb(size_mb, minimum=100, maximum=2048)
    if disk_n is None or size is None:
        log(f"create_boot_in_unallocated bad params disk={disk!r} size_mb={size_mb!r}", "ERROR")
        return None

    letter = free_letter()
    if not letter:
        log("create_boot_in_unallocated: no free drive letter", "ERROR")
        return None
    letter = _norm_letter(letter)
    if not letter:
        return None

    ok_sel, _ = ensure_select_disk(int(disk_n))
    if not ok_sel:
        return None

    style = (partition_style or "").strip().upper()
    use_efi = bool(prefer_uefi) and style != "MBR"
    if prefer_uefi and style == "MBR":
        log("Disk is MBR — creating primary system partition (not EFI)", "INFO")

    if use_efi:
        script = (
            f"select disk {int(disk_n)}\n"
            f"create partition efi size={int(size)}\n"
            "format fs=fat32 quick label=ESP\n"
            f"assign letter={letter}\n"
            "exit\n"
        )
    else:
        script = (
            f"select disk {int(disk_n)}\n"
            f"create partition primary size={int(size)}\n"
            "format fs=ntfs quick label=System\n"
            f"assign letter={letter}\n"
            "exit\n"
        )
    ok, out = run_diskpart(script)
    log((out or "")[-300:])
    if not ok:
        return None
    if out and re.search(r"error|failed|denied|échec|echec|erreur", out, re.I):
        if not re.search(r"successfully|r[eé]ussi|termin[eé]", out, re.I):
            return None
    root = f"{letter}:"
    if not Path(root + "\\").exists():
        return None
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    mode = "UEFI" if use_efi else "BIOS"
    if bcdboot.is_file():
        c, bout = _run([str(bcdboot), sys_root, "/s", root, "/f", mode], timeout=180)
        if c != 0:
            c2, bout2 = _run([str(bcdboot), sys_root, "/s", root, "/f", "ALL"], timeout=180)
            if c2 != 0:
                log(
                    f"bcdboot failed on unallocated-created boot partition: {(bout2 or bout or '')[:160]}",
                    "WARN",
                )
                return None
    else:
        log("bcdboot.exe missing — partition created but not bootable yet", "WARN")
        return None
    log(f"Boot partition created in unallocated space at {root} (mode={mode})", "OK")
    return root


def run_smart_partition_magic(
    *,
    system_disk: int | None = None,
    prefer_uefi: bool = True,
    target_mb: int = TARGET_BOOT_MB,
) -> dict[str, Any]:
    """
    Full intelligent resize/move plan + native execution + boot fix + finish checks.
    Falls back to staging GParted automation when move is required.
    """
    summary: dict[str, Any] = {
        "ok": False,
        "enabled": _enabled(),
        "actions": [],
        "plan": None,
        "layout": None,
        "boot_fix": None,
        "fallback": None,
        "finish": None,
    }
    if not _enabled():
        summary["actions"].append("smart_partition_disabled")
        return summary

    log("Smart partition planner (Partition-Magic style, Windows-safe)", "STEP")
    layout = map_system_disk(system_disk)
    summary["layout"] = {
        "ok": layout.get("ok"),
        "disk": layout.get("disk"),
        "style": layout.get("style"),
        "n_parts": len(layout.get("partitions") or []),
        "n_free": len(layout.get("free_regions") or []),
    }
    if not layout.get("ok"):
        summary["actions"].append("layout_map_failed")
        return summary

    disk = _valid_disk(layout.get("disk"))
    if disk is None:
        summary["actions"].append("bad_layout_disk")
        return summary
    part_style = str(layout.get("style") or "")
    # UEFI create only makes sense on GPT
    prefer_uefi_eff = bool(prefer_uefi) and part_style.upper() != "MBR"
    if prefer_uefi and part_style.upper() == "MBR":
        summary["actions"].append("prefer_uefi_forced_off_mbr_disk")
    target = _valid_mb(target_mb, minimum=100, maximum=2048) or TARGET_BOOT_MB

    plan = plan_smart_layout(layout, prefer_uefi=prefer_uefi_eff, target_mb=target)
    # OEM quirks (Acer/Asus/Toshiba/...): prefer new ESP; block if HDD password locked
    try:
        from .oem_adapt import apply_oem_to_partition_plan, get_oem_profile

        oem = get_oem_profile()
        plan = apply_oem_to_partition_plan(plan, oem)
        summary["oem"] = {
            "family": oem.family,
            "prefer_new_esp": oem.prefer_new_esp_over_grow,
            "msdm": oem.msdm_present,
        }
        if plan.get("strategy") == "blocked_encryption":
            summary["actions"].append("blocked_oem_encryption")
            summary["ok"] = False
            summary["plan"] = {
                "strategy": plan.get("strategy"),
                "reasons": plan.get("reasons"),
                "steps": plan.get("steps"),
                "gparted": plan.get("gparted"),
            }
            return summary
    except Exception as e:
        summary["actions"].append(f"oem_plan_skip:{type(e).__name__}")

    summary["plan"] = {
        "strategy": plan.get("strategy"),
        "reasons": plan.get("reasons"),
        "steps": plan.get("steps"),
        "gparted": plan.get("gparted"),
        "prefer_uefi_eff": prefer_uefi_eff,
        "partition_style": part_style,
    }
    summary["actions"].append(f"plan:{plan.get('strategy')}")

    try:
        if plan["strategy"] == "noop_space_ok":
            summary["ok"] = True
            summary["actions"].append("noop")

        elif plan["strategy"] == "extend_boot":
            step = (plan.get("steps") or [{}])[0]
            boot = plan.get("boot")
            add_mb = _valid_mb(step.get("add_mb"), minimum=16, maximum=4096)
            part_n = _valid_part((boot or {}).get("number"))
            if boot and add_mb and part_n:
                ext = try_extend_boot_partition(
                    disk=disk, part_number=part_n, add_mb=add_mb
                )
                summary["extend"] = ext
                summary["actions"].extend(ext.get("actions") or [])
                if ext.get("ok"):
                    summary["ok"] = True
            else:
                summary["actions"].append("extend_skipped_bad_step_params")

        elif plan["strategy"] in ("shrink_c_then_create", "shrink_data_then_create", "create_in_free"):
            shrunk = plan["strategy"] == "create_in_free"
            for step in plan.get("steps") or []:
                op = step.get("op")
                if op == "shrink":
                    letter = _norm_letter(step.get("letter"))
                    mb = _valid_mb(step.get("mb"), minimum=100, maximum=2048)
                    if not letter or not mb:
                        summary["actions"].append(f"shrink_bad_params:{step!r}")
                        break
                    if letter != "C" and not _allow_data_shrink():
                        summary["actions"].append("data_shrink_blocked")
                        break
                    ok = shrink_volume_letter(letter, mb, max(100, mb - 80))
                    summary["actions"].append(f"shrink_{letter}:{ok}")
                    if not ok:
                        break
                    shrunk = True
                elif op == "create_boot":
                    size = _valid_mb(step.get("size_mb"), minimum=100, maximum=2048) or target
                    root = None
                    if shrunk or plan["strategy"] == "create_in_free":
                        root = create_boot_in_unallocated(
                            disk=disk,
                            size_mb=size,
                            prefer_uefi=prefer_uefi_eff,
                            partition_style=part_style,
                        )
                    if not root and not shrunk:
                        # Legacy path only when we did NOT already shrink (avoid double-shrink)
                        from .sysreserved import (
                            create_larger_esp,
                            create_larger_system_reserved_mbr,
                        )

                        if prefer_uefi_eff:
                            root = create_larger_esp(size, system_disk=disk)
                            if not root:
                                root = create_larger_system_reserved_mbr(size, system_disk=disk)
                        else:
                            root = create_larger_system_reserved_mbr(size, system_disk=disk)
                            if not root and part_style.upper() == "GPT":
                                root = create_larger_esp(size, system_disk=disk)
                    elif not root and shrunk:
                        # Already shrunk: try opposite partition type in same free space
                        summary["actions"].append("create_unalloc_retry_flipped")
                        root = create_boot_in_unallocated(
                            disk=disk,
                            size_mb=size,
                            prefer_uefi=not prefer_uefi_eff,
                            partition_style=part_style,
                        )
                    summary["actions"].append(f"create_boot:{bool(root)}")
                    if root:
                        summary["ok"] = True
                        summary["boot_letter"] = root
                        # Keep letter mounted for fix_boot_for_new_layout — unmount after

        elif plan["strategy"] == "gparted_move":
            scripts = write_gparted_smart_scripts(
                plan, layout, reason="free_space_not_adjacent"
            )
            summary["fallback"] = scripts
            summary["actions"].append("gparted_smart_scripts")
            # Stage ISO + classic guide without re-entering full smart planner loops
            try:
                from .boot_safe import download_gparted_iso, write_gparted_rescue_guide

                guide = write_gparted_rescue_guide(
                    reason="smart_partition_needs_move",
                    system_disk=disk,
                    mode="EFI" if prefer_uefi_eff else "SystemReserved",
                )
                iso = download_gparted_iso()
                summary["fallback_media"] = {
                    "guide": str(guide),
                    "iso": str(iso) if iso else None,
                    "smart_gparted": scripts,
                }
            except Exception as e:
                summary["actions"].append(f"fallback_media:{type(e).__name__}")

        else:
            summary["actions"].append("defer_legacy_expand")
    except Exception as e:
        log(f"Smart partition execute error: {e}", "ERROR")
        summary["actions"].append(f"exec_error:{type(e).__name__}")
        summary["ok"] = False

    # Boot fix whenever we mutated successfully — letter must still be mounted if set
    if summary.get("ok") and plan.get("strategy") not in ("noop_space_ok", "gparted_move", "none"):
        preserve = os.environ.get("MAGIC_GRUB_PRESERVE", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        bl = summary.get("boot_letter")
        summary["boot_fix"] = fix_boot_for_new_layout(
            prefer_uefi=prefer_uefi_eff,
            boot_letter=bl,
            preserve_grub=preserve,
            unmount_target=True,
        )
        summary["actions"].extend(summary["boot_fix"].get("actions") or [])
        if summary["boot_fix"].get("ok") is False:
            summary["actions"].append("boot_fix_soft_fail")
        summary["boot_letter"] = None  # unmounted by fix_boot when provided
    elif summary.get("boot_letter"):
        # Failed path: still unmount temporary letter
        try:
            from .sysreserved import unmount_letter

            unmount_letter(str(summary["boot_letter"]))
        except Exception:
            pass
        summary["boot_letter"] = None

    # Always write GParted smart scripts alongside when not fully ok
    if not summary.get("ok") and plan.get("strategy") != "noop_space_ok":
        try:
            summary["gparted_plan"] = write_gparted_smart_scripts(
                plan, layout, reason="smart_native_incomplete"
            )
        except Exception:
            pass

    # Finish verification — hard-fail only on layout/boot-size regressions
    try:
        summary["finish"] = finish_partition_verification(
            prefer_uefi=prefer_uefi_eff,
            system_disk=disk,
            expect_expanded=bool(
                summary.get("ok") and plan.get("strategy") not in ("noop_space_ok", None)
            ),
        )
        issues = set(summary["finish"].get("issues") or [])
        hard = {"layout_mapped", "boot_partition_found", "boot_size_ge_260mb"}
        if summary.get("ok") and issues.intersection(hard):
            summary["ok"] = False
            summary["actions"].append("finish_checks_failed:" + ",".join(sorted(issues & hard)))
        elif summary.get("ok") and summary["finish"] and not summary["finish"].get("ok"):
            summary["actions"].append("finish_checks_warn")
    except Exception as e:
        summary["actions"].append(f"finish_error:{type(e).__name__}")

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "partition-smart-last.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass

    log(
        f"Smart partition done: ok={summary.get('ok')} strategy={plan.get('strategy')} "
        f"actions={len(summary.get('actions') or [])}",
        "OK" if summary.get("ok") else "WARN",
    )
    return summary
