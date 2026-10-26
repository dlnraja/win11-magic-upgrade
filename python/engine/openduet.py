"""Optional OpenDuet + OpenCore bridge for a legacy-only BIOS PC.

Chain (software UEFI on top of BIOS):

    BIOS → boot0 (MBR) → boot1f32 (FAT32 VBR) → OpenDuet (`boot`) → OpenCore → OS loader

The Windows loader OpenCore should reach is \\EFI\\Microsoft\\Boot\\bootmgfw.efi.
OpenCore 1.0.7's ocvalidate flags that exact path as redundant when ScanPolicy is 0,
because the default scan already finds it. The Windows config therefore leaves
BlessOverride empty. A non-standard loader path (the article used a Linux grub
path) is written into BlessOverride and does pass ocvalidate.

Pinned to OpenCorePkg 1.0.7. 1.0.8 (2026-09-27) is not used until this config
contract is re-checked against that release's Sample.plist and ocvalidate.

The stick has to stay plugged in; firmware has no UEFI of its own.
Nothing is written to a disk unless the caller passes a confirm token that
matches a non-system target. `--dry-run` and a missing token only print the plan.

Method reference (rewritten, not copied):
https://chainesteve.be/ajouter-uefi-pc-bios-legacy-opendued-opencore/
Boot-sector splice follows OpenCorePkg Utilities/LegacyBoot/BootInstallBase.sh.
"""
from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import ssl
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .logutil import STATE_DIR, log

OPENCORE_VERSION = "1.0.7"
OPENCORE_ZIP_NAME = f"OpenCore-{OPENCORE_VERSION}-RELEASE.zip"
OPENCORE_URL = (
    "https://github.com/acidanthera/OpenCorePkg/releases/download/"
    f"{OPENCORE_VERSION}/{OPENCORE_ZIP_NAME}"
)
# sha256 of the official 1.0.7 RELEASE asset (2026-03-20).
OPENCORE_SHA256 = "2ffab6ebf58c7aefb0bcb3a1a385d207746823d6dd87d44bd666e1286939943e"
OPENCORE_NEWER_NOTE = (
    "OpenCore 1.0.8 was published on 2026-09-27. This bridge stays on 1.0.7 "
    "until Sample.plist keys and ocvalidate are checked again."
)
WINDOWS_LOADER = r"\EFI\Microsoft\Boot\bootmgfw.efi"
USER_AGENT = "Win11MagicUpgrade/1.54 (openduet; +https://github.com/dlnraja/win11-magic-upgrade)"
LARGE_DISK_BYTES = 128 * (1024**3)
SOURCE_CREDIT = "https://chainesteve.be/ajouter-uefi-pc-bios-legacy-opendued-opencore/"


class OpenDuetError(RuntimeError):
    pass


class DiskWriteRefused(OpenDuetError):
    pass


def normalize_efi_path(path: str) -> str:
    text = (path or "").replace("/", "\\")
    if text and not text.startswith("\\"):
        text = "\\" + text
    return text


def is_redundant_windows_loader(path: str) -> bool:
    return normalize_efi_path(path).lower() == WINDOWS_LOADER.lower()


def bless_from_env() -> str | None:
    raw = (os.environ.get("MAGIC_OPENDUET_BLESS") or "").strip()
    return raw or None


def _driver_list(cfg: dict) -> list:
    uefi = cfg.setdefault("UEFI", {})
    drivers = uefi.setdefault("Drivers", [])
    if not isinstance(drivers, list):
        drivers = []
        uefi["Drivers"] = drivers
    return drivers


def _set_driver(drivers: list, name: str, enabled: bool) -> None:
    wanted = name.lower()
    for entry in drivers:
        if isinstance(entry, dict) and str(entry.get("Path", "")).lower() == wanted:
            entry["Enabled"] = enabled
            return
    proto: dict = {}
    if drivers and isinstance(drivers[0], dict):
        proto = dict(drivers[0])
    entry = dict(proto)
    entry["Path"] = name
    entry["Enabled"] = enabled
    entry.setdefault("Arguments", "")
    entry.setdefault("Comment", name)
    entry.setdefault("LoadEarly", False)
    drivers.append(entry)


