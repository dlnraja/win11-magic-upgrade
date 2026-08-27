"""
OEM-aware adaptation for Acer / Asus / Toshiba / Dell / HP / Lenovo / MSI / etc.

Covers:
  - Manufacturer / model / SKU detection (WMI + registry)
  - Encryption: BitLocker, Device Encryption, Toshiba HDD password / SED / eDrive
  - OEM digital license (MSDM ACPI / OA3 entitlement) — preserve, never wipe
  - Per-OEM ESP cleanup policy + recovery partition awareness
  - Partition / mbr2gpt hints tuned to typical OEM layouts

Does NOT decrypt ATA HDD passwords or remove BitLocker keys without user action.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log

# EFI vendor folders we never treat as Linux GRUB
OEM_EFI_VENDORS = frozenset(
    {
        "dell",
        "hp",
        "hewlett-packard",
        "lenovo",
        "asus",
        "acer",
        "toshiba",
        "dynabook",
        "msi",
        "gigabyte",
        "asrock",
        "intel",
        "ami",
        "phoenix",
        "insyde",
        "samsung",
        "sony",
        "fujitsu",
        "panasonic",
        "microsoft",
        "boot",
        "oem",
        "gateway",
        "packard",
        "mediatek",
        "realtek",
    }
)

# Typical OEM recovery / utility partition labels (EN + common)
OEM_RECOVERY_LABELS = re.compile(
    r"RECOVERY|RECOVER|RESTORE|PQSERVICE|FACTORY|"
    r"HP_RECOVERY|HP_TOOLS|DELLSUPPORT|DELLDIAGS|"
    r"LENOVO|LENOVO_PART|IBM_SERVICE|"
    r"ACER|ASUS|ASUSCORE|MYASUS|"
    r"TOSHIBA|DYNABOOK|SSD\s*Recovery|"
    r"WINRE|Recovery|"
    r"OEM|TOOLS|DIAGNOSTICS",
    re.I,
)


@dataclass
class OemProfile:
    family: str  # acer|asus|toshiba|dell|hp|lenovo|msi|generic|unknown
    manufacturer: str = ""
    model: str = ""
    sku: str = ""
    bios_vendor: str = ""
    # Encryption
    bitlocker: str = "unknown"  # on|off|locked|unknown
    device_encryption: bool = False
    toshiba_hdd_password_likely: bool = False
    sed_edrive_likely: bool = False
    encryption_blocks_mutate: bool = False
    encryption_notes: list[str] = field(default_factory=list)
    # License
    msdm_present: bool = False
    digital_license_likely: bool = False
    activation_status: str = ""
    license_notes: list[str] = field(default_factory=list)
    # Layout policy
    preserve_oem_efi_strict: bool = False
    preserve_recovery_partitions: bool = True
    prefer_new_esp_over_grow: bool = False
    mbr2gpt_disable_winre_first: bool = True
    esp_cleanup_max_file_kb: int = 512
    keep_efi_suffixes: list[str] = field(default_factory=list)
    # Guidance
    guidance: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(cmd: list[str], timeout: int = 90) -> tuple[int, str]:
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


def _ps(script: str, timeout: int = 120) -> tuple[int, str]:
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


def _wmi_csproduct() -> dict[str, str]:
    """Manufacturer / Model / Name via Win32_ComputerSystemProduct + ComputerSystem."""
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$p = Get-CimInstance Win32_ComputerSystemProduct
$c = Get-CimInstance Win32_ComputerSystem
$b = Get-CimInstance Win32_BIOS
[PSCustomObject]@{
  Vendor = [string]$p.Vendor
  Name = [string]$p.Name
  Version = [string]$p.Version
  Manufacturer = [string]$c.Manufacturer
  Model = [string]$c.Model
  SystemSKU = [string]$c.SystemSKUNumber
  BiosManufacturer = [string]$b.Manufacturer
  BiosVersion = [string]$b.SMBIOSBIOSVersion
} | ConvertTo-Json -Compress
"""
    code, out = _ps(script, timeout=60)
    if code != 0 or not out.strip():
        return {}
    try:
        idx = out.find("{")
        data = json.loads(out[idx:] if idx >= 0 else out)
        return {k: str(v or "") for k, v in data.items()}
    except Exception:
        return {}


