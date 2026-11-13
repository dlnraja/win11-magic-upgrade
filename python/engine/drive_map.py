"""Full drive map, content-aware boot decisions, and ESP mount plans.

No drive letter, disk number, or partition index is assumed. Callers pass a
fresh map (or the previous one) and get a new plan. Disk numbers in a map are
observations from that scan, not constants.

The Windows scanner lives in ``esp_access``. This module is pure so unit tests
can feed fake maps and fake volume contents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Callable, Iterable

GPT_ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
MBR_EFI = 0xEF
MBR_PROTECTIVE = 0xEE

# Floppy letters are never used for an ESP mount.
_SKIP_LETTERS = frozenset({"A", "B"})


def normalize_guid(value: str | None) -> str:
    if not value:
        return ""
    return str(value).strip().strip("{}").lower()


def redact_serial(serial: str | None) -> str:
    """Keep a hint for diagnostics without writing the full serial."""
    s = (serial or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return "****"
    return f"{s[:2]}…{s[-2:]}"


def access_root(value: str | None) -> str | None:
    """'s', 'S:', 'S:\\\\' → 'S:\\\\'. A folder mount is returned with a trailing slash."""
    if value is None:
        return None
    s = str(value).strip().replace("/", "\\")
    if not s:
        return None
    if len(s) == 1 and s.isalpha():
        return f"{s.upper()}:\\"
    if len(s) >= 2 and s[0].isalpha() and s[1] == ":":
        rest = s[2:]
        if rest in ("", "\\"):
            return f"{s[0].upper()}:\\"
        return s if s.endswith("\\") else s + "\\"
    if s.startswith("\\\\"):
        return s if s.endswith("\\") else s + "\\"
    return None


def drive_letter_of(value: str | None) -> str | None:
    root = access_root(value)
    if not root or len(root) < 2 or root[1] != ":":
        return None
    if len(root) == 3 and root[2] == "\\":
        return root[0].upper()
    return None


def bcdboot_target(value: str | None) -> str | None:
    """bcdboot /s wants 'E:' or a mounted folder, never a hard-coded letter."""
    root = access_root(value)
    if not root:
        return None
    if len(root) == 3 and root[1] == ":" and root[2] == "\\":
        return root[:2]
    return root.rstrip("\\")


def bcdboot_args(exe: str, windows_root: str, target: str, mode: str) -> list[str]:
    mode_u = (mode or "").upper()
    if mode_u not in ("UEFI", "BIOS", "ALL"):
        raise ValueError(f"bcdboot mode must be UEFI, BIOS, or ALL (got {mode!r})")
    tgt = bcdboot_target(target)
    if not tgt:
        raise ValueError("bcdboot target is empty")
    return [str(exe), str(windows_root), "/s", tgt, "/f", mode_u]


def pick_free_letter(used: Iterable[str]) -> str | None:
    """Any free letter from Z down to D. Nothing is preferred, including Y."""
    taken = set()
    for item in used:
        letter = drive_letter_of(item) or (str(item).strip().upper()[:1] if str(item).strip() else "")
        if letter and "A" <= letter <= "Z":
            taken.add(letter)
    for code in range(ord("Z"), ord("C"), -1):
        letter = chr(code)
        if letter in _SKIP_LETTERS or letter in taken:
            continue
        return letter
    return None


@dataclass(frozen=True)
class VolumeContent:
    """Read-only picture of what a volume actually contains."""

    readable: bool = True
    has_efi_dir: bool = False
    has_microsoft_boot: bool = False
    has_bootmgfw: bool = False
    has_bootx64: bool = False
    has_bootia32: bool = False
    has_opencore: bool = False
    has_openduet: bool = False
    linux_loaders: tuple[str, ...] = ()
    has_bcd: bool = False
    has_bios_bootmgr: bool = False
    has_boot_bcd: bool = False
    has_windows: bool = False
    product_build: str | None = None
    edition: str | None = None
    bitness: str | None = None
    loader_version: tuple[int, ...] = ()
    has_install_wim: bool = False
    has_install_esd: bool = False
    has_boot_wim: bool = False
    has_setup_exe: bool = False
    media_version: str | None = None
    has_winre: bool = False
    free_bytes: int | None = None
    total_bytes: int | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class VolumeRecord:
    disk: int
    partition: int | None = None
    style: str = ""
    gpt_type: str = ""
    mbr_type: int | None = None
    type_name: str = ""
    filesystem: str = ""
    label: str = ""
    size_bytes: int | None = None
    letter: str | None = None
    access_paths: tuple[str, ...] = ()
    is_boot: bool = False
    is_system: bool = False
    is_active: bool = False
    bitlocker: str = "off"
    volume_guid: str = ""
    bus: str = "unknown"
    serial_redacted: str = ""
    offset: int | None = None
    dynamic: bool = False
    content: VolumeContent | None = None


@dataclass(frozen=True)
class DriveMap:
    volumes: tuple[VolumeRecord, ...]
    firmware: str = "UEFI"
    boot_disk: int | None = None
    windows_letter: str | None = None
    windows_volume_guid: str = ""
    source: str = "injected"
    captured_at: float = 0.0
    uefi_capable: bool = False


@dataclass(frozen=True)
class MapDiff:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    letter_changes: tuple[tuple[str, str | None, str | None], ...] = ()
    disk_changes: tuple[tuple[str, int, int], ...] = ()
    path_lost: tuple[str, ...] = ()
    content_changes: tuple[str, ...] = ()
    usb_added: tuple[str, ...] = ()


@dataclass(frozen=True)
class MountPlan:
    action: str
    path: str
    letter: str | None
    mounted_by_us: bool
    folder_fallback: str


@dataclass(frozen=True)
class BootDecision:
    esp: VolumeRecord | None = None
    srp: VolumeRecord | None = None
    windows: VolumeRecord | None = None
    install_media: VolumeRecord | None = None
    opencore: VolumeRecord | None = None
    openduet: VolumeRecord | None = None
    winre: VolumeRecord | None = None
    strategy: str = "abort"
    bcd_mode: str = "NONE"
    boot_files: tuple[str, ...] = ()
    mount: MountPlan | None = None
    preserve_loaders: tuple[str, ...] = ()
    skip_loader_overwrite: bool = False
    allow_mbr2gpt: bool = False
    windows_ambiguous: bool = False
    abort: bool = False
    explanation: str = ""
    warnings: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    diff: MapDiff | None = None


def make_volume(**kwargs: object) -> VolumeRecord:
    serial = str(kwargs.pop("serial", "") or "")
    redacted = kwargs.pop("serial_redacted", None)
    if not redacted:
        redacted = redact_serial(serial) if serial else ""
    letter = kwargs.get("letter")
    if isinstance(letter, str) and letter:
        kwargs["letter"] = letter.strip().rstrip("\\").rstrip(":").upper()[:1]
    gpt = normalize_guid(str(kwargs.get("gpt_type") or ""))
    kwargs["gpt_type"] = gpt
    paths = kwargs.get("access_paths") or ()
    if isinstance(paths, list):
        kwargs["access_paths"] = tuple(paths)
    loaders = None
    content = kwargs.get("content")
    if isinstance(content, VolumeContent) and isinstance(content.linux_loaders, list):
        loaders = tuple(content.linux_loaders)
        kwargs["content"] = replace(content, linux_loaders=loaders)
    kwargs["serial_redacted"] = str(redacted)
    return VolumeRecord(**kwargs)  # type: ignore[arg-type]


def content_signature(content: VolumeContent | None) -> str:
    if content is None:
        return ""
    return "|".join(
        [
            "1" if content.has_bootmgfw else "0",
            "1" if content.has_bootx64 else "0",
            "1" if content.has_bootia32 else "0",
            "1" if content.has_opencore else "0",
            "1" if content.has_openduet else "0",
            "1" if content.has_windows else "0",
            content.bitness or "",
            content.product_build or "",
            ",".join(content.linux_loaders),
            "1" if content.has_install_wim or content.has_install_esd else "0",
            "1" if content.has_winre else "0",
        ]
    )


def fingerprint(volume: VolumeRecord) -> str:
    """Identity that survives a letter change and a disk renumber."""
    guid = normalize_guid(volume.volume_guid)
    if guid:
        return "guid:" + guid
    return "|".join(
        [
            "fp",
            "" if volume.offset is None else str(volume.offset),
            "" if volume.size_bytes is None else str(volume.size_bytes),
            normalize_guid(volume.gpt_type),
            "" if volume.mbr_type is None else str(volume.mbr_type),
            (volume.filesystem or "").upper(),
            (volume.label or "").lower(),
            content_signature(volume.content),
        ]
    )


def summarize_content(content: VolumeContent | None) -> str:
    if content is None:
        return "unread"
    if not content.readable:
        return "unreadable"
    bits: list[str] = []
    if content.has_bootmgfw:
        bits.append(f"bootmgfw:{content.bitness or '?'}")
    if content.has_bootx64:
        bits.append("bootx64")
    if content.has_bootia32:
        bits.append("bootia32")
    if content.has_microsoft_boot and not content.has_bootmgfw:
        bits.append("efi-microsoft-boot")
    elif content.has_efi_dir and not (
        content.has_bootmgfw or content.has_bootx64 or content.has_bootia32
    ):
        bits.append("efi-dir")
    if content.has_opencore:
        bits.append("opencore")
    if content.has_openduet:
        bits.append("openduet")
    if content.linux_loaders:
        bits.append("linux:" + ",".join(content.linux_loaders))
    if content.has_bcd or content.has_boot_bcd:
        bits.append("bcd")
    if content.has_bios_bootmgr:
        bits.append("bootmgr")
    if content.has_windows:
        desc = "windows"
        if content.product_build:
            desc += ":" + content.product_build
        if content.edition:
            desc += "/" + content.edition
        if content.bitness:
            desc += "/" + content.bitness
        bits.append(desc)
    if content.has_install_wim or content.has_install_esd or content.has_setup_exe:
        media = "install-media"
        if content.media_version:
            media += ":" + content.media_version
        if content.bitness:
            media += "/" + content.bitness
        bits.append(media)
    if content.has_winre:
        bits.append("winre")
    if content.free_bytes is not None:
        bits.append(f"free={content.free_bytes}")
    return ",".join(bits) if bits else "empty"


def format_drive_map(drive_map: DriveMap) -> str:
    """Diagnostic text. Serials are already redacted on each volume."""
    header = (
        f"source={drive_map.source} firmware={drive_map.firmware} "
        f"boot_disk={drive_map.boot_disk if drive_map.boot_disk is not None else '-'} "
        f"windows_letter={drive_map.windows_letter or '-'} "
        f"windows_guid={normalize_guid(drive_map.windows_volume_guid) or '-'}"
    )
    lines = [header]
    for vol in drive_map.volumes:
        paths = ";".join(vol.access_paths) if vol.access_paths else "-"
        lines.append(
            " ".join(
                [
                    f"disk={vol.disk}",
                    f"part={vol.partition if vol.partition is not None else '-'}",
                    f"style={vol.style or '-'}",
                    f"gpt={normalize_guid(vol.gpt_type) or '-'}",
                    f"mbr={vol.mbr_type if vol.mbr_type is not None else '-'}",
                    f"fs={vol.filesystem or '-'}",
                    f"label={vol.label or '-'}",
                    f"size={vol.size_bytes if vol.size_bytes is not None else '-'}",
                    f"letter={vol.letter or '-'}",
                    f"paths={paths}",
                    f"boot={int(vol.is_boot)}",
                    f"sys={int(vol.is_system)}",
                    f"active={int(vol.is_active)}",
                    f"bitlocker={vol.bitlocker or '-'}",
                    f"bus={vol.bus or '-'}",
                    f"serial={vol.serial_redacted or '-'}",
                    f"content={summarize_content(vol.content)}",
                ]
            )
        )
    overview = content_overview(drive_map)
    lines.append("content-summary: " + (overview or "(no readable content)"))
    return "\n".join(lines)


def content_overview(drive_map: DriveMap) -> str:
    bits: list[str] = []
    for vol in drive_map.volumes:
        summary = summarize_content(vol.content)
        if summary in ("unread", "empty"):
            continue
        tag = vol.letter or (normalize_guid(vol.volume_guid)[:8] if vol.volume_guid else f"d{vol.disk}")
        bits.append(f"{tag}:{summary}")
    return "; ".join(bits)


def is_gpt_esp(volume: VolumeRecord) -> bool:
    if normalize_guid(volume.gpt_type) == GPT_ESP_GUID:
        return True
    name = (volume.type_name or "").lower()
    style = (volume.style or "").lower()
    return style == "gpt" and "efi" in name and "system" in name


def is_locked(volume: VolumeRecord) -> bool:
    return (volume.bitlocker or "").strip().lower() in ("locked", "on_locked")


def is_install_media(volume: VolumeRecord) -> bool:
    content = volume.content
    if content is None or not content.readable:
        return False
    if content.has_install_wim or content.has_install_esd:
        return True
    if content.has_setup_exe and content.has_boot_wim:
        return True
    return False


def is_winre_only(volume: VolumeRecord) -> bool:
    content = volume.content
    if content is None or not content.has_winre:
        return False
    if is_gpt_esp(volume):
        return False
    if content.has_windows or content.has_bootmgfw or content.has_bios_bootmgr:
        return False
    return True


def is_windows_volume(volume: VolumeRecord) -> bool:
    if is_install_media(volume) or is_gpt_esp(volume) or is_winre_only(volume):
        return False
    content = volume.content
    if content is not None and content.has_windows:
        return True
    if volume.is_boot and not is_mbr_efi(volume):
        return True
    return False


def is_mbr_efi(volume: VolumeRecord) -> bool:
    return volume.mbr_type == MBR_EFI


def is_mbr_system(volume: VolumeRecord) -> bool:
    """Active system / type 0xEF / System Reserved. Not the Windows volume."""
    if is_windows_volume(volume) and not is_mbr_efi(volume):
        return False
    if is_install_media(volume) or is_winre_only(volume):
        return False
    if volume.mbr_type == MBR_EFI:
        return True
    blob = f"{volume.label} {volume.type_name}".lower()
    if "system reserved" in blob or "reserv" in blob:
        return True
    if volume.is_active and volume.is_system and not volume.is_boot:
        return True
    content = volume.content
    if content is not None and (content.has_bios_bootmgr or content.has_boot_bcd) and not content.has_windows:
        size = volume.size_bytes or 0
        if size == 0 or size <= 2 * 1024 * 1024 * 1024:
            return True
    if volume.is_active and not volume.is_boot:
        fs = (volume.filesystem or "").upper()
        size = volume.size_bytes or 0
        if fs in ("NTFS", "FAT32", "FAT") and 0 < size <= 2 * 1024 * 1024 * 1024:
            if "data" not in (volume.label or "").lower():
                return True
    return False


def content_inconsistent(volume: VolumeRecord) -> bool:
    """True when the bytes contradict the partition type. Unread is not a contradiction."""
    content = volume.content
    if content is None or not content.readable:
        return False
    if is_gpt_esp(volume) or volume.mbr_type == MBR_EFI:
        fs = (volume.filesystem or "").upper()
        if fs in ("NTFS", "REFS", "EXFAT"):
            return True
        has_efi = any(
            (
                content.has_efi_dir,
                content.has_microsoft_boot,
                content.has_bootmgfw,
                content.has_bootx64,
                content.has_bootia32,
            )
        )
        if not has_efi and (content.has_windows or is_install_media(volume)):
            return True
    return False


def has_efi_tree(volume: VolumeRecord) -> bool:
    content = volume.content
    if content is None:
        return False
    return any(
        (
            content.has_efi_dir,
            content.has_microsoft_boot,
            content.has_bootmgfw,
            content.has_bootx64,
            content.has_bootia32,
        )
    )


def content_bitness(volume: VolumeRecord) -> str:
    content = volume.content
    if content is None:
        return ""
    if content.bitness in ("x64", "x86", "arm64"):
        return str(content.bitness)
    if content.has_bootx64 and not content.has_bootia32:
        return "x64"
    if content.has_bootia32 and not content.has_bootx64:
        return "x86"
    return ""


def letters_in_use(drive_map: DriveMap) -> set[str]:
    used: set[str] = set()
    if drive_map.windows_letter:
        letter = drive_map.windows_letter.strip().upper()[:1]
        if letter:
            used.add(letter)
    for vol in drive_map.volumes:
        if vol.letter:
            used.add(vol.letter.upper()[:1])
        for path in vol.access_paths:
            letter = drive_letter_of(path)
            if letter:
                used.add(letter)
    return used


def existing_folder(volume: VolumeRecord) -> str | None:
    for path in volume.access_paths:
        if path and drive_letter_of(path) is None and access_root(path):
            return access_root(path)
    return None


def plan_mount(
    volume: VolumeRecord,
    used: Iterable[str],
    *,
    folder_fallback: str,
    allow_mountvol: bool = False,
) -> MountPlan:
    """First step of ``access_chain``. ``allow_mountvol`` is ignored on purpose."""
    del allow_mountvol
    return access_chain(volume, used, folder_fallback=folder_fallback)[0]


def verify_for_write(
    volume: VolumeRecord,
    content: VolumeContent | None = None,
    *,
    allow_empty: bool = False,
    allow_windows_boot: bool = False,
) -> tuple[bool, str]:
    """FAT32 + an EFI tree, or a real MBR system volume, before any write."""
    if is_locked(volume):
        return False, "bitlocker_locked"
    probe = content if content is not None else volume.content
    fs = (volume.filesystem or "").upper()
    if is_install_media(volume):
        return False, "install_media_not_esp"
    if is_windows_volume(volume) and not is_mbr_efi(volume):
        if allow_windows_boot and (volume.is_boot or (probe is not None and probe.has_windows)):
            return True, "windows_boot_volume"
        return False, "windows_volume_not_esp"
    typed_esp = is_gpt_esp(volume) or volume.mbr_type == MBR_EFI
    if typed_esp:
        if fs in ("NTFS", "REFS", "EXFAT"):
            return False, f"esp_not_fat:{fs}"
        if probe is None:
            return (True, "typed_esp_unprobed") if allow_empty else (False, "content_unread")
        if not probe.readable:
            return False, "content_unreadable"
        if probe.has_microsoft_boot or probe.has_bootmgfw or probe.has_efi_dir or probe.has_bootx64 or probe.has_bootia32:
            if fs and fs not in ("FAT32", "FAT", "FAT16"):
                return False, f"esp_not_fat:{fs}"
            return True, "efi_tree"
        if allow_empty and fs in ("", "FAT32", "FAT", "FAT16") and not probe.has_windows:
            return True, "empty_typed_esp"
        return False, "missing_efi"
    if probe is not None and (probe.has_bios_bootmgr or probe.has_boot_bcd or probe.has_efi_dir):
        return True, "mbr_system_files"
    if is_mbr_system(volume) and allow_empty:
        return True, "empty_mbr_system"
    if is_mbr_system(volume) and probe is None and allow_empty:
        return True, "mbr_system_unprobed"
    return False, "inconsistent_content"


def disk_style(drive_map: DriveMap, disk: int | None) -> str:
    vols = [v for v in drive_map.volumes if disk is None or v.disk == disk]
    styles = {(v.style or "").lower() for v in vols}
    if "hybrid" in styles or ("gpt" in styles and "mbr" in styles):
        return "hybrid"
    types = [v.mbr_type for v in vols if v.mbr_type]
    if MBR_PROTECTIVE in types and any(t not in (MBR_PROTECTIVE,) for t in types):
        return "hybrid"
    if "gpt" in styles or any(is_gpt_esp(v) for v in vols):
        return "gpt"
    if "mbr" in styles or any(v.mbr_type not in (None, 0) for v in vols):
        return "mbr"
    return "unknown"


def classify_firmware(
    *,
    firmware_type: str | None = None,
    env_var_present: bool | None = None,
    bcd_loader: str | None = None,
) -> str:
    """Current boot mode. winload.exe means BIOS even if the box could do UEFI.

    ``mountvol /S`` only works for a UEFI boot that has an ESP. A legacy BIOS
    boot, WinPE, or a failed firmware API must not be reported as UEFI.
    """
    loader = (bcd_loader or "").lower()
    if "winload.exe" in loader or loader == "exe":
        return "BIOS"
    if "winload.efi" in loader or loader == "efi":
        return "UEFI"
    kind = (firmware_type or "").strip().upper()
    if kind == "BIOS":
        return "BIOS"
    if env_var_present is False and kind != "UEFI":
        return "BIOS"
    if kind == "UEFI" or env_var_present is True:
        return "UEFI"
    return "unknown"


def uefi_supported(
    *,
    firmware_type: str | None = None,
    env_var_present: bool | None = None,
    bcd_loader: str | None = None,
    firmware_text: str = "",
) -> bool:
    """True only when the firmware can boot UEFI. Required before mbr2gpt."""
    if classify_firmware(
        firmware_type=firmware_type,
        env_var_present=env_var_present,
        bcd_loader=bcd_loader,
    ) == "UEFI":
        return True
    if (firmware_type or "").strip().upper() == "UEFI" or env_var_present is True:
        return True
    return bool(re.search(r"\bUEFI\b", firmware_text or "", re.I))


def _firmware(drive_map: DriveMap) -> str:
    name = (drive_map.firmware or "").strip().upper()
    if name in ("BIOS", "LEGACY", "MBR"):
        return "BIOS"
    if name in ("UEFI", "EFI"):
        return "UEFI"
    return "unknown"


MOUNTVOL_UNSUPPORTED = (
    "mountvol /S was not used as the mount method. It only works when this Windows "
    "boot is UEFI and an ESP exists. It fails on legacy BIOS, when the ESP already "
    "has a letter, when the chosen letter is taken, in WinPE, and without administrator rights."
)


def access_chain(
    volume: VolumeRecord,
    used: Iterable[str],
    *,
    folder_fallback: str,
    firmware: str = "UEFI",
) -> tuple[MountPlan, ...]:
    """How to open a volume. mountvol /S is never the only step, and not the first.

    Order: existing letter or folder, Add-PartitionAccessPath, diskpart assign, folder.
    """
    del firmware  # kept so callers record the mode they already detected
    if volume.letter:
        return (
            MountPlan(
                "reuse_letter",
                f"{volume.letter.upper()[:1]}:",
                volume.letter.upper()[:1],
                False,
                folder_fallback,
            ),
        )
    folder = existing_folder(volume)
    if folder:
        return (MountPlan("reuse_folder", folder.rstrip("\\"), None, False, folder_fallback),)
    if volume.dynamic:
        return (MountPlan("folder", folder_fallback, None, True, folder_fallback),)
    free = pick_free_letter(used)
    steps: list[MountPlan] = []
    if free:
        steps.append(MountPlan("assign_letter", f"{free}:", free, True, folder_fallback))
        steps.append(MountPlan("diskpart_assign", f"{free}:", free, True, folder_fallback))
    steps.append(MountPlan("folder", folder_fallback, None, True, folder_fallback))
    return tuple(steps)


def _windows_disk(drive_map: DriveMap) -> int | None:
    """Disk that holds Windows, including a secondary disk. Never assumed to be 0."""
    if drive_map.windows_volume_guid:
        want = normalize_guid(drive_map.windows_volume_guid)
        for vol in drive_map.volumes:
            if normalize_guid(vol.volume_guid) == want and vol.disk >= 0:
                return vol.disk
    for vol in drive_map.volumes:
        if is_windows_volume(vol) and vol.disk >= 0:
            return vol.disk
    return drive_map.boot_disk if drive_map.boot_disk is not None and drive_map.boot_disk >= 0 else None


def _on_boot_disk(drive_map: DriveMap, volume: VolumeRecord) -> bool:
    if drive_map.boot_disk is None:
        return volume.bus != "usb"
    return volume.disk == drive_map.boot_disk


def _on_windows_disk(drive_map: DriveMap, volume: VolumeRecord) -> bool:
    disk = _windows_disk(drive_map)
    if disk is None:
        return _on_boot_disk(drive_map, volume)
    return volume.disk == disk


def _esp_score(drive_map: DriveMap, volume: VolumeRecord, *, os_arch: str) -> int:
    score = 0
    if volume.is_system:
        score += 100
    if is_gpt_esp(volume):
        score += 50
    if volume.mbr_type == MBR_EFI:
        score += 40
    content = volume.content
    if content is not None:
        if content.has_bootmgfw:
            score += 40
        elif content.has_microsoft_boot:
            score += 25
        if content.has_bootx64 or content.has_bootia32 or content.has_efi_dir:
            score += 10
        if content.linux_loaders and not (content.has_bootmgfw or content.has_microsoft_boot):
            score -= 35
        if content.has_opencore and not content.has_bootmgfw:
            score -= 20
        bits = content_bitness(volume)
        want = "x86" if os_arch == "x86" else "x64"
        if bits and bits == want:
            score += 15
        elif bits and bits != want:
            score -= 5
    fs = (volume.filesystem or "").upper()
    if fs in ("FAT32", "FAT"):
        score += 10
    if volume.bus == "usb" and not _on_boot_disk(drive_map, volume):
        score -= 80
    if not _on_boot_disk(drive_map, volume):
        score -= 30
    return score


def _esp_pool(drive_map: DriveMap) -> list[VolumeRecord]:
    pool: list[VolumeRecord] = []
    for vol in drive_map.volumes:
        if is_install_media(vol) or is_winre_only(vol) or is_windows_volume(vol):
            continue
        if is_locked(vol) or content_inconsistent(vol):
            continue
        if is_gpt_esp(vol) or has_efi_tree(vol) or is_mbr_efi(vol):
            pool.append(vol)
    on_boot = [v for v in pool if _on_boot_disk(drive_map, v)]
    if on_boot:
        return on_boot
    return [v for v in pool if _on_windows_disk(drive_map, v)]


def _pick_scored(
    drive_map: DriveMap,
    pool: list[VolumeRecord],
    *,
    os_arch: str,
    prefer_fp: str | None,
) -> tuple[VolumeRecord | None, bool]:
    """Return (choice, ambiguous). prefer_fp keeps the same volume across letter/disk changes."""
    if not pool:
        return None, False
    if prefer_fp:
        for vol in pool:
            if fingerprint(vol) == prefer_fp:
                return vol, False
    ranked = sorted(pool, key=lambda v: _esp_score(drive_map, v, os_arch=os_arch), reverse=True)
    best = _esp_score(drive_map, ranked[0], os_arch=os_arch)
    top = [v for v in ranked if _esp_score(drive_map, v, os_arch=os_arch) == best]
    if len(top) == 1:
        return top[0], False
    return None, True


def _mbr_pool(drive_map: DriveMap) -> list[VolumeRecord]:
    pool = []
    for vol in drive_map.volumes:
        if is_locked(vol) or content_inconsistent(vol):
            continue
        if is_mbr_system(vol) and _on_windows_disk(drive_map, vol):
            pool.append(vol)
    return pool


def _choose_windows(drive_map: DriveMap) -> tuple[VolumeRecord | None, bool]:
    cands = [v for v in drive_map.volumes if is_windows_volume(v)]
    if drive_map.windows_volume_guid:
        want = normalize_guid(drive_map.windows_volume_guid)
        for vol in cands:
            if normalize_guid(vol.volume_guid) == want:
                return vol, False
    if drive_map.windows_letter:
        want = drive_map.windows_letter.upper()[:1]
        for vol in cands:
            if (vol.letter or "").upper() == want:
                return vol, False
    booting = [v for v in cands if v.is_boot]
    if len(booting) == 1:
        return booting[0], False
    if len(cands) == 1:
        return cands[0], False
    if len(cands) > 1:
        return None, True
    return None, False


def _first_where(volumes: Iterable[VolumeRecord], pred: Callable[[VolumeRecord], bool]) -> VolumeRecord | None:
    rows = [v for v in volumes if pred(v)]
    for vol in rows:
        if vol.bus == "usb":
            return vol
    return rows[0] if rows else None


def _preserve(drive_map: DriveMap) -> tuple[str, ...]:
    found: list[str] = []
    for vol in drive_map.volumes:
        content = vol.content
        if content is None:
            continue
        for name in content.linux_loaders:
            if name not in found:
                found.append(name)
        if content.has_opencore and "opencore" not in found:
            found.append("opencore")
        if content.has_openduet and "openduet" not in found:
            found.append("openduet")
        if content.has_bootia32 and "bootia32.efi" not in found:
            found.append("bootia32.efi")
    return tuple(found)


@dataclass
class _Roles:
    esp: VolumeRecord | None = None
    srp: VolumeRecord | None = None
    ambiguous: bool = False
    ambiguous_detail: str = ""
    locked: int = 0
    inconsistent: int = 0


def _select_roles(drive_map: DriveMap, *, os_arch: str, prefer_fp: str | None) -> _Roles:
    roles = _Roles()
    roles.locked = sum(1 for v in drive_map.volumes if is_locked(v) and (is_gpt_esp(v) or is_mbr_system(v)))
    roles.inconsistent = sum(1 for v in drive_map.volumes if content_inconsistent(v))
    esp, ambiguous = _pick_scored(drive_map, _esp_pool(drive_map), os_arch=os_arch, prefer_fp=prefer_fp)
    roles.esp = esp
    roles.ambiguous = ambiguous
    if ambiguous:
        roles.ambiguous_detail = "more than one ESP on the firmware boot disk matches equally"
    mbr_pool = _mbr_pool(drive_map)
    # Do not treat a GPT ESP as the MBR system volume.
    mbr_only = [v for v in mbr_pool if not is_gpt_esp(v) or v.mbr_type == MBR_EFI]
    srp, srp_amb = _pick_scored(drive_map, mbr_only, os_arch=os_arch, prefer_fp=prefer_fp)
    if srp is None and not srp_amb and len(mbr_only) == 1:
        srp = mbr_only[0]
    roles.srp = srp
    if roles.esp is None and srp_amb and _firmware(drive_map) == "BIOS":
        roles.ambiguous = True
        roles.ambiguous_detail = "more than one active system partition matches equally"
    return roles


def diff_maps(previous: DriveMap | None, current: DriveMap) -> MapDiff:
    if previous is None:
        fps = tuple(fingerprint(v) for v in current.volumes)
        usb = tuple(fingerprint(v) for v in current.volumes if v.bus == "usb")
        return MapDiff(added=fps, usb_added=usb)
    old = {fingerprint(v): v for v in previous.volumes}
    new = {fingerprint(v): v for v in current.volumes}
    added = tuple(fp for fp in new if fp not in old)
    removed = tuple(fp for fp in old if fp not in new)
    letter_changes: list[tuple[str, str | None, str | None]] = []
    disk_changes: list[tuple[str, int, int]] = []
    path_lost: list[str] = []
    content_changes: list[str] = []
    for fp, vol in new.items():
        prev = old.get(fp)
        if prev is None:
            continue
        if (prev.letter or None) != (vol.letter or None):
            letter_changes.append((fp, prev.letter, vol.letter))
        if prev.disk != vol.disk:
            disk_changes.append((fp, prev.disk, vol.disk))
        had_path = bool(prev.letter) or bool(prev.access_paths)
        has_path = bool(vol.letter) or bool(vol.access_paths)
        if had_path and not has_path:
            path_lost.append(fp)
        if content_signature(prev.content) != content_signature(vol.content):
            content_changes.append(fp)
    usb_added = tuple(fp for fp in added if new[fp].bus == "usb")
    return MapDiff(
        added=added,
        removed=removed,
        letter_changes=tuple(letter_changes),
        disk_changes=tuple(disk_changes),
        path_lost=tuple(path_lost),
        content_changes=tuple(content_changes),
        usb_added=usb_added,
    )


def _version_newer(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    if not left or not right:
        return False
    width = max(len(left), len(right))
    a = left + (0,) * (width - len(left))
    b = right + (0,) * (width - len(right))
    return a > b


def _loader_on_windows(windows: VolumeRecord | None) -> tuple[int, ...]:
    if windows is None or windows.content is None:
        return ()
    return tuple(windows.content.loader_version or ())


def decide(
    drive_map: DriveMap,
    *,
    previous: DriveMap | None = None,
    os_arch: str = "x64",
    cpu_64: bool = True,
    secure_boot: str = "unknown",
    folder_fallback: str = "esp-mount",
    request_conversion: bool = False,
) -> BootDecision:
    """Re-plan from this map. ``previous`` is the last scan, if any."""
    fw = _firmware(drive_map)
    diff = diff_maps(previous, drive_map) if previous is not None else None
    prefer = None
    prev_fp = None
    if previous is not None:
        prev_roles = _select_roles(previous, os_arch=os_arch, prefer_fp=None)
        prev_target = prev_roles.esp or prev_roles.srp
        if prev_target is not None:
            prev_fp = fingerprint(prev_target)
            prefer = prev_fp
    roles = _select_roles(drive_map, os_arch=os_arch, prefer_fp=prefer)
    windows, win_amb = _choose_windows(drive_map)
    install = _first_where(drive_map.volumes, is_install_media)
    opencore = _first_where(
        drive_map.volumes,
        lambda v: bool(v.content and v.content.has_opencore),
    )
    openduet = _first_where(
        drive_map.volumes,
        lambda v: bool(v.content and v.content.has_openduet and not (v.content.has_opencore)),
    )
    if openduet is None:
        openduet = _first_where(
            drive_map.volumes,
            lambda v: bool(v.content and v.content.has_openduet),
        )
    winre = _first_where(drive_map.volumes, is_winre_only)
    style = disk_style(drive_map, drive_map.boot_disk)
    preserve = _preserve(drive_map)
    warnings: list[str] = []
    actions: list[str] = []
    notes: list[str] = []

    if diff is not None:
        for _fp, old, new in diff.letter_changes:
            notes.append(f"letter reassigned {old or 'none'} -> {new or 'none'}")
        for _fp, old_disk, new_disk in diff.disk_changes:
            notes.append(f"disk renumbered {old_disk} -> {new_disk}")
        if diff.usb_added:
            notes.append(f"usb inserted ({len(diff.usb_added)})")
            actions.append("note_usb")
        if diff.path_lost:
            notes.append("ESP letter removed; access path will be remounted")
            actions.append("remount")
        if diff.content_changes:
            notes.append("volume content changed")

    target_esp = roles.esp
    target_srp = roles.srp
    switched = bool(prev_fp and ((target_esp and fingerprint(target_esp) != prev_fp) or (
        target_esp is None and target_srp is not None and fingerprint(target_srp) != prev_fp
    )))
    if switched:
        actions.append("alternate_esp")
        notes.append("previous system partition is gone; using the remaining boot volume on the firmware disk")

    abort = False
    explanation_bits = list(notes)
    if roles.ambiguous:
        abort = True
        explanation_bits.append(roles.ambiguous_detail or "system partition is ambiguous")
        actions.append("abort")
    elif fw == "UEFI" and target_esp is None and style != "mbr":
        if roles.locked and not roles.inconsistent:
            explanation_bits.append("BitLocker locked the only system partition; no other ESP is safe to use")
        elif roles.inconsistent:
            explanation_bits.append(
                "ESP content is inconsistent (not FAT32 with an EFI directory, or it is a Windows/install volume)"
            )
        else:
            explanation_bits.append("no safe ESP on the firmware boot disk")
        abort = True
        actions.append("abort")
    elif fw in ("BIOS", "unknown") and target_srp is None and target_esp is None and windows is None and opencore is None and openduet is None:
        explanation_bits.append("no system partition, no Windows volume, and no OpenCore/OpenDuet volume")
        abort = True
        actions.append("abort")

    strategy = "abort"
    bcd_mode = "NONE"
    boot_files: tuple[str, ...] = ()
    allow_mbr2gpt = False

    if not abort:
        esp_bits = content_bitness(target_esp) if target_esp is not None else ""
        has_x64 = bool(
            target_esp
            and target_esp.content
            and (target_esp.content.has_bootx64 or esp_bits == "x64" or (
                target_esp.content.has_bootmgfw and target_esp.content.bitness in (None, "", "x64")
                and not target_esp.content.has_bootia32
            ))
        )
        only_ia32 = bool(
            target_esp
            and target_esp.content
            and target_esp.content.has_bootia32
            and not target_esp.content.has_bootx64
            and esp_bits != "x64"
        )
        if fw in ("BIOS", "unknown"):
            # Legacy boot: never mount or create an ESP. mbr2gpt only if the
            # firmware can actually boot UEFI and the caller asked to convert.
            if style == "gpt" and target_esp is None:
                strategy = "bios_gpt"
                bcd_mode = "BIOS"
                boot_files = ("bootmgr",)
                actions.append("bootsect_volume_only")
                warnings.append(
                    "GPT disk on a BIOS boot (protective MBR). No ESP is created. "
                    "OpenDuet/OpenCore remains the UEFI chain for this firmware."
                )
            elif style == "hybrid":
                strategy = "hybrid_mbr_gpt"
                bcd_mode = "BIOS"
                boot_files = ("bootmgr",)
                actions.append("bootsect_nt60")
            elif target_srp is not None or windows is not None or target_esp is not None:
                strategy = "bios_mbr"
                bcd_mode = "BIOS"
                boot_files = ("bootmgr",)
                actions.append("bootsect_nt60")
                if target_srp is None and target_esp is None and windows is not None:
                    actions.append("windows_is_boot_target")
                    warnings.append(
                        "No ESP and no System Reserved partition. "
                        "BIOS boot files go on the Windows volume. An ESP is not created."
                    )
                if target_esp is not None and fw == "BIOS":
                    warnings.append(
                        "An ESP is present but this boot is legacy BIOS. "
                        "mountvol /S is not used and UEFI boot files are not written."
                    )
            elif openduet is not None:
                strategy = "openduet"
                bcd_mode = "NONE"
                boot_files = ("boot",)
                actions.append("openduet_chain")
            elif opencore is not None:
                strategy = "opencore"
                bcd_mode = "NONE"
                boot_files = ("OpenCore.efi",)
                actions.append("opencore_chain")
            if drive_map.uefi_capable:
                warnings.append(
                    "Firmware can boot UEFI, but this session is BIOS. "
                    "mbr2gpt runs only when conversion is explicitly requested."
                )
            else:
                warnings.append("BIOS-only firmware: mbr2gpt and a new ESP are not used.")
                actions.append("no_esp_on_bios")
            if drive_map.uefi_capable and request_conversion:
                allow_mbr2gpt = True
                actions.append("mbr2gpt_explicit")
        elif style == "hybrid":
            strategy = "hybrid_mbr_gpt"
            bcd_mode = "UEFI"
            boot_files = ("bootx64.efi",)
            actions.append("keep_hybrid_layout")
            warnings.append("Hybrid MBR/GPT layout is kept; mbr2gpt is not used.")
        elif style == "mbr":
            strategy = "uefi_mbr_convert"
            bcd_mode = "UEFI"
            boot_files = ("bootx64.efi",)
            allow_mbr2gpt = not win_amb
            actions.append("mbr2gpt")
            if only_ia32:
                warnings.append("MBR disk still has only IA32 boot files.")
            if win_amb:
                warnings.append("Windows volume is ambiguous; mbr2gpt is held.")
        elif only_ia32 and cpu_64:
            strategy = "ia32_hybrid"
            bcd_mode = "BIOS"
            boot_files = ("bootia32.efi", "bootmgr")
            actions.append("stage_csmwrap")
            warnings.append(
                "Firmware boot files are IA32 only. CSMWrap can chain to BIOS bootmgr; "
                "Secure Boot has to be off before that loader is activated."
            )
        elif os_arch == "x86" and (has_x64 or esp_bits == "x64"):
            strategy = "cross_bitness"
            bcd_mode = "UEFI"
            boot_files = ("bootx64.efi",)
            warnings.append("32-bit Windows cannot inplace-upgrade to 64-bit. Boot files stay bootx64.efi.")
        elif only_ia32 and not cpu_64:
            strategy = "uefi_ia32"
            bcd_mode = "UEFI"
            boot_files = ("bootia32.efi",)
        else:
            strategy = "uefi64" if os_arch != "x86" else "uefi_ia32"
            bcd_mode = "UEFI"
            boot_files = ("bootia32.efi",) if strategy == "uefi_ia32" else ("bootx64.efi",)

    if secure_boot == "on" and strategy in ("ia32_hybrid", "opencore", "openduet"):
        warnings.append("Secure Boot is on. The unsigned bridge is staged only and is not activated.")
    if secure_boot == "on" and bcd_mode in ("UEFI", "ALL"):
        warnings.append("Secure Boot is on; UEFI boot files are checked before they are written.")

    write_target = None
    if not abort:
        if strategy in ("bios_mbr", "bios_gpt") or (
            strategy == "hybrid_mbr_gpt" and fw in ("BIOS", "unknown")
        ):
            write_target = target_srp or (None if target_esp is not None and fw == "BIOS" else target_esp) or windows
        elif strategy in ("opencore", "openduet"):
            write_target = openduet if strategy == "openduet" else opencore
        elif strategy == "uefi_mbr_convert":
            write_target = target_srp or target_esp
        else:
            write_target = target_esp or target_srp

    if not abort and fw in ("BIOS", "unknown"):
        boot_vol = target_srp or windows
        if boot_vol is not None and not boot_vol.is_active and not is_gpt_esp(boot_vol):
            actions.append("mark_active")
            warnings.append("No partition is marked active. The BIOS boot volume will be set active after bcdboot succeeds.")
        win_disk = _windows_disk(drive_map)
        if win_disk not in (None, 0):
            actions.append("windows_secondary_disk")
            notes.append(f"Windows is on disk {win_disk}, not disk 0")
            explanation_bits.append(f"Windows is on disk {win_disk}, not disk 0")
        if any(vol.dynamic for vol in drive_map.volumes if win_disk is None or vol.disk == win_disk):
            actions.append("dynamic_disk")
            warnings.append("Dynamic disk: no new ESP is created and diskpart will not assign a letter.")

    mount = None
    if write_target is not None and not abort:
        allow_mountvol = bool(
            write_target.is_system and (drive_map.boot_disk is None or write_target.disk == drive_map.boot_disk)
        )
        mount = plan_mount(
            write_target,
            letters_in_use(drive_map),
            folder_fallback=folder_fallback,
            allow_mountvol=allow_mountvol and is_gpt_esp(write_target),
        )
        actions.append(mount.action if mount.action != "folder" else "folder_mount")

    skip = False
    if (
        target_esp is not None
        and target_esp.content is not None
        and windows is not None
        and _version_newer(tuple(target_esp.content.loader_version or ()), _loader_on_windows(windows))
    ):
        skip = True
        actions.append("skip_newer_loader")
        warnings.append(
            "ESP boot manager is newer than the copy in Windows. It will not be overwritten."
        )

    if preserve:
        actions.append("preserve_other_os")
        warnings.append("Other OS loaders stay in place: " + ", ".join(preserve))

    low = None
    if target_esp is not None and target_esp.content is not None and target_esp.content.free_bytes is not None:
        if target_esp.content.free_bytes < 20 * 1024 * 1024:
            low = target_esp.content.free_bytes
    if low is not None:
        warnings.append(f"ESP free space is low ({low} bytes).")

    if win_amb:
        warnings.append("More than one Windows install matches; the running Windows directory is used for bcdboot.")

    if abort:
        strategy = "abort"
        bcd_mode = "NONE"
        mount = None

    explanation = "; ".join(bit for bit in explanation_bits if bit)
    if not explanation and warnings:
        explanation = warnings[0]

    return BootDecision(
        esp=target_esp,
        srp=target_srp,
        windows=windows,
        install_media=install,
        opencore=opencore,
        openduet=openduet,
        winre=winre,
        strategy=strategy,
        bcd_mode=bcd_mode,
        boot_files=boot_files,
        mount=mount,
        preserve_loaders=preserve,
        skip_loader_overwrite=skip,
        allow_mbr2gpt=allow_mbr2gpt and not abort,
        windows_ambiguous=win_amb,
        abort=abort,
        explanation=explanation,
        warnings=tuple(warnings),
        actions=tuple(dict.fromkeys(actions)),
        diff=diff,
    )


def replan(current: DriveMap, previous: DriveMap | None = None, **kwargs: object) -> BootDecision:
    """Diff ``current`` against ``previous`` and build a new boot plan."""
    return decide(current, previous=previous, **kwargs)  # type: ignore[arg-type]


def drive_map_from_payload(payload: dict) -> DriveMap:
    """Build a map from the scanner JSON. Full serials are redacted here."""
    volumes: list[VolumeRecord] = []
    for raw in payload.get("volumes") or []:
        content = None
        raw_content = raw.get("content")
        if isinstance(raw_content, dict):
            loaders = raw_content.get("linux_loaders") or []
            version = raw_content.get("loader_version") or []
            content = VolumeContent(
                readable=bool(raw_content.get("readable", True)),
                has_efi_dir=bool(raw_content.get("has_efi_dir")),
                has_microsoft_boot=bool(raw_content.get("has_microsoft_boot")),
                has_bootmgfw=bool(raw_content.get("has_bootmgfw")),
                has_bootx64=bool(raw_content.get("has_bootx64")),
                has_bootia32=bool(raw_content.get("has_bootia32")),
                has_opencore=bool(raw_content.get("has_opencore")),
                has_openduet=bool(raw_content.get("has_openduet")),
                linux_loaders=tuple(str(x) for x in loaders),
                has_bcd=bool(raw_content.get("has_bcd")),
                has_bios_bootmgr=bool(raw_content.get("has_bios_bootmgr")),
                has_boot_bcd=bool(raw_content.get("has_boot_bcd")),
                has_windows=bool(raw_content.get("has_windows")),
                product_build=raw_content.get("product_build") or None,
                edition=raw_content.get("edition") or None,
                bitness=raw_content.get("bitness") or None,
                loader_version=tuple(int(x) for x in version),
                has_install_wim=bool(raw_content.get("has_install_wim")),
                has_install_esd=bool(raw_content.get("has_install_esd")),
                has_boot_wim=bool(raw_content.get("has_boot_wim")),
                has_setup_exe=bool(raw_content.get("has_setup_exe")),
                media_version=raw_content.get("media_version") or None,
                has_winre=bool(raw_content.get("has_winre")),
                free_bytes=raw_content.get("free_bytes"),
                total_bytes=raw_content.get("total_bytes"),
                notes=tuple(raw_content.get("notes") or ()),
            )
        paths = raw.get("access_paths") or []
        volumes.append(
            make_volume(
                disk=int(raw.get("disk")),
                partition=raw.get("partition"),
                style=str(raw.get("style") or ""),
                gpt_type=str(raw.get("gpt_type") or ""),
                mbr_type=raw.get("mbr_type"),
                type_name=str(raw.get("type_name") or ""),
                filesystem=str(raw.get("filesystem") or ""),
                label=str(raw.get("label") or ""),
                size_bytes=raw.get("size_bytes"),
                letter=raw.get("letter") or None,
                access_paths=tuple(str(p) for p in paths),
                is_boot=bool(raw.get("is_boot")),
                is_system=bool(raw.get("is_system")),
                is_active=bool(raw.get("is_active")),
                bitlocker=str(raw.get("bitlocker") or "off"),
                volume_guid=str(raw.get("volume_guid") or ""),
                bus=str(raw.get("bus") or "unknown"),
                serial=str(raw.get("serial") or ""),
                offset=raw.get("offset"),
                content=content,
            )
        )
    boot_disk = payload.get("boot_disk")
    return DriveMap(
        volumes=tuple(volumes),
        firmware=str(payload.get("firmware") or "UEFI"),
        boot_disk=None if boot_disk is None else int(boot_disk),
        windows_letter=(payload.get("windows_letter") or None),
        windows_volume_guid=str(payload.get("windows_volume_guid") or ""),
        source=str(payload.get("source") or "payload"),
        captured_at=float(payload.get("captured_at") or 0),
    )


class MapPoller:
    """Refresh on a timer during long operations and always on a boot-critical step."""

    def __init__(
        self,
        scanner: Callable[[], DriveMap],
        *,
        interval_s: float = 15.0,
        clock: Callable[[], float] | None = None,
        os_arch: str = "x64",
        cpu_64: bool = True,
        secure_boot: str = "unknown",
        folder_fallback: str = "esp-mount",
    ) -> None:
        self.scanner = scanner
        self.interval_s = interval_s
        self.clock = clock or (lambda: 0.0)
        self.os_arch = os_arch
        self.cpu_64 = cpu_64
        self.secure_boot = secure_boot
        self.folder_fallback = folder_fallback
        self.map: DriveMap | None = None
        self.decision: BootDecision | None = None
        self._last: float | None = None
        self.refreshes = 0

    def refresh(self, reason: str) -> BootDecision:
        del reason  # the caller logs the reason; the plan itself is the result
        current = self.scanner()
        decision = decide(
            current,
            previous=self.map,
            os_arch=self.os_arch,
            cpu_64=self.cpu_64,
            secure_boot=self.secure_boot,
            folder_fallback=self.folder_fallback,
        )
        self.map = current
        self.decision = decision
        self._last = self.clock()
        self.refreshes += 1
        return decision

    def before_step(self, step: str) -> BootDecision:
        return self.refresh(step)

    def poll(self, now: float | None = None) -> BootDecision | None:
        if self.decision is None or self._last is None:
            return self.refresh("poll")
        moment = self.clock() if now is None else now
        if moment - self._last >= self.interval_s:
            return self.refresh("poll")
        return self.decision