def apply_windows_bridge_config(cfg: dict, *, bless_path: str | None = None) -> dict:
    """Apply the legacy-BIOS bridge keys. Does not copy article text into the plist."""
    misc = cfg.setdefault("Misc", {})
    security = misc.setdefault("Security", {})
    security["ScanPolicy"] = 0
    security["SecureBootModel"] = "Disabled"
    security["Vault"] = "Optional"
    bless = misc.get("BlessOverride")
    if not isinstance(bless, list):
        bless = []
    if bless_path and not is_redundant_windows_loader(bless_path):
        norm = normalize_efi_path(bless_path)
        if norm not in bless:
            bless.append(norm)
    misc["BlessOverride"] = bless
    uefi = cfg.setdefault("UEFI", {})
    inp = uefi.setdefault("Input", {})
    inp["KeySupport"] = False
    drivers = _driver_list(cfg)
    _set_driver(drivers, "HfsPlus.efi", False)
    _set_driver(drivers, "OpenUsbKbDxe.efi", True)
    return cfg


def config_problems(cfg: dict) -> list[str]:
    problems: list[str] = []
    security = ((cfg.get("Misc") or {}).get("Security") or {})
    if security.get("ScanPolicy") != 0:
        problems.append("ScanPolicy")
    if security.get("SecureBootModel") != "Disabled":
        problems.append("SecureBootModel")
    if security.get("Vault") != "Optional":
        problems.append("Vault")
    if ((cfg.get("UEFI") or {}).get("Input") or {}).get("KeySupport") is not False:
        problems.append("KeySupport")
    drivers = ((cfg.get("UEFI") or {}).get("Drivers") or [])
    flags = {
        str(d.get("Path", "")).lower(): d.get("Enabled")
        for d in drivers
        if isinstance(d, dict)
    }
    if flags.get("hfsplus.efi") is not False:
        problems.append("HfsPlus.efi")
    if flags.get("openusbkbdxe.efi") is not True:
        problems.append("OpenUsbKbDxe.efi")
    return problems


def ocvalidate_acceptable(returncode: int, output: str) -> tuple[bool, str]:
    """Exit 0 is success. A lone 'redundant' note for bootmgfw.efi is also accepted."""
    text = output or ""
    if returncode == 0:
        return True, "ok"
    redundant = re.findall(r"BlessOverride:\s*(\S+)\s+is redundant", text, re.I)
    other_error = bool(re.search(r"returns \d+ error", text, re.I)) and not redundant
    if (
        redundant
        and all(is_redundant_windows_loader(p) for p in redundant)
        and not other_error
        and "found 1 issue" in text.lower()
    ):
        return True, "windows_bless_redundant"
    return False, text.strip()[:500] or f"ocvalidate exit {returncode}"


def splice_mbr(existing: bytes, boot0: bytes) -> bytes:
    """Install boot0 code. Partition table (bytes 446:512) stays."""
    if len(existing) < 512 or len(boot0) < 446:
        raise OpenDuetError("MBR or boot0 is shorter than a boot sector")
    out = bytearray(existing[:512])
    out[:446] = boot0[:446]
    if out[510:512] != b"\x55\xAA":
        out[510:512] = b"\x55\xAA"
    return bytes(out)


def splice_pbr(
    existing: bytes,
    boot1f32: bytes,
    rng: Callable[[int], bytes] | None = None,
) -> bytes:
    """Install boot1f32. Keep the formatted FAT32 BPB (87 bytes at offset 3).

    Bytes 496:510 are replaced with random data, matching OpenCore BootInstall.
    """
    if len(existing) < 512 or len(boot1f32) < 512:
        raise OpenDuetError("PBR or boot1f32 is shorter than a boot sector")
    source = rng or os.urandom
    out = bytearray(boot1f32[:512])
    out[3:90] = existing[3:90]
    out[496:510] = source(14)
    out[510:512] = b"\x55\xAA"
    return bytes(out)