def _reg_oem() -> dict[str, str]:
    try:
        import winreg

        out: dict[str, str] = {}
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\OEMInformation",
        )
        for name in ("Manufacturer", "Model", "SupportURL"):
            try:
                out[name], _ = winreg.QueryValueEx(key, name)
            except OSError:
                pass
        winreg.CloseKey(key)
        return {k: str(v) for k, v in out.items()}
    except Exception:
        return {}


def classify_oem_family(manufacturer: str, model: str = "", bios: str = "") -> str:
    blob = f"{manufacturer} {model} {bios}".lower()
    rules = [
        ("toshiba", ("toshiba", "dynabook", "taec")),
        ("acer", ("acer", "gateway", "packard bell", "emachines")),
        ("asus", ("asus", "asustek", "rog ", " tuf ")),
        ("dell", ("dell", "alienware", "precision")),
        ("hp", ("hewlett", " hp", "hp ", "hewlett-packard", "omen")),
        ("lenovo", ("lenovo", "ibm", "thinkpad", "ideapad", "legion")),
        ("msi", ("micro-star", "msi ")),
        ("samsung", ("samsung",)),
        ("sony", ("sony", "vaio")),
        ("fujitsu", ("fujitsu",)),
        ("microsoft", ("microsoft corporation", "surface")),
    ]
    for family, needles in rules:
        if any(n.strip() in blob for n in needles):
            return family
    if "asus" in blob:
        return "asus"
    if re.search(r"\bhp\b", blob):
        return "hp"
    return "generic" if manufacturer.strip() else "unknown"


def _bitlocker_status(drive: str | None = None) -> str:
    """
    Return: locked | on | off | unknown
    IMPORTANT: encrypted volume with Protection Off is "off" (safe to mutate after suspend).
    Only "locked" must hard-block.
    """
    drive = drive or os.environ.get("SystemDrive", "C:")
    manage = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "manage-bde.exe"
    if not manage.is_file():
        return "unknown"
    code, out = _run([str(manage), "-status", drive], timeout=60)
    if code != 0:
        return "unknown"
    # Locked is the only hard-fail state
    if re.search(r"Lock Status:\s*Locked", out, re.I):
        return "locked"
    # Protection On => need -protectors -disable (non-destructive)
    if re.search(r"Protection\s*Status\s*:\s*On|Protection On", out, re.I):
        return "on"
    # Protection Off / Fully Decrypted / no protectors => OK to proceed
    if re.search(
        r"Protection\s*Status\s*:\s*Off|Protection Off|Fully Decrypted|No Key Protectors",
        out,
        re.I,
    ):
        return "off"
    return "unknown"


