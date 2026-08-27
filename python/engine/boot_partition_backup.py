"""
Boot partition backup / restore / dynamic regeneration.

Captures ESP + System Reserved (when present):
  - full file tree (not only critical loaders)
  - BCD export sidecar
  - partition metadata (disk#, number, size, GPT type, offset)
  - optional WIM image via DISM when partition is small enough

On failure: restore files → apply WIM → dynamically recreate partition from
metadata (PowerShell Storage / diskpart helpers) → bcdboot → verify.

Never wipes C:. Never auto-deletes the only remaining ESP without a backup.
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

from .logutil import STATE_DIR, log

BACKUP_ROOT = STATE_DIR / "partition-backups"
KEEP_GENERATIONS = 3
# Capture WIM only if used size under this (bytes) — ESP is typically < 512 MB used
MAX_WIM_USED_BYTES = 900 * 1024 * 1024
GPT_ESP_TYPE = "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}"


def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
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


def _ps(script: str, timeout: int = 300) -> tuple[int, str]:
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


def _prune_old_generations(root: Path, keep: int = KEEP_GENERATIONS) -> None:
    try:
        dirs = sorted(
            [p for p in root.iterdir() if p.is_dir() and re.match(r"^\d{8}T\d{6}Z$", p.name)],
            key=lambda p: p.name,
            reverse=True,
        )
        for old in dirs[keep:]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass


def collect_boot_partition_metadata() -> list[dict[str, Any]]:
    """Enumerate ESP / System / Reserved partitions via Storage (no diskpart)."""
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$parts = Get-Partition | Where-Object {
  $_.GptType -eq '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' -or
  $_.IsSystem -eq $true -or
  $_.Type -match 'System|Reserved' -or
  ($_.FileSystem -eq 'FAT32' -and $_.Size -lt 1GB -and $_.Size -gt 50MB)
}
$out = @()
foreach ($p in $parts) {
  $disk = Get-Disk -Number $p.DiskNumber
  $vol = Get-Volume -Partition $p -EA SilentlyContinue
  $out += [pscustomobject]@{
    DiskNumber = $p.DiskNumber
    PartitionNumber = $p.PartitionNumber
    Size = [int64]$p.Size
    Offset = [int64]$p.Offset
    GptType = [string]$p.GptType
    MbrType = [string]$p.MbrType
    IsSystem = [bool]$p.IsSystem
    IsBoot = [bool]$p.IsBoot
    IsActive = [bool]$p.IsActive
    DriveLetter = if ($p.DriveLetter) { [string]$p.DriveLetter } else { '' }
    PartitionStyle = [string]$disk.PartitionStyle
    FileSystem = if ($vol) { [string]$vol.FileSystem } else { '' }
    SizeRemaining = if ($vol) { [int64]$vol.SizeRemaining } else { 0 }
    Role = if ($p.GptType -eq '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}') { 'ESP' }
           elseif ($p.IsSystem -or $p.Type -match 'Reserved') { 'SRP' }
           else { 'BOOTISH' }
  }
}
$out | ConvertTo-Json -Compress -Depth 4
"""
    code, out = _ps(script, timeout=120)
    if code != 0 or not (out or "").strip():
        return []
    text = (out or "").strip()
    # PowerShell may emit warnings before JSON — find first [ or {
    idx = min([i for i in (text.find("["), text.find("{")) if i >= 0] or [-1])
    if idx < 0:
        return []
    try:
        data = json.loads(text[idx:])
    except Exception:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def _copy_tree_filtered(src: Path, dst: Path) -> tuple[int, int]:
    """Copy files recursively; skip System Volume Information / $RECYCLE.BIN. Returns (files, bytes)."""
    files = 0
    nbytes = 0
    skip_names = {"system volume information", "$recycle.bin", "recycle.bin"}
    for root, dirs, filenames in os.walk(src):
        dirs[:] = [d for d in dirs if d.lower() not in skip_names]
        rel_root = Path(root).relative_to(src)
        target_dir = dst / rel_root
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            sp = Path(root) / name
            tp = target_dir / name
            try:
                shutil.copy2(sp, tp)
                files += 1
                nbytes += sp.stat().st_size
            except Exception:
                continue
    return files, nbytes


