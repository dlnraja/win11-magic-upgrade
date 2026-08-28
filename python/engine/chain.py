"""Ordered intermediate upgrade chain (version stepping) across reboots."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .detect import Report


@dataclass
class ChainStep:
    id: str
    label: str
    kind: str  # iso_upgrade | mbr2gpt | bypass | done
    win: str | None = None  # "10" | "11"
    arch: str | None = None  # x64 | x86
    use_server_product: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Windows 10 22H2 = build 19045. Prefer stepping through it before Win11.
WIN10_22H2_BUILD = 19045
# Win11 24H2 baseline (dynamic latest tracked in state.json LatestWin11Build)
WIN11_24H2_BUILD = 26100
WIN11_LATEST_DEFAULT = 26200  # 25H2-class retail when CDN serves it


def mapped_version(build: int) -> str:
    table = {
        10240: "1507",
        10586: "1511",
        14393: "1607",
        15063: "1703",
        16299: "1709",
        17134: "1803",
        17763: "1809",
        18362: "1903",
        18363: "1909",
        19041: "2004",
        19042: "20H2",
        19043: "21H1",
        19044: "21H2",
        19045: "22H2",
        22000: "21H2",
        22621: "22H2",
        22631: "23H2",
        26100: "24H2",
        26200: "25H2",
    }
    if build in table:
        return table[build]
    # nearest lower
    keys = sorted(table)
    ver = str(build)
    for k in keys:
        if build >= k:
            ver = table[k]
    return ver


def build_version_chain(r: Report) -> list[ChainStep]:
    """
    Compute mandatory intermediate steps.

    Official Microsoft CDN only still serves Win10 22H2 + Win11 latest ISOs,
    so obsolete Win10 (1511, 1607, 1809, ...) always steps through 22H2 first,
    then Win11. That is the supported reliable intermediate path today.
    """
    steps: list[ChainStep] = []
    cur = mapped_version(r.build)

    # Already latest-class Win11 (build from state / default 25H2 baseline)
    from .version_planner import latest_win11_build_from_state

    latest_target = latest_win11_build_from_state()
    if r.is_win11 and r.build >= latest_target:
        return [
            ChainStep(
                id="done",
                label="Already on Windows 11 24H2+",
                kind="done",
                note=f"Current {cur} build {r.build}",
            )
        ]

    # 32-bit OS: Win11 inplace impossible. Smart max path by CPU/firmware.
    if r.architecture != "x64":
        steps.append(
            ChainStep(
                id="fix_srp",
                label="Fix System Reserved / EFI partition (free space or enlarge)",
                kind="fix_srp",
                note="Prevents reserved-partition upgrade failures",
            )
        )
        if getattr(r, "firmware_likely_ia32", False) or getattr(r, "boot_strategy", "") == "hybrid_ia32_csmwrap":
            note_max = (
                "IA32 UEFI + hybrid CSMWrap: keep-apps max = Win10 22H2 x86; "
                "Win11 x64 via clean install after CSMWrap (Secure Boot off)."
            )
            steps.append(
                ChainStep(
                    id="hybrid_ia32",
                    label="Deploy hybrid IA32 UEFI bridge (CSMWrap -> SeaBIOS)",
                    kind="hybrid_ia32",
                    note="Enables later Win11 x64 boot on 32-bit UEFI firmware",
                ),
            )
        elif getattr(r, "cpu_64bit", False):
            note_max = (
                "32-bit Windows cannot inplace to Win11 x64. "
                "CPU is 64-bit: after backup, clean-install Win11 x64 is the only path."
            )
        else:
            note_max = "Cannot upgrade 32-bit OS to Windows 11 inplace"
        if r.build < WIN10_22H2_BUILD:
            steps.append(
                ChainStep(
                    id="win10_22h2",
                    label=f"Intermediate: {cur} -> Windows 10 22H2 (x86)",
                    kind="iso_upgrade",
                    win="10",
                    arch="x86",
                    note=note_max,
                )
            )
        steps.append(
            ChainStep(
                id="done",
                label="Max reached: Windows 10 22H2 x86 (no inplace Win11)",
                kind="done",
                note=note_max,
            )
        )
        return steps

    # IA32 UEFI + x64 OS: hybrid CSMWrap then continue toward Win11 (BIOS handoff)
    if getattr(r, "firmware_likely_ia32", False) or getattr(r, "boot_strategy", "") == "hybrid_ia32_csmwrap":
        steps.append(
            ChainStep(
                id="hybrid_ia32",
                label="Deploy hybrid IA32 UEFI bridge (CSMWrap -> SeaBIOS)",
                kind="hybrid_ia32",
                note="Activate CSMWrap as bootia32; disable Secure Boot; BIOS bootmgr for Win11 x64",
            )
        )

    # No SSE4.2: cannot boot Win11 24H2+
    if r.sse42 is False:
        steps.append(
            ChainStep(
                id="fix_srp",
                label="Fix System Reserved / EFI partition (free space or enlarge)",
                kind="fix_srp",
                note="Prevents reserved-partition upgrade failures",
            )
        )
        if r.is_win10 and r.build < WIN10_22H2_BUILD:
            steps.append(
                ChainStep(
                    id="win10_22h2",
                    label=f"Intermediate: {cur} -> Windows 10 22H2",
                    kind="iso_upgrade",
                    win="10",
                    arch="x64",
                    note="CPU cannot run Win11 24H2+; stop at Win10 22H2",
                )
            )
        steps.append(
            ChainStep(
                id="done",
                label="Max reached for this CPU (no Win11 24H2+)",
                kind="done",
                note="SSE4.2/POPCNT missing",
            )
        )
        return steps

    # --- Path toward Windows 11 ---

    # Always fix System Reserved / EFI before feature upgrades (common setup blocker)
    steps.append(
        ChainStep(
            id="fix_srp",
            label="Fix System Reserved / EFI partition (free space or enlarge)",
            kind="fix_srp",
            note="Prevents 'We could not update the system reserved partition' / FR equivalent",
        )
    )

    # x64 OS with 32-bit / missing bootx64 Boot Manager -> rewrite ESP via bcdboot
    if getattr(r, "bootmgr_mismatch", False) or getattr(r, "boot_strategy", "") == "repair_bootmgr_x64":
        steps.append(
            ChainStep(
                id="fix_bootmgr",
                label="Align Boot Manager to x64 (bcdboot UEFI)",
                kind="fix_bootmgr",
                note="Fixes stale bootia32 / wrong bitness so Win11 x64 can reboot",
            )
        )

    # Step A: any Win10 below 22H2 must pass by 22H2 first (1511, 1607, 1809, 21H2, ...)
    if r.is_win10 and r.build < WIN10_22H2_BUILD:
        steps.append(
            ChainStep(
                id="win10_22h2",
                label=f"Intermediate 1: Windows {cur} -> Windows 10 22H2",
                kind="iso_upgrade",
                win="10",
                arch="x64",
                note="Required stepping stone before Windows 11 (keeps files/apps)",
            )
        )

    # Step B: MBR -> GPT after we have a modern enough OS (1703+ / after 22H2 step)
    # If currently too old for mbr2gpt, the 22H2 intermediate runs first; MBR is done on resume.
    if r.partition_style == "MBR":
        if r.mbr2gpt_available or (r.is_win10 and r.build < WIN10_22H2_BUILD):
            # Schedule MBR after 22H2 if not yet available
            steps.append(
                ChainStep(
                    id="mbr2gpt",
                    label="Intermediate: MBR -> GPT + Boot Manager (no wipe)",
                    kind="mbr2gpt",
                    note="Required for Windows 11 UEFI boot; runs after Win10 22H2 if build was <1703",
                )
            )

    # Step C: Windows 11 latest (from Win10 22H2, older Win10, or older Win11 builds)
    if r.is_win10 or (r.is_win11 and r.build < latest_target):
        from_l = mapped_version(r.build)
        from_label = (
            "Windows 10 22H2"
            if (r.is_win10 and r.build < WIN10_22H2_BUILD)
            else f"Windows {from_l} (build {r.build})"
        )
        idx = len([s for s in steps if s.kind == "iso_upgrade"]) + 1
        steps.append(
            ChainStep(
                id="win11_latest",
                label=f"Intermediate {idx}: {from_label} -> Windows 11 latest ({mapped_version(latest_target)}+)",
                kind="iso_upgrade",
                win="11",
                arch="x64",
                use_server_product=True,
                note="In-place upgrade; /product server + registry bypass; keeps files/apps",
            )
        )

    if not steps:
        steps.append(
            ChainStep(id="done", label="Nothing to do", kind="done", note=f"build {r.build}")
        )
    return steps


def format_chain(steps: list[ChainStep]) -> str:
    parts = [s.label for s in steps if s.kind != "done"]
    if not parts:
        return steps[0].label if steps else "n/a"
    return "  =>  ".join(parts)