def _device_encryption_on() -> bool:
    """
    True only when Device Encryption / BitLocker protectors are actively On.
    FullyEncrypted + Protection Off (after suspend) must return False — safe to mutate.
    """
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$de = $false
$bl = Get-BitLockerVolume -MountPoint $env:SystemDrive -EA SilentlyContinue
if ($bl) {
  # ProtectionStatus: On=1 Off=0
  if ([string]$bl.ProtectionStatus -match 'On' -or [int]$bl.ProtectionStatus -eq 1) { $de = $true }
}
Write-Output ($(if ($de) {'1'} else {'0'}))
"""
    code, out = _ps(script, timeout=45)
    return code == 0 and (out or "").strip().endswith("1")


def _probe_toshiba_hdd_encryption() -> tuple[bool, bool, list[str]]:
    """
    Heuristics for Toshiba / Dynabook ATA HDD Password vs BitLocker.
    Software under HKLM\\SOFTWARE\\Toshiba is NOT proof of HDD password.
    Returns (hdd_password_likely, sed_likely, notes).
    """
    notes: list[str] = []
    hdd_pw = False
    sed = False
    # Registry / Toshiba tools remnants — soft note only
    for path in (
        r"SOFTWARE\Toshiba",
        r"SOFTWARE\Dynabook",
        r"SOFTWARE\WOW6432Node\Toshiba",
    ):
        try:
            import winreg

            winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
            notes.append(f"toshiba_software_present:{path.split(chr(92))[-1]}")
        except OSError:
            pass

    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$bl = Get-BitLockerVolume -MountPoint $env:SystemDrive -EA SilentlyContinue
if ($bl) {
  Write-Output ('ENC=' + [string]$bl.EncryptionMethod)
  Write-Output ('PROT=' + [string]$bl.ProtectionStatus)
  Write-Output ('VOL=' + [string]$bl.VolumeStatus)
}
$d = Get-Disk | Where-Object { $_.IsSystem } | Select-Object -First 1
if ($d) { Write-Output ('DISK_OK=' + [string]$d.Number) } else { Write-Output 'DISK_OK=none' }
"""
    code, out = _ps(script, timeout=45)
    blob = (out or "").lower()
    if "hardware" in blob or "xedtsaes" in blob or "aes_256_h" in blob:
        sed = True
        notes.append("bitlocker_hardware_encryption_hint")
    # Real ATA lock signal: cannot see system disk AND SystemRoot missing (checked by caller)
    if code != 0 or "disk_ok=none" in blob:
        notes.append("storage_query_weak_check_bios_hdd_password")
        # Do NOT set hdd_pw=True from weak query alone — BitLocker On machines still see the disk
    return hdd_pw, sed, notes


def _msdm_oa3_present() -> tuple[bool, list[str]]:
    """
    Check ACPI MSDM table (OEM digital product key for Windows).
    Presence means reinstall/activation usually auto-binds to hardware — preserve disk/ESP.
    """
    notes: list[str] = []
    # PowerShell: Get-CimInstance or firmware table via msinfo — use licensedia / WMI
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
# MSDM via Windows Licensing (OA3)
$oa = Get-CimInstance -Query "SELECT * FROM SoftwareLicensingService" -EA SilentlyContinue
$oa3 = $false
if ($oa -and $oa.OA3xOriginalProductKey) { $oa3 = $true; Write-Output ('OA3KEY=1') }
elseif ($oa -and $oa.OA3xOriginalProductKeyDescription) { $oa3 = $true; Write-Output ('OA3DESC=1') }
# Fallback: MSDM ACPI raw (needs elevation; may fail)
try {
  $msdm = Get-WmiObject -Namespace root\wmi -Class MSDM -EA SilentlyContinue
  if ($msdm) { Write-Output 'MSDM=1' }
} catch {}
# Digital license / Store activation
$lic = Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL AND LicenseStatus=1" -EA SilentlyContinue |
  Where-Object { $_.Name -match 'Windows' } | Select-Object -First 1