def _try_capture_wim(src_letter: str, wim_path: Path, name: str) -> bool:
    dism = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "dism.exe"
    if not dism.exists():
        return False
    # Rough used-size check
    try:
        usage = shutil.disk_usage(f"{src_letter.strip().rstrip(':')}:\\")
        used = usage.total - usage.free
        if used > MAX_WIM_USED_BYTES:
            log(f"Skip WIM capture — used {used // (1024*1024)} MB too large", "INFO")
            return False
    except Exception:
        pass
    wim_path.parent.mkdir(parents=True, exist_ok=True)
    code, out = _run(
        [
            str(dism),
            "/Capture-Image",
            f"/ImageFile:{wim_path}",
            f"/CaptureDir:{src_letter.strip().rstrip(':')}:\\",
            f"/Name:{name}",
            "/Compress:fast",
            "/CheckIntegrity",
        ],
        timeout=600,
    )
    if code == 0 and wim_path.is_file() and wim_path.stat().st_size > 1000:
        log(f"WIM captured: {wim_path.name} ({wim_path.stat().st_size // (1024*1024)} MB)", "OK")
        return True
    log(f"WIM capture failed/skipped: {(out or '')[:160]}", "INFO")
    try:
        wim_path.unlink(missing_ok=True)
    except Exception:
        pass
    return False


def _try_apply_wim(wim_path: Path, dest_letter: str) -> bool:
    dism = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "dism.exe"
    if not dism.exists() or not wim_path.is_file():
        return False
    code, out = _run(
        [
            str(dism),
            "/Apply-Image",
            f"/ImageFile:{wim_path}",
            "/Index:1",
            f"/ApplyDir:{dest_letter.strip().rstrip(':')}:\\",
            "/CheckIntegrity",
        ],
        timeout=600,
    )
    if code == 0:
        log(f"WIM applied onto {dest_letter}", "OK")
        return True
    log(f"WIM apply failed: {(out or '')[:160]}", "WARN")
    return False


def _mount_letter_for_meta(meta: dict[str, Any]) -> str | None:
    """Assign a free letter to the partition described by meta; return 'X:' or None."""
    letter = (meta.get("DriveLetter") or "").strip().rstrip(":")
    if letter and Path(f"{letter}:\\").exists():
        return f"{letter}:"
    disk_n = meta.get("DiskNumber")
    part_n = meta.get("PartitionNumber")
    if disk_n is None or part_n is None:
        return None
    script = f"""
$ErrorActionPreference = 'Stop'
$letter = $null
foreach ($L in 83..90) {{
  $ch = [char]$L
  if (-not (Test-Path ("$ch" + ':\\'))) {{ $letter = "$ch"; break }}
}}
if (-not $letter) {{ throw 'no letter' }}
Add-PartitionAccessPath -DiskNumber {int(disk_n)} -PartitionNumber {int(part_n)} -AccessPath ($letter + ':') -EA Stop
Write-Output ($letter + ':')
"""
    code, out = _ps(script, timeout=90)
    if code != 0:
        return None
    for line in (out or "").splitlines():
        line = line.strip()
        if re.fullmatch(r"[A-Z]:", line):
            return line
    return None


def _unmount_letter_safe(letter: str | None, meta: dict[str, Any] | None = None) -> None:
    if not letter:
        return
    # Only remove if we assigned it (no DriveLetter in meta originally)
    if meta and (meta.get("DriveLetter") or "").strip():
        return
    disk_n = (meta or {}).get("DiskNumber")
    part_n = (meta or {}).get("PartitionNumber")
    L = letter.rstrip(":\\")[:1]
    if disk_n is None or part_n is None:
        try:
            from .sysreserved import unmount_letter

            unmount_letter(letter)
        except Exception:
            pass
        return
    _ps(
        f"Remove-PartitionAccessPath -DiskNumber {int(disk_n)} "
        f"-PartitionNumber {int(part_n)} -AccessPath '{L}:' -EA SilentlyContinue",
        timeout=60,
    )


