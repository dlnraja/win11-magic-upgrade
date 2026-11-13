"""Mount the firmware system partition and re-check it before every boot write.

``drive_map`` decides which volume is safe. This module scans Windows when it
is actually running there, mounts only when the volume has no access path, and
removes only the path this process added.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path

from .drive_map import (
    MOUNTVOL_UNSUPPORTED,
    BootDecision,
    DriveMap,
    MapPoller,
    MountPlan,
    VolumeContent,
    VolumeRecord,
    access_chain,
    access_root,
    bcdboot_args,
    classify_firmware,
    drive_letter_of,
    drive_map_from_payload,
    fingerprint,
    format_drive_map,
    is_gpt_esp,
    make_volume,
    normalize_volume_guid_path,
    pick_free_letter,
    plan_mount,
    uefi_supported,
    verify_for_write,
)
from .logutil import STATE_DIR, log

_POLL_SEC = 15.0
_LEDGER: dict[str, tuple[MountPlan, VolumeRecord | None]] = {}
_POLLER: MapPoller | None = None


class ScannerUnavailable(Exception):
    """No Windows disk API on this host. Callers keep their non-Windows behavior."""


class UnsafeTarget(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class GuardError(RuntimeError):
    """A boot write's target moved, disappeared, or is no longer safe."""


def _run(cmd: list[str], input_text: str | None = None, timeout: int = 90) -> tuple[int, str]:
    from .proc_guard import run_bounded

    result = run_bounded(cmd, timeout=timeout, input_text=input_text)
    return result.code, result.output or result.reason


