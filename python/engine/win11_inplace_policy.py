"""One-Click policy when the PC is already Windows 11.

The first attempt is only an official in-place feature update (Windows Update
or setup.exe /auto upgrade). Bypass, MBR to GPT, ESP/SRP changes, and Boot Fix
stay off. A failure may add one matching remediation and one retry. Windows 10
and older keep the full migration path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WIN11_MIN_BUILD = 22000
DEVICE_BRANCH_BUILD = 28000
MAX_INPLACE_ATTEMPTS = 2

_COMPAT = ("0XC1900200", "0XC1900202", "0XC1900204", "0XC1900208", "0XC190020E")
_DRIVER = "0XC1900101"
_BOOT = re.compile(r"bootmgr|\\bcd\b|\besp\b|efi system|0x20017|boot configuration", re.I)
_ROLLBACK = re.compile(r"\$WINDOWS\.~BT\\Sources\\Rollback|rollback", re.I)


_EFI_ACTIONS = ("esp_repair", "bcdboot_uefi", "bcd_fix")
_BIOS_ACTIONS = ("srp_active", "bcdboot_bios", "bootsect_nt60")


@dataclass(frozen=True)
class FailureAssessment:
    cause: str
    architecture: str
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class OneClickPlan:
    path: str  # full_migration | health_check_only | cumulative_update | inplace_least
    allow_bypass: bool = False
    allow_mbr2gpt: bool = False
    allow_esp: bool = False
    allow_boot_fix: bool = False
    reason: str = ""


def is_windows_11(build: int) -> bool:
    return int(build or 0) >= WIN11_MIN_BUILD


def _version_key(version: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{2})H([12])", (version or "").upper())
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def _device_branch(display_version: str, build: int) -> bool:
    return (display_version or "").upper() == "26H1" or int(build or 0) >= DEVICE_BRANCH_BUILD


def decide_oneclick(
    *,
    build: int,
    ubr: int,
    display_version: str,
    target_version: str,
    target_build: int,
    target_ubr: int,
) -> OneClickPlan:
    if not is_windows_11(build):
        return OneClickPlan(
            path="full_migration",
            allow_bypass=True,
            allow_mbr2gpt=True,
            allow_esp=True,
            allow_boot_fix=True,
            reason="Windows 10 or older keeps the full migration path.",
        )
    host_key = _version_key(display_version)
    target_key = _version_key(target_version)
    if _device_branch(display_version, build) and (display_version or "").upper() != (target_version or "").upper():
        return OneClickPlan(
            path="health_check_only",
            reason="This PC is on a device-only Windows 11 branch. No in-place move to the consumer release.",
        )
    if host_key > target_key or (
        host_key == target_key and host_key != (0, 0) and int(build) > int(target_build)
    ):
        return OneClickPlan(path="health_check_only", reason="Already newer than the release pin.")
    if int(build) == int(target_build):
        if int(ubr or 0) <= 0 or int(ubr) >= int(target_ubr):
            return OneClickPlan(
                path="health_check_only",
                reason="Already on the latest Windows 11 build from the release pin.",
            )
        return OneClickPlan(
            path="cumulative_update",
            reason="Same Windows 11 feature build; only the cumulative update is behind.",
        )
    if host_key and target_key and host_key < target_key or int(build) < int(target_build):
        return OneClickPlan(
            path="inplace_least",
            reason="Windows 11 feature update via official setup /auto upgrade. No bypass, disk, or boot changes.",
        )
    return OneClickPlan(path="health_check_only", reason="No newer in-place Windows 11 target.")


def official_setup_args(*, quiet: bool = True, bypass: bool = False) -> list[str]:
    args = ["/auto", "upgrade", "/eula", "accept", "/dynamicupdate", "enable"]
    if quiet:
        args += ["/quiet", "/showoobe", "none"]
    if bypass:
        args += ["/Compat", "IgnoreWarning"]
    return args


def classify_setup_failure(exit_code: int, text: str) -> str:
    return assess_failure(exit_code, text).cause


def detect_boot_architecture(text: str, firmware: str = "") -> str:
    """uefi, bios, or hybrid. Unknown firmware is not guessed from silence."""
    raw = text or ""
    fw = (firmware or "").strip().upper()
    uefi = fw in ("UEFI", "EFI") or bool(
        re.search(r"\bUEFI\b|\\EFI\\|bootmgfw|winload\.efi|bcdboot\s+/f\s+UEFI", raw, re.I)
    )
    bios = fw in ("BIOS", "LEGACY", "MBR") or bool(
        re.search(
            r"\bBIOS\b|winload\.exe|System Reserved|bootsect|bcdboot\s+/f\s+BIOS",
            raw,
            re.I,
        )
    )
    if uefi and bios:
        return "hybrid"
    if uefi:
        return "uefi"
    if bios:
        return "bios"
    return "unknown"


def actions_for_failure(cause: str, architecture: str) -> tuple[str, ...]:
    """Only the actions that match the cause and the boot architecture."""
    if cause == "compat":
        return ("requirements_bypass",)
    if cause == "driver":
        return ("driver_cleanup",)
    if cause != "boot":
        return ()
    if architecture == "uefi":
        return _EFI_ACTIONS
    if architecture == "bios":
        return _BIOS_ACTIONS
    if architecture == "hybrid":
        return _EFI_ACTIONS + _BIOS_ACTIONS
    return ()


def assess_failure(exit_code: int, text: str, *, firmware: str = "") -> FailureAssessment:
    blob = f"{exit_code}\n{text or ''}".upper()
    raw = text or ""
    if _BOOT.search(raw) or "0X20017" in blob:
        cause = "boot"
    elif _DRIVER in blob:
        cause = "driver"
    elif any(code in blob for code in _COMPAT):
        cause = "compat"
    else:
        cause = "unknown"
    architecture = detect_boot_architecture(raw, firmware)
    return FailureAssessment(
        cause=cause,
        architecture=architecture,
        actions=actions_for_failure(cause, architecture),
    )


def next_remediation(cause: str, *, used: set[str], attempt: int, architecture: str = "") -> tuple[str, ...] | None:
    """One matching action list, then stop. Unknown failures do not escalate."""
    if attempt >= MAX_INPLACE_ATTEMPTS or cause in used or cause == "unknown":
        return None
    actions = actions_for_failure(cause, architecture)
    if not actions:
        return None
    return actions


def run_inplace_attempts(
    *,
    launch,
    read_text,
    remediate,
    quiet: bool = True,
    firmware: str = "",
) -> int:
    """Official upgrade first. No bypass or boot action until a logged failure."""
    used: set[str] = set()
    bypass = False
    last = 2
    for attempt in range(1, MAX_INPLACE_ATTEMPTS + 1):
        last = int(launch(official_setup_args(quiet=quiet, bypass=bypass)))
        if last in (0, 3010, 3011):
            return 0
        assessment = assess_failure(last, read_text() or "", firmware=firmware)
        actions = next_remediation(
            assessment.cause,
            used=used,
            attempt=attempt,
            architecture=assessment.architecture,
        )
        if not actions:
            return last if last else 2
        used.add(assessment.cause)
        remediate(
            FailureAssessment(
                cause=assessment.cause,
                architecture=assessment.architecture,
                actions=actions,
            )
        )
        bypass = "requirements_bypass" in actions
    return last if last else 2