def install_boot_code_files(
    mbr_path: str,
    pbr_path: str,
    boot0: bytes,
    boot1f32: bytes,
    *,
    rng: Callable[[int], bytes] | None = None,
    system_disk: int | None = None,
    target_disk: int | None = None,
) -> dict[str, bytes]:
    """Splice boot code into two sector files or raw devices. Returns previous bytes."""
    from .rawdisk import RawDiskError, assert_sector_target, read_at, write_at

    for path in (mbr_path, pbr_path):
        if path.startswith("\\\\.\\") or path.startswith("//./"):
            try:
                assert_sector_target(path=path, system_disk=system_disk, target_disk=target_disk)
            except RawDiskError as exc:
                raise DiskWriteRefused(str(exc)) from exc
    old_mbr = read_at(mbr_path, 0, 512)
    old_pbr = read_at(pbr_path, 0, 512)
    write_at(mbr_path, 0, splice_mbr(old_mbr, boot0))
    write_at(pbr_path, 0, splice_pbr(old_pbr, boot1f32, rng))
    return {"mbr": old_mbr, "pbr": old_pbr}


@dataclass(frozen=True)
class TargetDisk:
    number: int
    size_bytes: int
    bus: str
    boot_disk: bool
    style: str = "unknown"
    model: str = ""


def confirm_token(disk: TargetDisk, *, partition: bool) -> str:
    bus = re.sub(r"\s+", "", (disk.bus or "unknown").lower())
    return (
        f"{int(disk.number)}|{int(disk.size_bytes)}|{bus}|"
        f"boot={'yes' if disk.boot_disk else 'no'}|part={'yes' if partition else 'no'}"
    )


def _size_to_bytes(number: str, unit: str) -> int:
    mult = {
        "B": 1,
        "O": 1,
        "KB": 1024,
        "KO": 1024,
        "MB": 1024**2,
        "MO": 1024**2,
        "GB": 1024**3,
        "GO": 1024**3,
        "TB": 1024**4,
        "TO": 1024**4,
    }
    return int(float(number.replace(",", "")) * mult[unit.upper()])


def parse_list_disk(text: str) -> list[TargetDisk]:
    disks: list[TargetDisk] = []
    for line in (text or "").splitlines():
        match = re.search(r"(?:Disk|Disque)\s+(\d+)\b(.*)$", line, re.I)
        if not match:
            continue
        rest = match.group(2)
        size = re.search(r"([\d.,]+)\s*(KB|MB|GB|TB|Ko|Mo|Go|To)\b", rest, re.I)
        if not size:
            continue
        gpt = "*" in rest
        disks.append(
            TargetDisk(
                number=int(match.group(1)),
                size_bytes=_size_to_bytes(size.group(1), size.group(2)),
                bus="unknown",
                boot_disk=False,
                style="gpt" if gpt else "mbr",
            )
        )
    return disks


def parse_detail_disk(text: str) -> dict[str, str]:
    boot = re.search(
        r"(?:Boot Disk|Disque de d[eé]marrage)\s*:\s*(\w+)",
        text or "",
        re.I,
    )
    kind = re.search(r"^Type\s*:\s*(.+)$", text or "", re.I | re.M)
    model = ""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped and not re.match(r"(Disk|Disque|Type|Status|Statut)\b", stripped, re.I):
            model = stripped
            break
    boot_word = (boot.group(1) if boot else "no").lower()
    return {
        "boot": "yes" if boot_word in ("yes", "oui", "true") else "no",
        "type": (kind.group(1).strip() if kind else ""),
        "model": model,
    }


def classify_bus(type_line: str, model: str = "") -> str:
    blob = f"{type_line} {model}".lower()
    if "usb" in blob or "removable" in blob or "amovible" in blob:
        return "usb"
    if "nvme" in blob:
        return "nvme"
    if any(token in blob for token in ("sata", "ata", "scsi", "raid", "sas")):
        return "sata"
    return "unknown"


def describe_target(list_text: str, detail_text: str, number: int) -> TargetDisk:
    match = next((d for d in parse_list_disk(list_text) if d.number == int(number)), None)
    detail = parse_detail_disk(detail_text)
    bus = classify_bus(detail.get("type", ""), detail.get("model", ""))
    if match is None:
        return TargetDisk(
            number=int(number),
            size_bytes=0,
            bus=bus,
            boot_disk=detail.get("boot") == "yes",
            model=detail.get("model", ""),
        )
    return TargetDisk(
        number=match.number,
        size_bytes=match.size_bytes,
        bus=bus if bus != "unknown" else match.bus,
        boot_disk=detail.get("boot") == "yes" or match.boot_disk,
        style=match.style,
        model=detail.get("model", ""),
    )