if ($lic) { Write-Output ('LICENSED=1'); Write-Output ('LICNAME=' + $lic.Name) }
Write-Output ('DONE')
"""
    code, out = _ps(script, timeout=60)
    text = out or ""
    msdm = bool(re.search(r"OA3KEY=1|OA3DESC=1|MSDM=1", text))
    if re.search(r"LICENSED=1", text):
        notes.append("windows_licensed")
    if msdm:
        notes.append("oem_digital_key_msdm_or_oa3")
    if code != 0 and not msdm:
        notes.append("license_probe_incomplete")
    return msdm, notes


def _activation_partial() -> str:
    code, out = _run(
        ["cscript", "//Nologo", str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "slmgr.vbs"), "/dli"],
        timeout=45,
    )
    if code != 0:
        return "unknown"
    if re.search(r"licensed", out, re.I):
        return "licensed"
    if re.search(r"notification|grace|unlicensed", out, re.I):
        return "not_fully_licensed"
    return "unknown"


def build_oem_policy(family: str) -> dict[str, Any]:
    """Per-OEM knobs for ESP cleanup, expand strategy, recovery."""
    # Defaults
    pol: dict[str, Any] = {
        "preserve_oem_efi_strict": False,
        "preserve_recovery_partitions": True,
        "prefer_new_esp_over_grow": False,
        "mbr2gpt_disable_winre_first": True,
        "esp_cleanup_max_file_kb": 512,
        "keep_efi_suffixes": [".efi", ".dll", ".nsh", ".csv", ".xml", ".ini", ".txt"],
        "guidance": [],
    }
    if family == "toshiba":
        pol.update(
            {
                "preserve_oem_efi_strict": True,
                "prefer_new_esp_over_grow": True,
                "esp_cleanup_max_file_kb": 256,
                "guidance": [
                    "Toshiba/Dynabook: if HDD Password (BIOS ATA Security) is set, unlock in BIOS before any partition/boot edit.",
                    "Device Encryption / BitLocker Protection On: suspend protectors (safe). Only LOCKED volumes refuse expand.",
                    "Prefer NEW ESP after shrink C: rather than moving OEM recovery partitions.",
                    "OEM Windows digital license (MSDM) stays with motherboard — keep disk, do not wipe.",
                ],
            }
        )
    elif family == "acer":
        pol.update(
            {
                "preserve_oem_efi_strict": True,
                "prefer_new_esp_over_grow": True,
                "esp_cleanup_max_file_kb": 384,
                "guidance": [
                    "Acer: ESP often full of firmware capsules — cleanup fonts + large .cap/.bin only.",
                    "Do not delete PQSERVICE / Recovery / EFI\\Acer folders structure.",
                    "Device Encryption common on Win11 Home — suspend BitLocker protectors before SRP fix.",
                ],
            }
        )
    elif family == "asus":
        pol.update(
            {
                "preserve_oem_efi_strict": True,
                "esp_cleanup_max_file_kb": 384,
                "guidance": [
                    "Asus: keep EFI\\ASUS / MyASUS recovery markers; shrink C: + new ESP preferred.",
                    "After MBR2GPT set firmware to UEFI (CSM off) in Asus BIOS.",
                ],
            }
        )
    elif family == "dell":
        pol.update(
            {
                "preserve_oem_efi_strict": True,
                "esp_cleanup_max_file_kb": 256,
                "guidance": [
                    "Dell: EFI\\Dell / SupportAssist payloads often fill ESP — remove large capsules only.",
                    "Do not merge DELLSUPPORT recovery into C: automatically.",
                ],
            }
        )
    elif family == "hp":
        pol.update(
            {
                "preserve_oem_efi_strict": True,
                "esp_cleanup_max_file_kb": 256,
                "guidance": [
                    "HP: HP_TOOLS / HP_RECOVERY — never format; create new ESP instead of extending into them.",
                    "EFI\\HP firmware files: delete only bulky .bin/.img/.cap.",
                ],
            }
        )
    elif family == "lenovo":
        pol.update(
            {
                "preserve_oem_efi_strict": True,
                "esp_cleanup_max_file_kb": 384,
                "guidance": [
                    "Lenovo: leave LENOVO / OEM recovery partitions intact for mbr2gpt slot math (disable WinRE first).",
                    "ThinkPad Device Encryption: suspend BitLocker before diskpart.",
                ],
            }
        )
    elif family == "msi":
        pol.update(
            {
                "guidance": [
                    "MSI: mostly standard GPT/ESP; still suspend BitLocker before expand.",
                ],
            }
        )
    else:
        pol["guidance"] = [
            "Generic PC: suspend BitLocker if On; prefer cleanup then new 512MB ESP; preserve any Recovery partition.",
        ]
    return pol


def detect_oem_profile(*, probe_encryption: bool = True, probe_license: bool = True) -> OemProfile:
    """Full OEM profile for the current machine."""
    wmi = _wmi_csproduct()
    reg = _reg_oem()
    manufacturer = (
        wmi.get("Manufacturer")
        or wmi.get("Vendor")
        or reg.get("Manufacturer")
        or ""
    ).strip()
    model = (wmi.get("Model") or wmi.get("Name") or reg.get("Model") or "").strip()
    sku = (wmi.get("SystemSKU") or "").strip()
    bios = (wmi.get("BiosManufacturer") or "").strip()
    family = classify_oem_family(manufacturer, model, bios)
    pol = build_oem_policy(family)

    profile = OemProfile(
        family=family,
        manufacturer=manufacturer,
        model=model,
        sku=sku,
        bios_vendor=bios,
        preserve_oem_efi_strict=bool(pol["preserve_oem_efi_strict"]),
        preserve_recovery_partitions=bool(pol["preserve_recovery_partitions"]),
        prefer_new_esp_over_grow=bool(pol["prefer_new_esp_over_grow"]),
        mbr2gpt_disable_winre_first=bool(pol["mbr2gpt_disable_winre_first"]),
        esp_cleanup_max_file_kb=int(pol["esp_cleanup_max_file_kb"]),
        keep_efi_suffixes=list(pol["keep_efi_suffixes"]),
        guidance=list(pol["guidance"]),
    )
    profile.actions.append(f"oem_family:{family}")

    if probe_encryption:
        profile.bitlocker = _bitlocker_status()
        try:
            profile.device_encryption = _device_encryption_on()
        except Exception:
            profile.device_encryption = False
        # Device Encryption with protectors On is the same as BitLocker On — suspend only, never block
        if profile.device_encryption and profile.bitlocker == "off":
            # protectors already off — clear false positive
            profile.device_encryption = False
        if profile.device_encryption and profile.bitlocker in ("on", "unknown"):
            profile.encryption_notes.append("device_encryption_active")
            profile.guidance.append(
                "Device Encryption On — protectors will be suspended (non-destructive). BitLocker On is OK."
            )
        if family == "toshiba" or "toshiba" in manufacturer.lower() or "dynabook" in manufacturer.lower():
            hdd, sed, notes = _probe_toshiba_hdd_encryption()
            profile.sed_edrive_likely = sed
            profile.encryption_notes.extend(notes)
            sys_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            # Real ATA HDD password: Windows cannot see SystemRoot / C: (BitLocker On never does this)
            if not sys_root.exists() or not Path(os.environ.get("SystemDrive", "C:") + "\\").exists():
                profile.toshiba_hdd_password_likely = True
                profile.encryption_blocks_mutate = True
                profile.encryption_notes.append("system_volume_unreachable_possible_ata")
                profile.guidance.append(
                    "CRITICAL: system drive unreachable — unlock Toshiba HDD Password in BIOS, then reboot."
                )
            elif hdd:
                profile.toshiba_hdd_password_likely = True
                profile.guidance.append(
                    "If disk edits fail on Toshiba/Dynabook, check BIOS HDD Password (not BitLocker)."
                )
            elif any("toshiba_software" in n for n in notes):
                profile.guidance.append(
                    "Toshiba tools installed — BitLocker/Device Encryption is suspended normally; "
                    "HDD Password is separate (BIOS only if disk is invisible)."
                )
        # ONLY locked BitLocker hard-blocks (On = suspend and continue)
        if profile.bitlocker == "locked":
            profile.encryption_blocks_mutate = True
            profile.encryption_notes.append("bitlocker_volume_locked")
            profile.guidance.append(
                "BitLocker volume is LOCKED — unlock with recovery key first. "
                "If only Protection On (unlocked Windows), the tool suspends protectors and continues."
            )
        elif profile.bitlocker == "on":
            profile.encryption_notes.append("bitlocker_on_will_suspend")
            profile.guidance.append(
                "BitLocker Protection On: protectors will be suspended before partition/boot edits (safe)."
            )

    if probe_license:
        msdm, lic_notes = _msdm_oa3_present()
        profile.msdm_present = msdm
        profile.license_notes.extend(lic_notes)
        profile.digital_license_likely = msdm or ("windows_licensed" in lic_notes)
        try:
            profile.activation_status = _activation_partial()
        except Exception:
            profile.activation_status = "unknown"
        if profile.digital_license_likely or profile.msdm_present:
            profile.guidance.append(
                "OEM digital license (MSDM/OA3) present — Windows reactivation should follow hardware; do not wipe disk."
            )
            profile.actions.append("license_preserve_oem_digital")

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "oem-profile.json").write_text(
            json.dumps(profile.as_dict(), indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    log(
        f"OEM: {family} | {manufacturer} {model} | BL={profile.bitlocker} "
        f"DevEnc={profile.device_encryption} MSDM={profile.msdm_present}",
        "INFO",
    )
    return profile


_cached_profile: OemProfile | None = None


def get_oem_profile(*, refresh: bool = False) -> OemProfile:
    global _cached_profile
    if _cached_profile is None or refresh:
        disable = os.environ.get("MAGIC_OEM_ADAPT", "1").strip().lower()
        if disable in ("0", "false", "no"):
            _cached_profile = OemProfile(family="generic", actions=["oem_adapt_disabled"])
        else:
            _cached_profile = detect_oem_profile()
    return _cached_profile


def should_delete_oem_efi_file(path: Path, profile: OemProfile | None = None) -> bool:
    """
    Policy: delete bulky firmware dumps, keep .efi loaders and small markers.
    Strict OEMs (Acer/Asus/Toshiba/Dell/HP/Lenovo): never delete .efi under vendor folder.
    """
    profile = profile or get_oem_profile()
    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    # Never touch bootloaders
    if suffix == ".efi" or path.name.lower() in ("bcd", "bootmgr", "bootmgfw.efi"):
        return False
    if suffix in {s.lower() if s.startswith(".") else f".{s}" for s in profile.keep_efi_suffixes}:
        # Keep small config; still allow huge mistaken dumps
        max_kb = int(profile.esp_cleanup_max_file_kb)
        if profile.preserve_oem_efi_strict and path.stat().st_size <= max_kb * 1024 * 4:
            return False
    bulky = suffix in {".bin", ".img", ".cap", ".fd", ".rom", ".exe", ".zip", ".cab", ".wim", ".iso"}
    max_bytes = int(profile.esp_cleanup_max_file_kb) * 1024
    try:
        sz = path.stat().st_size
    except OSError:
        return False
    if bulky or sz > max_bytes:
        # Strict: still allow bulky capsule removal (they fill ESP) but not .efi
        return True
    return False


def is_oem_recovery_volume(label: str, fs: str = "", size_mb: float | None = None) -> bool:
    if OEM_RECOVERY_LABELS.search(label or ""):
        return True
    # Small NTFS without letter often recovery (1–20 GB)
    if size_mb is not None and 500 <= size_mb <= 25000 and (fs or "").upper() in ("NTFS", ""):
        if re.search(r"recover|oem|factory|restore|diag", label or "", re.I):
            return True
    return False


def apply_oem_to_partition_plan(plan: dict[str, Any], profile: OemProfile | None = None) -> dict[str, Any]:
    """Nudge smart partition strategy for OEM quirks (prefer create vs grow into recovery)."""
    profile = profile or get_oem_profile()
    plan = dict(plan)
    plan.setdefault("oem_family", profile.family)
    if profile.prefer_new_esp_over_grow and plan.get("strategy") == "extend_boot":
        # Growing ESP might collide with OEM recovery right after ESP on Acer/HP
        plan["strategy"] = "fallback_legacy"
        plan.setdefault("reasons", []).append("oem_prefer_new_esp_over_grow")
        plan["oem_override"] = "prefer_new_esp"
    if profile.encryption_blocks_mutate:
        plan["strategy"] = "blocked_encryption"
        plan["gparted"] = False
        plan.setdefault("reasons", []).append("oem_encryption_block")
    return plan


def prepare_encryption_for_mutate(profile: OemProfile | None = None) -> dict[str, Any]:
    """
    Suspend BitLocker / Device Encryption protectors when safe.

    Policy (do not break BitLocker-present PCs):
      - locked            -> hard block
      - on / device enc   -> manage-bde -protectors -disable, retry once, CONTINUE even if still on
      - off / unknown     -> no-op / soft suspend attempt if device_encryption hint
    Never treat "Protection On" as locked.
    """
    profile = profile or get_oem_profile()
    out: dict[str, Any] = {"ok": True, "actions": [], "blocked": False, "suspended": False}

    # Hard block ONLY for locked volume or unreachable system drive (ATA)
    if profile.bitlocker == "locked" or profile.encryption_blocks_mutate:
        out["ok"] = False
        out["blocked"] = True
        out["actions"].append("blocked_locked_only")
        log(
            "BitLocker LOCKED (or system drive unreachable) — unlock before disk/boot edits. "
            "BitLocker Protection On is NOT a lock.",
            "ERROR",
        )
        return out

    if profile.toshiba_hdd_password_likely:
        out["actions"].append("toshiba_hdd_password_soft_warn")
        log(
            "Toshiba HDD Password soft warning only — BitLocker On machines continue normally.",
            "WARN",
        )

    need_suspend = profile.bitlocker == "on" or profile.device_encryption
    if not need_suspend:
        out["actions"].append("bitlocker_no_suspend_needed")
        return out

    manage = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "manage-bde.exe"
    drive = os.environ.get("SystemDrive", "C:")
    if not manage.is_file():
        out["actions"].append("manage_bde_missing")
        # Still OK — do not block; diskpart may work with protectors on in some cases
        log("manage-bde missing — continuing (BitLocker On will not hard-block)", "WARN")
        return out

    log("Suspending BitLocker protectors (On != Locked; safe, non-destructive)...", "STEP")
    c1, o1 = _run([str(manage), "-protectors", "-disable", drive], timeout=90)
    out["actions"].append(f"protectors_disable:{c1}")
    # Already disabled / no protectors often returns non-zero — re-check status
    status = _bitlocker_status(drive)
    out["actions"].append(f"status_after_suspend:{status}")
    if status == "on":
        # Retry once
        c2, _ = _run([str(manage), "-protectors", "-disable", drive], timeout=90)
        out["actions"].append(f"protectors_disable_retry:{c2}")
        status = _bitlocker_status(drive)
        out["actions"].append(f"status_after_retry:{status}")

    if status == "locked":
        out["ok"] = False
        out["blocked"] = True
        out["actions"].append("became_locked")
        log("Volume became LOCKED during suspend attempt — stop", "ERROR")
        return out

    if status == "on":
        # Do NOT block — warn only. Shrink/bcdboot often still works; user can unlock later.
        out["actions"].append("suspend_incomplete_continue")
        log(
            "BitLocker still Protection On after suspend — continuing anyway (not locked). "
            "If diskpart fails, run: manage-bde -protectors -disable C:",
            "WARN",
        )
        out["ok"] = True
        out["blocked"] = False
    else:
        out["suspended"] = True
        out["actions"].append("protectors_suspended_ok")
        log(f"BitLocker protectors suspended (status={status}) — safe to mutate", "OK")

    # Refresh profile bitlocker field for callers
    profile.bitlocker = status
    return out


def oem_guidance_text(profile: OemProfile | None = None) -> str:
    profile = profile or get_oem_profile()
    lines = [
        f"OEM family: {profile.family}",
        f"Manufacturer: {profile.manufacturer}",
        f"Model: {profile.model}",
        f"BitLocker: {profile.bitlocker} | DeviceEncryption: {profile.device_encryption}",
        f"Toshiba HDD password likely: {profile.toshiba_hdd_password_likely}",
        f"SED/eDrive likely: {profile.sed_edrive_likely}",
        f"MSDM/OA3 OEM key: {profile.msdm_present} | Activation: {profile.activation_status}",
        "",
        "Guidance:",
    ]
    for g in profile.guidance:
        lines.append(f"  - {g}")
    return "\n".join(lines)


def write_oem_guidance_file(profile: OemProfile | None = None) -> Path:
    profile = profile or get_oem_profile()
    rescue = STATE_DIR / "rescue"
    rescue.mkdir(parents=True, exist_ok=True)
    path = rescue / "OEM-Boot-License-Guide.txt"
    path.write_text(oem_guidance_text(profile), encoding="utf-8")
    try:
        desk = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Win11MagicUpgrade-OEM-Guide.txt"
        if desk.parent.exists():
            import shutil

            shutil.copy2(path, desk)
    except Exception:
        pass
    return path