def backup_boot_partitions(*, system_disk: int | None = None) -> dict[str, Any]:
    """
    Full backup of ESP/SRP partitions + BCD + metadata.
    Stores under %LOCALAPPDATA%\\Win11MagicUpgrade\\partition-backups\\<stamp>\\
    """
    allow = os.environ.get("MAGIC_PARTITION_BACKUP", "1").strip().lower()
    if allow in ("0", "false", "no"):
        return {"ok": False, "actions": ["partition_backup_disabled"]}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    gen = BACKUP_ROOT / stamp
    gen.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "ok": False,
        "stamp": stamp,
        "path": str(gen),
        "partitions": [],
        "actions": [],
        "firmware": _firmware(),
    }
    log(f"BOOT PARTITION BACKUP — generation {stamp}", "STEP")

    # BCD sidecar
    try:
        from .boot_safe import backup_bcd

        bcd = backup_bcd()
        if bcd:
            # Also copy into this generation folder
            dest_bcd = gen / "bcd-export"
            try:
                shutil.copy2(bcd, dest_bcd)
                result["actions"].append("bcd_copied")
                result["bcd"] = str(dest_bcd)
            except Exception:
                result["bcd"] = str(bcd)
                result["actions"].append("bcd_pointer_only")
    except Exception as e:
        result["actions"].append(f"bcd_skip:{type(e).__name__}")

    metas = collect_boot_partition_metadata()
    if system_disk is not None and int(system_disk) >= 0:
        metas = [m for m in metas if int(m.get("DiskNumber", -1)) == int(system_disk)] or metas

    if not metas:
        # Fallback: mount ESP via existing helper and treat as single unknown partition
        try:
            from .sysreserved import mount_esp, unmount_letter

            mounted = mount_esp()
            if mounted:
                metas = [
                    {
                        "DiskNumber": system_disk,
                        "PartitionNumber": None,
                        "DriveLetter": mounted.rstrip(":\\")[:1],
                        "Role": "ESP" if _firmware() == "UEFI" else "SRP",
                        "Size": shutil.disk_usage(f"{mounted.rstrip(':\\')}:\\").total,
                        "PartitionStyle": "Unknown",
                        "GptType": GPT_ESP_TYPE if _firmware() == "UEFI" else "",
                        "_mounted_via_helper": True,
                    }
                ]
                result["actions"].append("meta_via_mount_esp")
        except Exception:
            pass

    (gen / "metadata.json").write_text(
        json.dumps({"utc": stamp, "firmware": result["firmware"], "partitions": metas}, indent=2),
        encoding="utf-8",
    )

    for i, meta in enumerate(metas):
        role = str(meta.get("Role") or f"part{i}")
        part_dir = gen / f"{role}-{i}"
        part_dir.mkdir(parents=True, exist_ok=True)
        (part_dir / "partition.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        letter = None
        assigned = False
        try:
            if meta.get("_mounted_via_helper") and meta.get("DriveLetter"):
                letter = f"{str(meta['DriveLetter']).rstrip(':')}:"
            else:
                letter = _mount_letter_for_meta(meta)
                assigned = bool(letter) and not (meta.get("DriveLetter") or "").strip()
            if not letter:
                result["actions"].append(f"{role}_mount_fail")
                continue

            src = Path(f"{letter.rstrip(':')}:\\")
            files_dir = part_dir / "files"
            files_dir.mkdir(parents=True, exist_ok=True)
            nfiles, nbytes = _copy_tree_filtered(src, files_dir)
            part_info = {
                "role": role,
                "letter": letter,
                "files": nfiles,
                "bytes": nbytes,
                "wim": None,
                "meta": meta,
            }
            # Optional WIM
            wim_path = part_dir / "partition.wim"
            if _try_capture_wim(letter, wim_path, f"Win11Magic-{role}-{stamp}"):
                part_info["wim"] = str(wim_path)
                result["actions"].append(f"{role}_wim")
            result["partitions"].append(part_info)
            result["actions"].append(f"{role}_files:{nfiles}")
            log(f"Backed up {role}: {nfiles} files ({nbytes // 1024} KB)", "OK")
        except Exception as e:
            result["actions"].append(f"{role}_error:{type(e).__name__}")
            log(f"Backup {role} failed: {e}", "WARN")
        finally:
            if assigned and letter:
                _unmount_letter_safe(letter, {**meta, "DriveLetter": ""})
            elif meta.get("_mounted_via_helper") and letter:
                try:
                    from .sysreserved import unmount_letter

                    unmount_letter(letter)
                except Exception:
                    pass

    result["ok"] = any(p.get("files", 0) > 0 for p in result["partitions"]) or bool(result.get("bcd"))
    (gen / "manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    try:
        (BACKUP_ROOT / "LAST.txt").write_text(str(gen), encoding="utf-8")
        # Generations index
        index_path = BACKUP_ROOT / "INDEX.json"
        index: list[str] = []
        if index_path.exists():
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                index = []
        if stamp not in index:
            index.append(stamp)
        index_path.write_text(json.dumps(index[-20:], indent=2), encoding="utf-8")
    except Exception:
        pass
    _prune_old_generations(BACKUP_ROOT, KEEP_GENERATIONS)
    log(f"Partition backup done ok={result['ok']} parts={len(result['partitions'])}", "OK" if result["ok"] else "WARN")
    return result


def last_backup_dir() -> Path | None:
    ptr = BACKUP_ROOT / "LAST.txt"
    if not ptr.exists():
        return None
    p = Path(ptr.read_text(encoding="utf-8").strip())
    return p if p.is_dir() else None


def restore_boot_partition_files(*, generation: Path | None = None) -> dict[str, Any]:
    """Restore full file trees from last (or given) backup onto current ESP/SRP."""
    gen = generation or last_backup_dir()
    out: dict[str, Any] = {"ok": False, "actions": [], "restored_files": 0, "generation": str(gen) if gen else None}
    if not gen:
        out["actions"].append("no_backup")
        return out

    log(f"RESTORE boot partition files from {gen.name}", "STEP")
    # Prefer WIM apply when available, else file tree
    part_dirs = sorted([p for p in gen.iterdir() if p.is_dir() and (p / "partition.json").exists()])
    if not part_dirs:
        out["actions"].append("empty_generation")
        return out

    from .sysreserved import mount_esp, unmount_letter

    mounted = None
    try:
        mounted = mount_esp()
        if not mounted:
            # Try PS mount
            try:
                from .boot_emergency import ps_mount_esp_letter

                mounted = ps_mount_esp_letter()
            except Exception:
                pass
        if not mounted:
            out["actions"].append("esp_unmountable")
            log("Cannot mount ESP for restore", "ERROR")
            return out

        dst = Path(f"{mounted.rstrip(':')}:\\")
        # Prefer matching role: ESP on UEFI, SRP on BIOS — never apply wrong-role WIM onto ESP
        want_role = "ESP" if _firmware() == "UEFI" else "SRP"
        candidates: list[tuple[int, Path, str]] = []
        for pd in part_dirs:
            role = "BOOTISH"
            try:
                role = str(json.loads((pd / "partition.json").read_text(encoding="utf-8")).get("Role") or "BOOTISH")
            except Exception:
                pass
            files_root = pd / "files"
            wim = pd / "partition.wim"
            count = sum(1 for _ in files_root.rglob("*") if _.is_file()) if files_root.is_dir() else 0
            if wim.is_file():
                count += 100000
            # Strongly prefer matching role
            score = count + (1_000_000 if role == want_role else 0)
            # Deprioritize obvious cross-role (SRP WIM onto UEFI ESP)
            if want_role == "ESP" and role == "SRP":
                score -= 500_000
            if want_role == "SRP" and role == "ESP":
                score -= 500_000
            candidates.append((score, pd, role))
        candidates.sort(key=lambda t: t[0], reverse=True)
        best = candidates[0][1] if candidates and candidates[0][0] > 0 else None
        if best:
            out["actions"].append(f"restore_role:{candidates[0][2]}")
        if not best:
            out["actions"].append("no_part_payload")
            return out

        wim = best / "partition.wim"
        applied = False
        if wim.is_file():
            applied = _try_apply_wim(wim, mounted)
            if applied:
                out["actions"].append("wim_applied")
        if not applied:
            files_root = best / "files"
            if files_root.is_dir():
                n, b = _copy_tree_filtered(files_root, dst)
                out["restored_files"] = n
                out["actions"].append(f"files_restored:{n}")
                applied = n > 0
        # Also restore BCD export if present
        bcd = gen / "bcd-export"
        if bcd.exists():
            code, o = _run(["bcdedit", "/import", str(bcd)], timeout=60)
            out["actions"].append(f"bcd_import:{code}")
            if code == 0:
                applied = True
        out["ok"] = applied
        log(f"Partition restore ok={out['ok']} actions={out['actions'][-4:]}", "OK" if out["ok"] else "WARN")
    finally:
        if mounted:
            try:
                unmount_letter(mounted)
            except Exception:
                pass
    return out


def dynamic_regenerate_boot_partition(
    *,
    prefer_uefi: bool | None = None,
    system_disk: int | None = None,
    size_mb: int | None = None,
) -> dict[str, Any]:
    """
    Dynamically recreate a boot partition from the last backup metadata,
    restore contents (WIM/files), then bcdboot.
    Used when ESP is missing, corrupt, or unmountable after a failed expand.
    """
    from .boot_safe import rewrite_boot_files_from_windows
    from .sysreserved import TARGET_ESP_MB

    uefi = prefer_uefi if prefer_uefi is not None else (_firmware() == "UEFI")
    out: dict[str, Any] = {"ok": False, "actions": [], "letter": None}
    log("DYNAMIC REGENERATE boot partition from backup metadata", "STEP")

    gen = last_backup_dir()
    meta_list: list[dict[str, Any]] = []
    target_size = (size_mb or TARGET_ESP_MB) * 1024 * 1024
    if gen and (gen / "metadata.json").exists():
        try:
            blob = json.loads((gen / "metadata.json").read_text(encoding="utf-8"))
            meta_list = blob.get("partitions") or []
        except Exception:
            pass
    # Prefer ESP role
    meta = None
    for m in meta_list:
        if str(m.get("Role")) == "ESP":
            meta = m
            break
    if meta is None and meta_list:
        meta = meta_list[0]
    if meta and meta.get("Size"):
        try:
            target_size = max(100 * 1024 * 1024, min(int(meta["Size"]), 1024 * 1024 * 1024))
        except Exception:
            pass

    disk_n = system_disk
    if disk_n is None and meta and meta.get("DiskNumber") is not None:
        disk_n = int(meta["DiskNumber"])
    if disk_n is None:
        try:
            from .diskpart_safe import get_system_disk_number

            disk_n = get_system_disk_number()
        except Exception:
            disk_n = None

    # 1) Try restore onto existing ESP first
    restored = restore_boot_partition_files(generation=gen)
    out["actions"].extend(restored.get("actions") or [])
    if restored.get("ok"):
        if rewrite_boot_files_from_windows(prefer_uefi=uefi):
            out["actions"].append("bcdboot_after_restore")
        out["ok"] = True
        out["mode"] = "restore_existing"
        return out

    # Refuse blank create without a backup generation (safety)
    if not gen:
        out["actions"].append("regenerate_refused_no_backup")
        log("Dynamic regenerate refused — no partition backup available", "ERROR")
        return out

    # Session cap on new ESP creation
    try:
        cap = BACKUP_ROOT / "regen-create-count.txt"
        ncreate = int(cap.read_text(encoding="utf-8").strip() or "0") if cap.exists() else 0
        if ncreate >= 2:
            out["actions"].append("regenerate_session_cap")
            log("Dynamic regenerate create skipped — session cap", "WARN")
            return out
    except Exception:
        ncreate = 0

    # 2) Create new partition WITHOUT bcdboot first (apply payload, then bcdboot)
    out["actions"].append("need_new_partition")
    letter = None
    try:
        from .boot_emergency import ps_storage_create_esp

        ps = ps_storage_create_esp(
            size_mb=max(100, target_size // (1024 * 1024)),
            system_disk=disk_n,
            prefer_uefi=uefi,
            run_bcdboot=False,
        )
        out["actions"].extend(ps.get("actions") or [])
        if ps.get("created") and ps.get("letter"):
            letter = f"{ps['letter']}:"
            out["letter"] = letter
            out["mode"] = "ps_storage_new"
    except Exception as e:
        out["actions"].append(f"ps_storage_fail:{type(e).__name__}")

    # 3) diskpart path via sysreserved if PS failed
    if not letter:
        try:
            from .sysreserved import create_larger_esp, create_larger_system_reserved_mbr

            if uefi:
                root = create_larger_esp(max(100, target_size // (1024 * 1024)), system_disk=disk_n)
            else:
                root = create_larger_system_reserved_mbr(
                    max(100, target_size // (1024 * 1024)), system_disk=disk_n
                )
            if root:
                letter = root if root.endswith(":") else f"{root.rstrip(':')}:"
                out["letter"] = letter
                out["mode"] = "diskpart_new"
                out["actions"].append("diskpart_create_ok")
        except Exception as e:
            out["actions"].append(f"diskpart_create_fail:{type(e).__name__}")

    if not letter:
        out["actions"].append("regenerate_create_failed")
        log("Dynamic regenerate: could not create boot partition", "ERROR")
        return out

    try:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        (BACKUP_ROOT / "regen-create-count.txt").write_text(str(ncreate + 1), encoding="utf-8")
    except Exception:
        pass

    # 4) Apply backup payload onto new partition
    if gen:
        # Find best WIM/files
        part_dirs = sorted([p for p in gen.iterdir() if p.is_dir() and (p / "partition.json").exists()])
        applied = False
        for pd in part_dirs:
            wim = pd / "partition.wim"
            if wim.is_file() and _try_apply_wim(wim, letter):
                out["actions"].append("wim_on_new")
                applied = True
                break
        if not applied:
            for pd in part_dirs:
                files_root = pd / "files"
                if files_root.is_dir():
                    n, _b = _copy_tree_filtered(files_root, Path(f"{letter.rstrip(':')}:\\"))
                    if n:
                        out["actions"].append(f"files_on_new:{n}")
                        applied = True
                        break
        # BCD import
        bcd = gen / "bcd-export"
        if bcd.exists():
            c, _o = _run(["bcdedit", "/import", str(bcd)], timeout=60)
            out["actions"].append(f"bcd_import_new:{c}")

    # 5) Always re-anchor with bcdboot
    if rewrite_boot_files_from_windows(prefer_uefi=uefi):
        out["actions"].append("bcdboot_final")
        out["ok"] = True
    else:
        # Direct bcdboot to letter
        sys_root = os.environ.get("SystemRoot", r"C:\Windows")
        bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
        if bcdboot.exists():
            mode = "UEFI" if uefi else "BIOS"
            c, o = _run([str(bcdboot), sys_root, "/s", letter, "/f", mode], timeout=180)
            out["actions"].append(f"bcdboot_s_{mode}:{c}")
            out["ok"] = c == 0 or "successfully" in (o or "").lower()

    # Persist regenerate status
    try:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        (BACKUP_ROOT / "last-regenerate.json").write_text(
            json.dumps(out, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    log(f"Dynamic regenerate ok={out['ok']} mode={out.get('mode')}", "OK" if out["ok"] else "ERROR")
    return out


def ensure_partition_backup_then_repair(
    *,
    prefer_uefi: bool | None = None,
    system_disk: int | None = None,
    force_backup: bool = False,
) -> dict[str, Any]:
    """
    Orchestrator: backup (if missing/stale) → restore → dynamic regenerate if needed.
    """
    summary: dict[str, Any] = {"actions": [], "backup": None, "restore": None, "regenerate": None}
    last = last_backup_dir()
    need_backup = force_backup or last is None
    if last and not need_backup:
        # Refresh if older than ~no check — always soft-refresh when MAGIC_PARTITION_BACKUP_ALWAYS=1
        always = os.environ.get("MAGIC_PARTITION_BACKUP_ALWAYS", "").strip().lower() in ("1", "true", "yes")
        need_backup = always

    if need_backup:
        summary["backup"] = backup_boot_partitions(system_disk=system_disk)
        summary["actions"].extend(summary["backup"].get("actions") or [])
        summary["actions"].append("backup_done" if summary["backup"].get("ok") else "backup_weak")
    else:
        summary["actions"].append("backup_reuse_last")

    # Restore first
    summary["restore"] = restore_boot_partition_files()
    summary["actions"].extend(summary["restore"].get("actions") or [])

    if not summary["restore"].get("ok"):
        summary["regenerate"] = dynamic_regenerate_boot_partition(
            prefer_uefi=prefer_uefi, system_disk=system_disk
        )
        summary["actions"].extend(summary["regenerate"].get("actions") or [])
        summary["ok"] = bool(summary["regenerate"].get("ok"))
    else:
        # Heal with bcdboot after successful file restore
        try:
            from .boot_safe import rewrite_boot_files_from_windows

            uefi = prefer_uefi if prefer_uefi is not None else (_firmware() == "UEFI")
            bcd_ok = rewrite_boot_files_from_windows(prefer_uefi=uefi)
            if bcd_ok:
                summary["actions"].append("bcdboot_after_restore")
            else:
                summary["actions"].append("bcdboot_after_restore_failed")
            # Files restored counts as partial success; full ok needs bcdboot
            summary["ok"] = bool(bcd_ok)
            summary["files_restored"] = True
        except Exception:
            summary["ok"] = False
            summary["files_restored"] = True
    try:
        (BACKUP_ROOT / "last-repair.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except Exception:
        pass
    return summary