def _os_arch() -> str:
    arch = (os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    wow = (os.environ.get("PROCESSOR_ARCHITEW6432") or "").upper()
    if arch == "ARM64" or wow == "ARM64":
        return "arm64"
    if arch in ("AMD64",) or wow in ("AMD64",):
        return "x64"
    if arch == "X86":
        return "x86"
    return "x64"


def _cpu64() -> bool:
    arch = (os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    wow = (os.environ.get("PROCESSOR_ARCHITEW6432") or "").upper()
    if arch in ("AMD64", "ARM64") or wow in ("AMD64", "ARM64"):
        return True
    if arch == "X86" and not wow:
        return False
    return True


def _secure_boot_hint() -> str:
    if os.name != "nt":
        return "unknown"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\SecureBoot\State",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "UEFISecureBootEnabled")
        return "on" if int(value) == 1 else "off"
    except Exception:
        return "unknown"


def _firmware_kind() -> str | None:
    """GetFirmwareType / PEFirmwareType. 2 is UEFI, 1 is BIOS."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        kind = ctypes.c_uint(0)
        if ctypes.windll.kernel32.GetFirmwareType(ctypes.byref(kind)):
            return "UEFI" if kind.value == 2 else "BIOS"
    except Exception:
        return None
    return None


def _firmware_env_present() -> bool | None:
    """GetFirmwareEnvironmentVariable exists only when this boot is UEFI.

    ERROR_INVALID_FUNCTION (1) means legacy BIOS. Any other error means the
    firmware API is present.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.SetLastError(0)
        buffer = ctypes.create_unicode_buffer(8)
        kernel.GetFirmwareEnvironmentVariableW(
            "",
            "{00000000-0000-0000-0000-000000000000}",
            buffer,
            8,
        )
        error = int(kernel.GetLastError())
        if error == 1:
            return False
        return True
    except Exception:
        return None


def _bcd_loader_kind() -> str:
    """'efi' or 'exe' from the running Windows loader, else ''."""
    code, out = _run(["bcdedit", "/enum", "{current}"], timeout=30)
    text = (out or "").lower()
    if code not in (0, 1) and "winload" not in text:
        return ""
    if "winload.efi" in text:
        return "efi"
    if "winload.exe" in text:
        return "exe"
    return ""


def detect_firmware_mode() -> tuple[str, bool]:
    """Return (BIOS|UEFI|unknown, uefi_capable) before any mountvol call."""
    kind = _firmware_kind()
    env_present = _firmware_env_present()
    loader = _bcd_loader_kind()
    mode = classify_firmware(firmware_type=kind, env_var_present=env_present, bcd_loader=loader)
    capable = uefi_supported(firmware_type=kind, env_var_present=env_present, bcd_loader=loader)
    return mode, capable


def _folder() -> Path:
    path = STATE_DIR / "esp-mount"
    return path


def _interval() -> float:
    raw = (os.environ.get("MAGIC_DRIVE_MAP_POLL_SEC") or "").strip()
    try:
        value = float(raw) if raw else _POLL_SEC
    except ValueError:
        value = _POLL_SEC
    return value if value > 0 else _POLL_SEC


def _ledger_key(path: str) -> str:
    root = access_root(path) or path.strip()
    return root.rstrip("\\").upper()


def remember_mount(path: str, plan: MountPlan, volume: VolumeRecord | None = None) -> None:
    _LEDGER[_ledger_key(path)] = (plan, volume)


def _rollback(plan: MountPlan, volume: VolumeRecord | None, io: "VolumeIO") -> None:
    if not plan.mounted_by_us:
        return
    try:
        if plan.action == "assign_letter" and plan.letter:
            io.remove_letter(volume, plan.letter)
        elif plan.action == "mountvol" and plan.letter:
            io.mountvol_delete(plan.letter)
        elif plan.action == "folder":
            io.remove_folder(volume, plan.path)
    except Exception as exc:
        log(f"ESP cleanup failed: {exc}", "WARN")


def release_access(path: str | None) -> bool:
    """Drop a path this process mounted. A pre-existing letter is left alone."""
    if not path:
        return False
    item = _LEDGER.pop(_ledger_key(path), None)
    if item is None:
        return False
    plan, volume = item
    if plan.mounted_by_us:
        _rollback(plan, volume, WindowsVolumeIO())
        log(f"Removed temporary ESP access {plan.path}", "INFO")
    return True


class VolumeIO:
    """Assign / mount / probe. Tests supply a fake; Windows uses the subclass."""

    def assign(self, volume: VolumeRecord | None, letter: str) -> bool:
        raise NotImplementedError

    def mountvol(self, volume: VolumeRecord | None, letter: str) -> bool:
        raise NotImplementedError

    def folder_mount(self, volume: VolumeRecord | None, folder: str) -> bool:
        raise NotImplementedError

    def remove_letter(self, volume: VolumeRecord | None, letter: str) -> None:
        raise NotImplementedError

    def mountvol_delete(self, letter: str) -> None:
        raise NotImplementedError

    def remove_folder(self, volume: VolumeRecord | None, folder: str) -> None:
        raise NotImplementedError

    def probe(self, path: str) -> VolumeContent:
        raise NotImplementedError


def _apply(
    volume: VolumeRecord,
    plan: MountPlan,
    io: VolumeIO,
    *,
    attempts: tuple[MountPlan, ...] | None = None,
) -> MountPlan:
    """Open a volume. mountvol /S is never required and never the only attempt."""
    chain = list(attempts) if attempts else [plan]
    if not attempts:
        extra = [step for step in access_chain(volume, set(), folder_fallback=plan.folder_fallback) if step.action != plan.action]
        # Keep the caller's first step, then the remaining safe methods.
        seen = {plan.action}
        for step in extra:
            if step.action not in seen:
                chain.append(step)
                seen.add(step.action)
    errors: list[str] = []
    for item in chain:
        ok = False
        if item.action in ("reuse_letter", "reuse_folder"):
            ok = True
        elif item.action == "assign_letter" and item.letter:
            ok = bool(io.assign(volume, item.letter))
            if not ok:
                errors.append(f"Add-PartitionAccessPath {item.letter}: failed")
        elif item.action == "diskpart_assign" and item.letter:
            assigner = getattr(io, "diskpart_assign", None)
            ok = bool(assigner(volume, item.letter)) if assigner else bool(io.assign(volume, item.letter))
            if not ok:
                errors.append(f"diskpart assign {item.letter}: failed")
        elif item.action == "mountvol" and item.letter:
            ok = bool(io.mountvol(volume, item.letter))
            if not ok:
                errors.append(MOUNTVOL_UNSUPPORTED)
        elif item.action == "folder":
            ok = bool(io.folder_mount(volume, item.path))
            if not ok:
                errors.append(f"folder mount {item.path}: failed")
        if ok:
            return item
        if item.action not in ("assign_letter", "diskpart_assign", "mountvol", "folder"):
            errors.append(item.action)
    detail = "; ".join(errors) or "no access method succeeded"
    raise UnsafeTarget(detail)


def apply_and_verify(
    volume: VolumeRecord,
    plan: MountPlan,
    io: VolumeIO,
    *,
    allow_empty: bool = False,
    allow_windows_boot: bool = False,
    attempts: tuple[MountPlan, ...] | None = None,
) -> tuple[str, MountPlan]:
    """Mount, check contents, and roll back only a path this call created."""
    applied: MountPlan | None = None
    try:
        applied = _apply(volume, plan, io, attempts=attempts)
        content = io.probe(applied.path)
        ok, reason = verify_for_write(
            volume,
            content,
            allow_empty=allow_empty,
            allow_windows_boot=allow_windows_boot,
        )
        if not ok:
            raise UnsafeTarget(reason)
        return applied.path, applied
    except Exception:
        if applied is not None:
            _rollback(applied, volume, io)
        raise


def _pe_machine(path: Path) -> str | None:
    try:
        data = path.read_bytes()[:4096]
    except OSError:
        return None
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    pe_off = int.from_bytes(data[0x3C:0x40], "little")
    try:
        with path.open("rb") as handle:
            handle.seek(pe_off)
            if handle.read(4) != b"PE\0\0":
                return None
            machine = int.from_bytes(handle.read(2), "little")
    except OSError:
        return None
    if machine == 0x8664:
        return "x64"
    if machine == 0x014C:
        return "x86"
    if machine == 0xAA64:
        return "arm64"
    return None


def _read_text(path: Path, limit: int = 8000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _hive_hint(path: Path) -> tuple[str | None, str | None]:
    """Best-effort build and product name from an offline SOFTWARE hive."""
    try:
        blob = path.read_bytes()[:4_000_000]
    except OSError:
        return None, None
    text = blob.decode("utf-16le", errors="ignore")
    build = None
    edition = None
    found = re.search(r"CurrentBuildNumber.{0,80}?(\d{4,6})", text)
    if found:
        build = found.group(1)
    product = re.search(r"ProductName.{0,80}?([A-Za-z0-9][A-Za-z0-9 .+-]{3,60})", text)
    if product:
        edition = product.group(1).strip()
    return build, edition


def probe_tree(root: str | Path) -> VolumeContent:
    """Read-only content probe. Missing files stay false; nothing is written."""
    base = Path(access_root(str(root)) or root)
    if not base.exists():
        return VolumeContent(readable=False, notes=("missing",))
    efi = base / "EFI"
    ms_boot = efi / "Microsoft" / "Boot"
    boot_dir = efi / "Boot"
    bootmgfw = ms_boot / "bootmgfw.efi"
    bootx64 = boot_dir / "bootx64.efi"
    bootia32 = boot_dir / "bootia32.efi"
    ntos = base / "Windows" / "System32" / "ntoskrnl.exe"
    oc = efi / "OC" / "OpenCore.efi"
    duet = base / "boot"
    linux: list[str] = []
    if efi.is_dir():
        try:
            for child in list(efi.iterdir())[:40]:
                if not child.is_dir():
                    continue
                name = child.name.lower()
                if name in ("microsoft", "boot", "oc"):
                    continue
                if any((child / leaf).is_file() for leaf in ("grubx64.efi", "shimx64.efi", "grub.efi")):
                    linux.append(child.name)
        except OSError:
            pass
    bitness = _pe_machine(bootmgfw) or _pe_machine(ntos) or _pe_machine(bootx64)
    if bootia32.is_file() and not bootx64.is_file() and bitness is None:
        bitness = "x86"
    build, edition = (None, None)
    hive = base / "Windows" / "System32" / "config" / "SOFTWARE"
    if hive.is_file():
        build, edition = _hive_hint(hive)
    media = ""
    for name in ("cversion.ini", "product.ini", "idwbinfo.txt"):
        text = _read_text(base / "sources" / name)
        if text:
            media = text.strip().splitlines()[0][:80]
            match = re.search(r"(\d{5,6}\.\d+|\d{5,6})", text)
            if match:
                media = match.group(1)
            break
    free = total = None
    try:
        usage = __import__("shutil").disk_usage(str(base))
        free, total = usage.free, usage.total
    except OSError:
        pass
    return VolumeContent(
        readable=True,
        has_efi_dir=efi.is_dir(),
        has_microsoft_boot=ms_boot.is_dir(),
        has_bootmgfw=bootmgfw.is_file(),
        has_bootx64=bootx64.is_file(),
        has_bootia32=bootia32.is_file(),
        has_opencore=oc.is_file() or (efi / "OC" / "config.plist").is_file(),
        has_openduet=duet.is_file() and (base / "EFI" / "OC").is_dir(),
        linux_loaders=tuple(linux),
        has_bcd=(ms_boot / "BCD").is_file() or (base / "Boot" / "BCD").is_file(),
        has_bios_bootmgr=(base / "bootmgr").is_file(),
        has_boot_bcd=(base / "Boot" / "BCD").is_file(),
        has_windows=ntos.is_file(),
        product_build=build,
        edition=edition,
        bitness=bitness,
        has_install_wim=(base / "sources" / "install.wim").is_file(),
        has_install_esd=(base / "sources" / "install.esd").is_file(),
        has_boot_wim=(base / "sources" / "boot.wim").is_file(),
        has_setup_exe=(base / "setup.exe").is_file(),
        media_version=media or None,
        has_winre=(base / "Recovery" / "WindowsRE" / "Winre.wim").is_file(),
        free_bytes=free,
        total_bytes=total,
    )


_SCAN_PS = r"""
$ErrorActionPreference = 'Stop'
if (-not (Get-Command Get-Partition -ErrorAction SilentlyContinue)) { exit 2 }
$fw = 'UEFI'
$winLetter = ''
$winGuid = ''
$bootDisk = $null
if ($env:SystemDrive) { $winLetter = $env:SystemDrive.Substring(0,1).ToUpper() }
$disks = @(Get-Disk | ForEach-Object {
  [pscustomobject]@{
    Number = [int]$_.Number
    Style = [string]$_.PartitionStyle
    Bus = [string]$_.BusType
    Serial = [string]$_.SerialNumber
  }
})
$diskByN = @{}
foreach ($d in $disks) { $diskByN[$d.Number] = $d }
$volumes = @()
$parts = @(Get-Partition -ErrorAction SilentlyContinue)
foreach ($p in $parts) {
  $d = $diskByN[[int]$p.DiskNumber]
  $vol = $null
  try { $vol = Get-Volume -Partition $p -ErrorAction SilentlyContinue } catch {}
  $letter = ''
  if ($p.DriveLetter) { $letter = [string]$p.DriveLetter }
  $paths = @()
  if ($p.AccessPaths) { $paths = @($p.AccessPaths | ForEach-Object { [string]$_ }) }
  $guid = ''
  if ($vol -and $vol.UniqueId) { $guid = [string]$vol.UniqueId }
  $bl = 'off'
  if ($letter) {
    try {
      $b = Get-BitLockerVolume -MountPoint ($letter + ':') -ErrorAction SilentlyContinue
      if ($b) {
        if ([string]$b.LockStatus -eq 'Locked') { $bl = 'locked' }
        elseif ([string]$b.ProtectionStatus -eq 'On') { $bl = 'on' }
      }
    } catch {}
  }
  $mbr = $null
  try { if ($null -ne $p.MbrType) { $mbr = [int]$p.MbrType } } catch {}
  if ($p.IsBoot -and $letter -eq $winLetter) { $winGuid = $guid }
  $volumes += [pscustomobject]@{
    disk = [int]$p.DiskNumber
    partition = [int]$p.PartitionNumber
    style = $(if ($d) { [string]$d.Style } else { '' })
    gpt_type = [string]$p.GptType
    mbr_type = $mbr
    type_name = [string]$p.Type
    filesystem = $(if ($vol) { [string]$vol.FileSystem } else { '' })
    label = $(if ($vol) { [string]$vol.FileSystemLabel } else { '' })
    size_bytes = [int64]$p.Size
    letter = $(if ($letter) { $letter } else { $null })
    access_paths = $paths
    is_boot = [bool]$p.IsBoot
    is_system = [bool]$p.IsSystem
    is_active = [bool]$p.IsActive
    bitlocker = $bl
    volume_guid = $guid
    bus = $(if ($d) { [string]$d.Bus } else { 'unknown' }).ToLower()
    serial = $(if ($d) { [string]$d.Serial } else { '' })
    offset = [int64]$p.Offset
  }
}
$espGuid = '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'
$sys = @($parts | Where-Object { $_.GptType -eq $espGuid -and $_.IsSystem })
if (-not $sys) { $sys = @($parts | Where-Object { $_.IsSystem -and -not $_.IsBoot }) }
if ($sys) { $bootDisk = [int]$sys[0].DiskNumber }
elseif ($winLetter) {
  try { $bootDisk = [int](Get-Partition -DriveLetter $winLetter).DiskNumber } catch {}
}
[pscustomobject]@{
  firmware = $fw
  boot_disk = $bootDisk
  windows_letter = $(if ($winLetter) { $winLetter } else { $null })
  windows_volume_guid = $winGuid
  source = 'get-partition'
  volumes = $volumes
} | ConvertTo-Json -Depth 6 -Compress
"""


def _attach_content(drive_map: DriveMap) -> DriveMap:
    updated: list[VolumeRecord] = []
    for vol in drive_map.volumes:
        if vol.content is not None:
            updated.append(vol)
            continue
        root = None
        if vol.letter:
            root = f"{vol.letter}:\\"
        else:
            for path in vol.access_paths:
                if access_root(path):
                    root = access_root(path)
                    break
        if root and Path(root).exists():
            updated.append(replace(vol, content=probe_tree(root)))
        else:
            updated.append(vol)
    return replace(drive_map, volumes=tuple(updated))


def _parse_scan(raw: str) -> DriveMap:
    start = raw.find("{")
    if start < 0:
        raise ScannerUnavailable("drive map JSON missing")
    payload = json.loads(raw[start:])
    drive_map = drive_map_from_payload(payload)
    mode, capable = detect_firmware_mode()
    if mode != "unknown":
        drive_map = replace(drive_map, firmware=mode, uefi_capable=capable)
    else:
        drive_map = replace(drive_map, uefi_capable=capable)
    return _attach_content(drive_map)


def _map_from_diskpart() -> DriveMap:
    from .diskpart_safe import get_system_disk_number, list_volumes

    rows = list_volumes()
    if not rows:
        raise ScannerUnavailable("diskpart returned no volumes")
    try:
        disk = get_system_disk_number()
    except Exception:
        disk = None
    records: list[VolumeRecord] = []
    for row in rows:
        type_name = row.type_col or ""
        lowered = type_name.lower()
        records.append(
            make_volume(
                disk=int(disk) if disk is not None else -1,
                style="",
                filesystem=row.fs or "",
                label=row.label or "",
                letter=row.letter,
                size_bytes=(row.size_mb * 1024 * 1024) if row.size_mb else None,
                type_name=type_name,
                is_system=("system" in lowered or "efi" in lowered or "esp" in lowered),
                bitlocker="off",
            )
        )
    win = (os.environ.get("SystemDrive") or "")[:1].upper() or None
    mode, capable = detect_firmware_mode()
    drive_map = DriveMap(
        volumes=tuple(records),
        firmware=mode if mode != "unknown" else "BIOS",
        boot_disk=disk,
        windows_letter=win,
        source="diskpart",
        uefi_capable=capable,
    )
    return _attach_content(drive_map)


def scan_windows_map() -> DriveMap:
    """Read disks, partitions, BitLocker, and any already mounted contents."""
    if os.name != "nt":
        raise ScannerUnavailable("drive map scan needs Windows")
    code, out = _run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCAN_PS],
        timeout=90,
    )
    if code == 0 and "{" in (out or ""):
        try:
            return _parse_scan(out)
        except Exception as exc:
            log(f"Get-Partition map parse failed, trying diskpart: {exc}", "WARN")
    return _map_from_diskpart()


def _poller() -> MapPoller:
    global _POLLER
    if _POLLER is None:
        _POLLER = MapPoller(
            scan_windows_map,
            interval_s=_interval(),
            os_arch=_os_arch(),
            cpu_64=_cpu64(),
            secure_boot=_secure_boot_hint(),
            folder_fallback=str(_folder()),
        )
    return _POLLER


def prepare_boot_step(step: str) -> BootDecision | None:
    """Re-scan and re-plan before bcdboot, Setup, mbr2gpt, OpenCore, or Secure Boot."""
    try:
        decision = _poller().before_step(step)
    except ScannerUnavailable:
        log(f"{step}: drive-map scanner unavailable on this host", "INFO")
        return None
    except Exception as exc:
        log(f"{step}: drive-map scan failed: {exc}", "WARN")
        return None
    current = _poller().map
    if current is not None:
        log(f"Drive map before {step}:\n{format_drive_map(current)}", "INFO")
    if decision.abort:
        log(f"{step}: {decision.explanation}", "ERROR")
    elif decision.explanation:
        log(f"{step}: {decision.explanation}", "INFO")
    for warning in decision.warnings:
        log(f"{step}: {warning}", "WARN")
    return decision


def poll_if_due(step: str) -> BootDecision | None:
    """During a long operation, pick up letters that Windows reassigned."""
    del step
    try:
        return _poller().poll()
    except ScannerUnavailable:
        return None
    except Exception as exc:
        log(f"drive-map poll failed: {exc}", "WARN")
        return None


def _target_for(decision: BootDecision, role: str) -> VolumeRecord | None:
    if role == "esp":
        return decision.esp
    if role == "srp":
        return decision.srp or decision.esp
    if decision.strategy in ("bios_mbr",) or (
        decision.strategy == "hybrid_mbr_gpt" and decision.bcd_mode == "BIOS"
    ):
        return decision.srp or decision.esp
    return decision.esp or decision.srp


class WindowsVolumeIO(VolumeIO):
    def assign(self, volume: VolumeRecord | None, letter: str) -> bool:
        if volume is None or volume.partition is None or volume.disk < 0:
            return False
        script = (
            f"$p = Get-Partition -DiskNumber {int(volume.disk)} -PartitionNumber {int(volume.partition)} -EA Stop; "
            f"Add-PartitionAccessPath -InputObject $p -AccessPath '{letter.upper()[:1]}:' -EA Stop"
        )
        code, _out = _run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            timeout=60,
        )
        if code == 0 and Path(f"{letter.upper()[:1]}:\\").exists():
            return True
        from .diskpart_safe import assign_letter_to_volume, list_volumes

        for row in list_volumes():
            if row.letter and row.letter.upper() == letter.upper()[:1]:
                return False
        # diskpart volume index is not the partition number; match label/size loosely via letter absence
        for row in list_volumes():
            if row.letter:
                continue
            if volume.label and row.label and volume.label.lower() not in row.label.lower() and row.label.lower() not in volume.label.lower():
                continue
            if assign_letter_to_volume(row.index, letter):
                return True
        return False

    def mountvol(self, volume: VolumeRecord | None, letter: str) -> bool:
        """Optional UEFI-only attempt. Failure is not fatal; the caller falls through."""
        del volume
        code, out = _run(["mountvol", f"{letter}:", "/s"], timeout=60)
        if code == 0 and Path(f"{letter}:\\").exists():
            return True
        log(f"mountvol {letter}: /S failed ({code}): {(out or '')[:240]}. {MOUNTVOL_UNSUPPORTED}", "WARN")
        return False

    def diskpart_assign(self, volume: VolumeRecord | None, letter: str) -> bool:
        """Assign by diskpart volume index, not by a guessed disk number."""
        from .diskpart_safe import assign_letter_to_volume, list_volumes

        if Path(f"{letter.upper()[:1]}:\\").exists():
            log(f"diskpart assign skipped: {letter}: is already in use", "WARN")
            return False
        rows = list_volumes()
        chosen = None
        for row in rows:
            if row.letter:
                continue
            if volume is not None and volume.label and row.label:
                if volume.label.lower() not in row.label.lower() and row.label.lower() not in volume.label.lower():
                    continue
            if volume is not None and (volume.filesystem or "").upper() in ("FAT32", "FAT", "NTFS"):
                if row.fs and row.fs.upper() != (volume.filesystem or "").upper():
                    continue
            chosen = row
            break
        if chosen is None:
            log("diskpart assign: no unlettered volume matched the drive map", "WARN")
            return False
        return assign_letter_to_volume(chosen.index, letter)

    def folder_mount(self, volume: VolumeRecord | None, folder: str) -> bool:
        if volume is None or volume.partition is None or volume.disk < 0:
            return False
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        script = (
            f"Add-PartitionAccessPath -DiskNumber {int(volume.disk)} "
            f"-PartitionNumber {int(volume.partition)} -AccessPath '{path}\\' -EA Stop"
        )
        code, _out = _run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            timeout=60,
        )
        return code == 0 and path.exists()

    def remove_letter(self, volume: VolumeRecord | None, letter: str) -> None:
        if volume is not None and volume.partition is not None and volume.disk >= 0:
            script = (
                f"Remove-PartitionAccessPath -DiskNumber {int(volume.disk)} "
                f"-PartitionNumber {int(volume.partition)} -AccessPath '{letter.upper()[:1]}:' -EA SilentlyContinue"
            )
            _run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                timeout=60,
            )
            return
        _run(["mountvol", f"{letter.upper()[:1]}:", "/d"])

    def mountvol_delete(self, letter: str) -> None:
        _run(["mountvol", f"{letter.upper()[:1]}:", "/d"])

    def remove_folder(self, volume: VolumeRecord | None, folder: str) -> None:
        if volume is not None and volume.partition is not None and volume.disk >= 0:
            script = (
                f"Remove-PartitionAccessPath -DiskNumber {int(volume.disk)} "
                f"-PartitionNumber {int(volume.partition)} -AccessPath '{folder}\\' -EA SilentlyContinue"
            )
            _run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                timeout=60,
            )
        try:
            Path(folder).rmdir()
        except OSError:
            pass

    def probe(self, path: str) -> VolumeContent:
        return probe_tree(path)


