"""Intelligent auto-diagnosis -> action plan for max compatibility."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .detect import Report, collect_report
from .chain import build_version_chain, format_chain
from .logutil import log


@dataclass
class Action:
    id: str
    title: str
    reason: str
    risk: str = "low"  # low | medium | high
    required: bool = True


@dataclass
class Plan:
    target: str  # win11_latest | win10_22h2 | already_done | blocked
    summary: str
    can_win11: bool
    actions: list[Action] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def build_plan(report: Report | None = None) -> Plan:
    r = report or collect_report()
    actions: list[Action] = []
    blockers: list[str] = []
    warnings: list[str] = []

    # Always useful prep
    actions.append(
        Action(
            "fix_srp",
            "Fix System Reserved / EFI partition",
            "Free ESP/SRP space (fonts/OEM) or enlarge via new 512 MB boot partition + bcdboot",
            "medium",
        )
    )
    actions.append(
        Action(
            "patches",
            "Apply migration safety patches",
            "Clear stale upgrade leftovers, stop risky filters, free space, clear appraiser",
            "low",
        )
    )
    actions.append(
        Action(
            "registry_bypass",
            "Apply intelligent Win11 registry bypass pack",
            "LabConfig/MoSetup/HwReqChk/PCHC/UpgradeEligibility - safe DWORD/MULTI_SZ only",
            "low",
        )
    )

    # Already latest-class Win11
    if r.is_win11 and r.build >= 26100:
        return Plan(
            target="already_done",
            summary="Already on Windows 11 24H2/25H2-class. Optional: re-apply bypasses for future feature updates.",
            can_win11=True,
            actions=[a for a in actions if a.id == "registry_bypass"],
            report=r.as_dict(),
        )

    # 32-bit: Win11 impossible; max = Win10 22H2 x86 inplace
    if r.architecture != "x64":
        if getattr(r, "firmware_likely_ia32", False):
            warnings.append(
                "IA32 UEFI detected: hybrid CSMWrap (UEFI32->SeaBIOS->BIOS) will be staged for Win11 x64."
            )
            actions.insert(
                0,
                Action(
                    "hybrid_ia32",
                    "Deploy hybrid IA32 UEFI bridge (CSMWrap)",
                    "Download csmwrapia32.efi, stage on ESP; disable Secure Boot before Win11 x64",
                    "high",
                ),
            )
            blockers.append(
                "No inplace Win11 from 32-bit Windows. Keep-apps max: Win10 22H2 x86. "
                "Win11 x64 needs clean install booting via hybrid CSMWrap."
            )
        elif getattr(r, "cpu_64bit", False):
            blockers.append("32-bit Windows cannot inplace-upgrade to 64-bit Win11.")
            warnings.append("CPU is 64-bit: only a clean install of Win11 x64 can reach Windows 11.")
        else:
            blockers.append("Windows 11 does not exist as 32-bit. In-place Win11 upgrade is impossible.")
        warnings.append("Best safe keep-apps path: Windows 10 22H2 x86.")
        if r.is_win10 and r.build < 19045:
            actions.append(
                Action(
                    "win10_22h2_x86",
                    "In-place upgrade to Windows 10 22H2 (32-bit ISO)",
                    "Maximum keep-apps destination on x86",
                    "medium",
                )
            )
        return Plan(
            target="win10_22h2",
            summary="32-bit Windows / IA32 UEFI - hybrid bridge for future Win11 x64; keep-apps max Win10 22H2 x86.",
            can_win11=False,
            actions=actions,
            blockers=blockers,
            warnings=warnings,
            report=r.as_dict(),
        )

    # Legacy Vista / 7 / 8 / 8.1 (incl. Media Center)
    if getattr(r, "is_legacy", False):
        from .legacy_os import detect_exotic_edition, inplace_notes_for_legacy, legacy_label

        fam = getattr(r, "os_family", "unknown")
        warnings.append(
            f"Legacy host detected: {legacy_label(fam, r.build)} — chain via Win10 22H2 → Win11."
        )
        for note in inplace_notes_for_legacy(r):
            warnings.append(note)
        for w in detect_exotic_edition(r.edition_id, r.product_name):
            warnings.append(w)
        if fam == "vista":
            warnings.append(
                "VISTA: no Microsoft-supported inplace path. Backup first. "
                "Set MAGIC_ALLOW_VISTA=1 to acknowledge best-effort attempt."
            )
            actions.append(
                Action(
                    "vista_backup_gate",
                    "Acknowledge Vista best-effort upgrade",
                    "Backup required; MAGIC_ALLOW_VISTA=1 recommended",
                    "high",
                )
            )
        actions.append(
            Action(
                "legacy_registry",
                "Legacy upgrade registry prep",
                "AllowOSUpgrade, Setup Compact (8.x), Media Center flags",
                "low",
            )
        )
        if getattr(r, "has_media_center", False):
            actions.append(
                Action(
                    "legacy_media_center",
                    "Media Center edition bypass on Setup media",
                    "sources\\ei.cfg + pid.txt → Professional; setupprep.exe",
                    "medium",
                )
            )
        if r.needs_intermediate:
            actions.append(
                Action(
                    "legacy_win10_22h2",
                    f"In-place upgrade: {legacy_label(fam, r.build)} → Windows 10 22H2",
                    "Required stepping stone; keeps files/apps when Setup allows",
                    "medium",
                )
            )

    if getattr(r, "bootmgr_mismatch", False):
        actions.insert(
            0,
            Action(
                "fix_bootmgr",
                "Repair Boot Manager to x64",
                "ESP has 32-bit/stale boot files while OS is x64 - bcdboot rewrite",
                "medium",
            ),
        )
        warnings.append("Boot Manager bitness mismatch detected - will realign before Win11.")

    # No SSE4.2/POPCNT: Win11 24H2+ won't boot
    if r.sse42 is False:
        blockers.append("CPU lacks SSE4.2/POPCNT - Windows 11 24H2+ cannot boot.")
        warnings.append("Will maximize on Windows 10 22H2 x64 instead of forcing a non-bootable Win11.")
        if r.is_win10 and (r.build < 19045 or r.needs_intermediate):
            actions.append(
                Action(
                    "win10_22h2_x64",
                    "In-place upgrade to Windows 10 22H2",
                    "Safest maximum OS for this CPU without boot failure",
                    "medium",
                )
            )
        elif getattr(r, "is_legacy", False):
            actions.append(
                Action(
                    "win10_22h2_x64",
                    "Legacy → Windows 10 22H2",
                    "Maximum keep-apps OS for this CPU (no Win11 24H2+ boot)",
                    "medium",
                )
            )
        return Plan(
            target="win10_22h2",
            summary="CPU incompatible with Win11 24H2+ - targeting Windows 10 22H2 (keep apps/files).",
            can_win11=False,
            actions=actions,
            blockers=blockers,
            warnings=warnings,
            report=r.as_dict(),
        )

    # Low RAM soft warning (still bypassable)
    if r.ram_gb and r.ram_gb < 4:
        warnings.append(f"RAM {r.ram_gb} GB < 4 GB - LabConfig BypassRAMCheck will be applied; system may be unstable.")
    if r.free_gb and r.free_gb < 20:
        warnings.append(f"Only {r.free_gb} GB free - cleanup will run; need ~20 GB for setup.")
        actions.append(
            Action("space", "Free disk space", "Hibernation off + temp cleanup", "low")
        )

    # MBR -> GPT + bootmgr
    if r.partition_style == "MBR":
        warnings.append("System disk is MBR - Windows 11 needs GPT/UEFI. Will convert without wipe when possible.")
        if r.mbr2gpt_available:
            actions.append(
                Action(
                    "mbr2gpt",
                    "Convert MBR->GPT without data loss",
                    "mbr2gpt /allowFullOS + layout repair (shrink/WinRE) if validate fails",
                    "medium",
                )
            )
            actions.append(
                Action(
                    "bootmgr",
                    "Repair Windows Boot Manager for UEFI",
                    "bcdboot /f UEFI (and /f ALL fallback) after conversion - no format",
                    "medium",
                )
            )
        else:
            actions.append(
                Action(
                    "intermediate_then_mbr",
                    "Upgrade to Win10 22H2 first, then MBR->GPT",
                    "mbr2gpt requires Win10 1703+; obsolete build detected",
                    "medium",
                )
            )
        warnings.append("After GPT conversion: set firmware boot mode to UEFI (disable CSM/Legacy).")

    # Obsolete / not-yet-22H2 Win10: always step via 22H2
    if r.needs_intermediate:
        actions.append(
            Action(
                "intermediate_win10",
                "Intermediate Windows 10 22H2 upgrade",
                f"Build {r.build} must pass by Win10 22H2 before Windows 11",
                "medium",
            )
        )

    # Final Win11
    actions.append(
        Action(
            "win11_latest",
            "In-place upgrade to Windows 11 latest",
            "Official ISO + setup /product server - keep files and apps; unsupported HW allowed via bypasses",
            "medium",
        )
    )

    if not r.tpm_present:
        warnings.append("No TPM detected - covered by BypassTPMCheck + /product server.")
    if not r.secure_boot:
        warnings.append("Secure Boot off/unavailable - covered by BypassSecureBootCheck.")

    chain = build_version_chain(r)
    chain_path = format_chain(chain)
    warnings.append(f"Version chain: {chain_path}")

    return Plan(
        target="win11_latest",
        summary=f"Stepped path: {chain_path}",
        can_win11=True,
        actions=actions,
        blockers=blockers,
        warnings=warnings,
        report=r.as_dict(),
    )


def print_plan(plan: Plan) -> None:
    log("=== Intelligent auto-diagnosis ===", "STEP")
    log(f"Target: {plan.target}", "OK" if plan.can_win11 or plan.target == "already_done" else "WARN")
    log(plan.summary)
    for b in plan.blockers:
        log(f"BLOCK: {b}", "ERROR")
    for w in plan.warnings:
        log(f"WARN: {w}", "WARN")
    log("Planned actions:", "STEP")
    for i, a in enumerate(plan.actions, 1):
        req = "required" if a.required else "optional"
        log(f"  {i}. [{a.risk}/{req}] {a.title} - {a.reason}")
