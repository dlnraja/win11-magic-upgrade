"""Intelligent auto-diagnosis -> action plan for max compatibility."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .detect import Report, collect_report
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
        blockers.append("Windows 11 does not exist as 32-bit. In-place Win11 upgrade is impossible.")
        warnings.append("Best safe path: upgrade this 32-bit OS to Windows 10 22H2 x86 (keep files/apps).")
        if r.is_win10 and r.build < 19045:
            actions.append(
                Action(
                    "win10_22h2_x86",
                    "In-place upgrade to Windows 10 22H2 (32-bit ISO)",
                    "Maximum supported destination on x86 without wiping data",
                    "medium",
                )
            )
        return Plan(
            target="win10_22h2",
            summary="32-bit Windows detected - targeting Windows 10 22H2 x86 (max without wipe). Win11 requires clean x64 install on 64-bit capable CPU.",
            can_win11=False,
            actions=actions,
            blockers=blockers,
            warnings=warnings,
            report=r.as_dict(),
        )

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

    # Obsolete Win10
    if r.needs_intermediate:
        actions.append(
            Action(
                "intermediate_win10",
                "Intermediate Windows 10 22H2 upgrade",
                f"Build {r.build} is too old for reliable direct Win11 setup",
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

    return Plan(
        target="win11_latest",
        summary="Path: prep -> registry bypass -> MBR/boot if needed -> (Win10 22H2 if obsolete) -> Win11 latest inplace.",
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