def diskpart_create_gpt_fat32(disk: int, letter: str) -> str:
    """Destructive GPT+FAT32 layout. Only run after authorize_write says commit."""
    letter = letter.upper()[:1]
    return (
        f"select disk {int(disk)}\n"
        "clean\n"
        "convert gpt\n"
        "create partition primary\n"
        "format fs=fat32 quick label=OPENCORE\n"
        f"assign letter={letter}\n"
        "exit\n"
    )


def authorize_write(
    target: TargetDisk,
    *,
    confirm: str,
    dry_run: bool,
    allow_fixed: bool,
    partition: bool,
    system_disk: int | None,
    allow_large: bool = False,
    volume_letter: str = "",
) -> str:
    """Return 'dry-run' or 'commit'. Raise DiskWriteRefused otherwise."""
    if target.number < 0:
        raise DiskWriteRefused("target disk number is unknown")
    if system_disk is None or int(system_disk) < 0:
        raise DiskWriteRefused("system disk number is unknown — refusing to write")
    if int(target.number) == int(system_disk) or target.boot_disk:
        raise DiskWriteRefused(
            f"refusing disk {target.number}: it is the system/boot disk"
        )
    sys_letter = (os.environ.get("SystemDrive", "C:") or "C:")[:1].upper()
    if volume_letter and volume_letter.upper()[:1] == sys_letter:
        raise DiskWriteRefused("refusing the system drive letter")
    removable = target.bus == "usb"
    if not removable and not allow_fixed:
        raise DiskWriteRefused(
            "target is not a USB disk — pass --openduet-allow-fixed for a non-system data disk"
        )
    if partition and target.size_bytes > LARGE_DISK_BYTES and not allow_large:
        raise DiskWriteRefused(
            "refusing to partition a disk larger than 128 GiB "
            "(set MAGIC_OPENDUET_ALLOW_LARGE=1 if this USB really is that big)"
        )
    if partition and not volume_letter and dry_run:
        pass
    expected = confirm_token(target, partition=partition)
    if dry_run or not (confirm or "").strip():
        return "dry-run"
    if confirm.strip() != expected:
        raise DiskWriteRefused(
            f"confirm token does not match this disk (expected {expected})"
        )
    return "commit"


def _zip_names(zf: zipfile.ZipFile) -> list[str]:
    return [n for n in zf.namelist() if n and not n.endswith("/")]


def _find_name(names: list[str], suffix: str) -> str:
    want = suffix.replace("\\", "/").lower()
    hits = [n for n in names if n.replace("\\", "/").lower().endswith(want)]
    if not hits:
        raise OpenDuetError(f"{suffix} is missing from the OpenCore archive")
    hits.sort(key=len)
    return hits[0]


def _legacy_duet_member(names: list[str]) -> str:
    hits = []
    for name in names:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if base.lower() == "bootx64" and "legacyboot" in name.replace("\\", "/").lower():
            hits.append(name)
    if not hits:
        raise OpenDuetError("Utilities/LegacyBoot/bootX64 is missing from the OpenCore archive")
    return hits[0]