def mount_system_partition(*, role: str = "auto", prefer_letter: str | None = None) -> str | None:
    """Return 'E:' or a folder. Only a path we created is recorded for cleanup."""
    try:
        decision = prepare_boot_step(f"mount-{role}")
    except Exception as exc:
        log(f"ESP mount scan failed: {exc}", "WARN")
        decision = None
    if decision is None:
        log(
            "Drive map is unavailable, so mountvol /S is not used. "
            "A system partition cannot be opened without a map.",
            "ERROR",
        )
        return None
    if decision.abort and role == "auto":
        log(decision.explanation or "no safe system partition", "ERROR")
        return None
    target = _target_for(decision, role)
    if target is None:
        log(decision.explanation or "no safe system partition", "ERROR")
        return None
    current = _poller().map
    firmware = (current.firmware if current is not None else "unknown")
    used = set()
    if current is not None:
        from .drive_map import letters_in_use

        used = letters_in_use(current)
    folder = str(_folder())
    if firmware.upper() == "BIOS":
        log("BIOS boot: mountvol /S is not attempted. Using the system or Windows volume.", "INFO")
    chain = access_chain(target, used, folder_fallback=folder, firmware=firmware)
    plan = chain[0]
    if prefer_letter and not target.letter:
        wanted = prefer_letter.strip().upper()[:1]
        if wanted in used:
            log(f"Requested letter {wanted}: is already taken. Choosing another free letter.", "WARN")
        elif wanted and wanted not in ("A", "B"):
            plan = MountPlan("assign_letter", f"{wanted}:", wanted, True, folder)
            chain = (plan,) + tuple(step for step in chain if step.action != "assign_letter")
    windows_boot = bool(
        decision.bcd_mode == "BIOS" and target.is_boot and not is_gpt_esp(target)
    )
    try:
        path, applied = apply_and_verify(
            target,
            plan,
            WindowsVolumeIO(),
            allow_empty=True,
            allow_windows_boot=windows_boot,
            attempts=chain,
        )
    except UnsafeTarget as exc:
        log(f"ESP check failed: {exc.reason}", "ERROR")
        return None
    except Exception as exc:
        log(f"ESP mount failed: {exc}", "ERROR")
        return None
    remember_mount(path, applied, target)
    method = {
        "assign_letter": "Add-PartitionAccessPath",
        "diskpart_assign": "diskpart assign",
        "mountvol": "mountvol /S",
        "folder": "folder mount",
        "reuse_letter": "existing letter",
        "reuse_folder": "existing folder",
    }.get(applied.action, applied.action)
    cleanup = (
        " This letter is temporary and will be removed at cleanup."
        if applied.mounted_by_us
        else " The existing access path is left in place."
    )
    shown = path if not (letter := drive_letter_of(path)) else f"{letter}:"
    log(f"ESP opened at {shown} via {method}.{cleanup}", "OK")
    if letter and (access_root(path) or "").upper() == f"{letter}:\\":
        return f"{letter}:"
    guid = normalize_volume_guid_path(path)
    if guid:
        return guid
    return path.rstrip("\\")


def guard_write_letter(letter: str, *, expect_disk: int, step: str) -> None:
    """Re-resolve a caller-supplied letter. Abort when it now points somewhere else."""
    decision = prepare_boot_step(step)
    if decision is None or _poller().map is None:
        return
    if decision.abort:
        raise GuardError(decision.explanation or f"{step}: drive map has no safe target")
    current = _poller().map
    assert current is not None
    wanted = drive_letter_of(letter)
    match = None
    for vol in current.volumes:
        if wanted and (vol.letter or "").upper() == wanted:
            match = vol
            break
    if match is None:
        raise GuardError(f"{step}: {letter} is not on the fresh drive map")
    if int(match.disk) != int(expect_disk):
        raise GuardError(
            f"{step}: {letter} is on disk {match.disk} after rescan; the selected target disk is {expect_disk}"
        )
    if decision.windows is not None and fingerprint(match) == fingerprint(decision.windows):
        raise GuardError(f"{step}: {letter} is the Windows volume")
    if (
        step in ("opencore", "openduet")
        and decision.esp is not None
        and fingerprint(match) == fingerprint(decision.esp)
    ):
        raise GuardError(f"{step}: {letter} is the firmware ESP, not the USB target")


def resolved_bcd_mode(prefer_uefi: bool, decision: BootDecision | None) -> list[str]:
    """Modes to try, taken from the fresh plan when one exists."""
    if decision is None:
        return ["UEFI", "ALL"] if prefer_uefi else ["BIOS", "ALL"]
    if decision.skip_loader_overwrite:
        return []
    mode = (decision.bcd_mode or "").upper()
    if mode == "BIOS":
        return ["BIOS", "ALL"]
    if mode == "UEFI":
        return ["UEFI", "ALL"] if prefer_uefi else ["ALL", "UEFI"]
    if mode == "ALL":
        return ["ALL", "UEFI" if prefer_uefi else "BIOS"]
    return ["UEFI", "ALL"] if prefer_uefi else ["BIOS", "ALL"]


def bcdboot_command(exe: str, windows_root: str, target: str, mode: str) -> list[str]:
    return bcdboot_args(exe, windows_root, target, mode)