def stage_openduet_tree(
    zip_path: Path,
    dest: Path,
    *,
    bless_path: str | None = None,
) -> Path:
    """Build the stick folder: EFI tree, `boot`, boot sectors, config.plist, readme."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = _zip_names(zf)
        sample = zf.read(_find_name(names, "Docs/Sample.plist"))
        cfg = plistlib.loads(sample)
        apply_windows_bridge_config(cfg, bless_path=bless_path)
        problems = config_problems(cfg)
        if problems:
            raise OpenDuetError("config contract failed: " + ", ".join(problems))
        oc_dir = dest / "EFI" / "OC"
        oc_dir.mkdir(parents=True, exist_ok=True)
        (oc_dir / "config.plist").write_bytes(
            plistlib.dumps(cfg, fmt=plistlib.FMT_XML, sort_keys=False)
        )
        prefix = "x64/efi/"
        for name in names:
            norm = name.replace("\\", "/").lower()
            if not norm.startswith(prefix):
                continue
            # Preserve EFI/... under the stick root regardless of archive case.
            tail = name.replace("\\", "/").split("/", 1)[1]
            target = dest / Path(tail)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
        boot = zf.read(_legacy_duet_member(names))
        (dest / "boot").write_bytes(boot)
        legacy = dest / "LegacyBoot"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "boot0").write_bytes(zf.read(_find_name(names, "Utilities/LegacyBoot/boot0")))
        (legacy / "boot1f32").write_bytes(zf.read(_find_name(names, "Utilities/LegacyBoot/boot1f32")))
        (legacy / "bootX64").write_bytes(boot)
        for tool in ("ocvalidate.exe", "ocvalidate.linux", "ocvalidate"):
            try:
                member = _find_name(names, f"Utilities/ocvalidate/{tool}")
            except OpenDuetError:
                continue
            tool_dir = dest / "Utilities" / "ocvalidate"
            tool_dir.mkdir(parents=True, exist_ok=True)
            (tool_dir / tool).write_bytes(zf.read(member))
    (dest / "OPENDUET-README.txt").write_text(bridge_readme(), encoding="utf-8")
    return dest


def bridge_readme() -> str:
    return "\n".join(
        [
            "Win11 Magic Upgrade — OpenDuet / OpenCore legacy BIOS bridge",
            "============================================================",
            f"OpenCorePkg {OPENCORE_VERSION} (pinned). {OPENCORE_NEWER_NOTE}",
            "",
            "What this stick does",
            "  A PC whose firmware is BIOS-only cannot run a UEFI boot loader.",
            "  This USB stick is a permanent bridge:",
            "    BIOS firmware → boot0 → boot1f32 → OpenDuet (file `boot`) → OpenCore",
            f"    → Windows loader {WINDOWS_LOADER} on the internal disk.",
            "  Leave the stick plugged in. Unplugging it returns the PC to plain BIOS boot.",
            "",
            "Layout",
            "  GPT disk, one FAT32 volume.",
            "  MBR code is OpenCore LegacyBoot/boot0 (partition table kept).",
            "  FAT32 boot sector is boot1f32 with the formatted BPB kept.",
            "  Root file `boot` is LegacyBoot/bootX64.",
            "  EFI/OC/config.plist: ScanPolicy 0, SecureBootModel Disabled, Vault Optional,",
            "  HfsPlus.efi off, OpenUsbKbDxe.efi on (USB keyboard on some machines),",
            "  KeySupport false.",
            f"  BlessOverride is empty for Windows because OpenCore already scans {WINDOWS_LOADER}.",
            "  Set MAGIC_OPENDUET_BLESS to a different loader path if you need one.",
            "",
            "Safety",
            "  The tool will not write a disk without a confirm token from a dry-run,",
            "  and it refuses the system disk even if the token is forced.",
            "  Partitioning (clean + GPT + FAT32) is a separate flag and erases that disk.",
            "",
            f"Background (not a copy of the article): {SOURCE_CREDIT}",
            "OpenCore legacy boot install: https://github.com/acidanthera/OpenCorePkg",
            "",
        ]
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_opencore_zip(cache_dir: Path | None = None) -> Path:
    cache_dir = cache_dir or (STATE_DIR / "openduet")
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / OPENCORE_ZIP_NAME
    if dest.is_file() and _sha256(dest) == OPENCORE_SHA256:
        return dest
    log(f"Downloading OpenCorePkg {OPENCORE_VERSION} RELEASE (pinned)...", "STEP")
    req = urllib.request.Request(OPENCORE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=180) as resp:
        dest.write_bytes(resp.read())
    got = _sha256(dest)
    if got != OPENCORE_SHA256:
        dest.unlink(missing_ok=True)
        raise OpenDuetError(
            f"OpenCore zip sha256 mismatch (got {got}, expected {OPENCORE_SHA256})"
        )
    log(f"OpenCore {OPENCORE_VERSION} cached ({dest.stat().st_size} bytes)", "OK")
    return dest


def _ocvalidate_binary(dest: Path) -> Path | None:
    folder = dest / "Utilities" / "ocvalidate"
    if os.name == "nt":
        candidate = folder / "ocvalidate.exe"
    else:
        candidate = folder / "ocvalidate.linux"
        if not candidate.is_file():
            candidate = folder / "ocvalidate"
    return candidate if candidate.is_file() else None


def run_ocvalidate(dest: Path) -> dict[str, str | bool]:
    binary = _ocvalidate_binary(dest)
    config = dest / "EFI" / "OC" / "config.plist"
    if binary is None or not config.is_file() or binary.stat().st_size < 50_000:
        return {"ran": False, "ok": True, "detail": "ocvalidate skipped (binary not staged)"}
    import subprocess

    if os.name != "nt":
        try:
            binary.chmod(binary.stat().st_mode | 0o111)
        except OSError:
            pass
    try:
        proc = subprocess.run(
            [str(binary), str(config)],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ran": True, "ok": False, "detail": str(exc)}
    output = (proc.stdout or "") + (proc.stderr or "")
    ok, detail = ocvalidate_acceptable(proc.returncode, output)
    return {"ran": True, "ok": ok, "detail": detail}


def prepare_openduet_stick(
    dest: Path | None = None,
    *,
    bless_path: str | None = None,
    zip_path: Path | None = None,
) -> Path:
    """Download (unless zip_path is given), stage files, run ocvalidate. No disk write."""
    if bless_path is None:
        bless_path = bless_from_env()
    archive = zip_path or ensure_opencore_zip()
    out = dest or (STATE_DIR / "openduet" / "stick")
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    stage_openduet_tree(archive, out, bless_path=bless_path)
    checked = run_ocvalidate(out)
    if not checked.get("ok"):
        raise OpenDuetError(f"ocvalidate rejected config.plist: {checked.get('detail')}")
    log(f"OpenDuet stick files ready at {out} ({checked.get('detail')})", "OK")
    return out


def _argv_option(argv: list[str], flag: str) -> str | None:
    low_flag = flag.lower()
    for index, arg in enumerate(argv):
        if arg.lower() == low_flag and index + 1 < len(argv):
            return argv[index + 1]
        if arg.lower().startswith(low_flag + "="):
            return arg.split("=", 1)[1]
    return None


def run_openduet_cli(argv: list[str]) -> int:
    """`--openduet-prepare` stages files. `--openduet-write` needs a confirm token."""
    low = [a.lower() for a in argv]
    bless = _argv_option(argv, "--bless") or bless_from_env()
    if "--openduet-prepare" in low and "--openduet-write" not in low:
        path = prepare_openduet_stick(bless_path=bless)
        print(path)
        return 0
    if "--openduet-write" not in low:
        return 2
    disk_raw = _argv_option(argv, "--disk")
    if disk_raw is None or not disk_raw.lstrip("-").isdigit():
        print("OpenDuet write needs --disk N (a disk that is not the system disk).")
        return 2
    partition = "--openduet-partition" in low
    allow_fixed = "--openduet-allow-fixed" in low
    dry = "--dry-run" in low or "--dryrun" in low or not (_argv_option(argv, "--confirm") or "").strip()
    allow_large = os.environ.get("MAGIC_OPENDUET_ALLOW_LARGE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    volume = (_argv_option(argv, "--volume") or "").strip()
    try:
        result = write_openduet_disk(
            disk=int(disk_raw),
            confirm=_argv_option(argv, "--confirm") or "",
            dry_run=dry,
            partition=partition,
            allow_fixed=allow_fixed,
            allow_large=allow_large,
            volume_letter=volume,
            bless_path=bless,
        )
    except (OpenDuetError, OSError) as exc:
        log(str(exc), "ERROR")
        print(str(exc))
        return 2
    if result.get("dry_run"):
        print("DRY-RUN — no sectors were written and the disk was not partitioned.")
        print(f"CONFIRM TOKEN: {result.get('token')}")
        print("Re-run with --confirm <token> to write that disk.")
        for line in result.get("plan") or []:
            print(f"  - {line}")
        return 0
    print(result.get("path") or "OpenDuet write finished")
    return 0 if result.get("ok") else 2


def write_openduet_disk(
    *,
    disk: int,
    confirm: str,
    dry_run: bool,
    partition: bool,
    allow_fixed: bool,
    allow_large: bool = False,
    volume_letter: str = "",
    bless_path: str | None = None,
    list_text: str | None = None,
    detail_text: str | None = None,
    system_disk: int | None = None,
    zip_path: Path | None = None,
) -> dict:
    """Plan or perform a stick write. Injected texts skip live diskpart (tests)."""
    from .diskpart_safe import get_system_disk_number, run_diskpart

    if system_disk is None and list_text is None:
        system_disk = get_system_disk_number()
    if list_text is None or detail_text is None:
        ok, listed = run_diskpart("list disk\nexit\n")
        if not ok and list_text is None:
            raise OpenDuetError(f"list disk failed: {listed[:240]}")
        list_text = listed if list_text is None else list_text
        ok, detail = run_diskpart(f"select disk {int(disk)}\ndetail disk\nexit\n")
        if not ok and detail_text is None:
            raise OpenDuetError(f"detail disk failed: {detail[:240]}")
        detail_text = detail if detail_text is None else detail_text
    target = describe_target(list_text or "", detail_text or "", disk)
    decision = authorize_write(
        target,
        confirm=confirm,
        dry_run=dry_run,
        allow_fixed=allow_fixed,
        partition=partition,
        system_disk=system_disk,
        allow_large=allow_large,
        volume_letter=volume_letter,
    )
    token = confirm_token(target, partition=partition)
    plan = [
        f"target disk {target.number} ({target.bus}, {target.size_bytes} bytes, boot={target.boot_disk})",
        "refuse if this number is the system disk",
        "stage OpenCore " + OPENCORE_VERSION + " files and run ocvalidate",
    ]
    if partition:
        plan.append("DISKPART clean + GPT + FAT32 (erases this disk only)")
    else:
        plan.append("keep existing partitions; require --volume LETTER on a FAT32 volume of this disk")
    plan.append("copy EFI + boot, then splice boot0 into the MBR and boot1f32 into the FAT32 VBR")
    if decision != "commit":
        return {"ok": True, "dry_run": True, "token": token, "plan": plan, "disk": target.number}
    if not partition and not volume_letter:
        raise DiskWriteRefused(
            "existing-disk write needs --volume E (FAT32 on that disk). "
            "Use --openduet-partition to create a new GPT+FAT32 layout."
        )
    staged = prepare_openduet_stick(bless_path=bless_path, zip_path=zip_path)
    letter = volume_letter.upper()[:1]
    if partition:
        letter = letter or "Y"
        script = diskpart_create_gpt_fat32(disk, letter)
        ok, out = run_diskpart(script)
        log(f"diskpart GPT+FAT32 → {ok}: {out[:300]}", "OK" if ok else "ERROR")
        if not ok:
            raise OpenDuetError("diskpart failed; disk was not assumed to be an OpenDuet stick")
    _copy_tree_to_volume(staged, Path(f"{letter}:\\"))
    _splice_live_sectors(
        disk=disk,
        letter=letter,
        staged=staged,
        system_disk=int(system_disk),
    )
    log(
        f"OpenDuet bridge written to disk {disk} ({letter}:). Leave this stick plugged in.",
        "WARN",
    )
    return {"ok": True, "dry_run": False, "token": token, "path": str(staged), "letter": letter}


def _copy_tree_to_volume(staged: Path, root: Path) -> None:
    if not root.exists():
        raise OpenDuetError(f"volume {root} is not mounted")
    for name in ("EFI", "boot"):
        src = staged / name
        dst = root / name
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
        elif src.is_file():
            shutil.copy2(src, dst)
    readme = staged / "OPENDUET-README.txt"
    if readme.is_file():
        shutil.copy2(readme, root / readme.name)


def _splice_live_sectors(*, disk: int, letter: str, staged: Path, system_disk: int) -> None:
    boot0 = (staged / "LegacyBoot" / "boot0").read_bytes()
    boot1 = (staged / "LegacyBoot" / "boot1f32").read_bytes()
    # Dismount so the volume boot sector is not cached over our splice.
    import subprocess

    subprocess.run(
        ["fsutil", "volume", "dismount", f"{letter}:"],
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    install_boot_code_files(
        rf"\\.\PhysicalDrive{int(disk)}",
        rf"\\.\{letter}:",
        boot0,
        boot1,
        system_disk=system_disk,
        target_disk=int(disk),
    )
