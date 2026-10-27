"""Ordered intermediate upgrade chain (version stepping) across reboots."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .detect import Report


@dataclass
class ChainStep:
    id: str
    label: str
    kind: str  # iso_upgrade | mbr2gpt | bypass | media_offers | done
    win: str | None = None  # "10" | "11" | "81"
    arch: str | None = None  # x64 | x86
    use_server_product: bool = False
    note: str = ""
    iso_path: str | None = None  # optional local ISO (e.g. Win8.1)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _media_center_chain_eligible(r: Report) -> bool:
    if getattr(r, "has_media_center", False):
        return True
    try:
        from .legacy_os import media_center_eligible_from_state

        return media_center_eligible_from_state()
    except Exception:
        return False


# Windows 10 22H2 = build 19045. Prefer stepping through it before Win11.
WIN10_22H2_BUILD = 19045
# Win11 24H2 baseline (dynamic latest tracked in state.json LatestWin11Build)
WIN11_24H2_BUILD = 26100
# Floor only. Live detection is win11_release (26H2 = 26300; 26H1/28000 is not in-place).
WIN11_LATEST_DEFAULT = 26300


def mapped_version(build: int) -> str:
    table = {
        6000: "Vista",
        6001: "Vista SP1",
        6002: "Vista SP2",
        7600: "7",
        7601: "7 SP1",
        9200: "8",
        9600: "8.1",
        9601: "8.1",
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
        26300: "26H2",
        28000: "26H1",
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

    # Already on the detected feature update (state / floor). A higher build
    # (device-scoped 26H1) is not pulled backwards. Same build + older UBR
    # only needs the cumulative nudge.
    from .version_planner import latest_win11_build_from_state, needs_cumulative_update

    latest_target = latest_win11_build_from_state()

    # ARM64: honest stop — no x64 ISO inplace chain
    if getattr(r, "is_arm64", False) or getattr(r, "architecture", "") == "arm64":
        return [
            ChainStep(
                id="done",
                label="ARM64 host — use Microsoft ARM64 media manually",
                kind="done",
                note="This tool’s CDN/ISO chain targets x86/x64. ARM64 keep-apps needs vendor ARM ISOs.",
            )
        ]

    if r.is_win11 and r.build > latest_target:
        return [
            ChainStep(
                id="done",
                label=f"Already on Windows 11 {mapped_version(r.build)}",
                kind="done",
                note=f"Current {cur} build {r.build} is newer than target {latest_target}",
            )
        ]
    if r.is_win11 and r.build == latest_target:
        if needs_cumulative_update(r, latest_target):
            return [
                ChainStep(
                    id="win11_cumulative",
                    label=f"Latest cumulative for Windows 11 {mapped_version(latest_target)}",
                    kind="cumulative",
                    note="Feature update is installed; nudge Windows Update for the current cumulative",
                )
            ]
        return [
            ChainStep(
                id="done",
                label=f"Already on Windows 11 {mapped_version(latest_target)}",
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
        elif getattr(r, "is_legacy", False):
            steps.append(
                ChainStep(
                    id="win10_22h2",
                    label=f"Intermediate: {cur} -> Windows 10 22H2 (x86)",
                    kind="iso_upgrade",
                    win="10",
                    arch="x86",
                    note="Legacy x86 host — max keep-apps path via Win10 22H2",
                )
            )
        _maybe_openduet(steps, r)
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
        elif getattr(r, "is_legacy", False):
            steps.append(
                ChainStep(
                    id="win10_22h2",
                    label=f"Intermediate: {cur} -> Windows 10 22H2",
                    kind="iso_upgrade",
                    win="10",
                    arch="x64",
                    note="Legacy host — stop at Win10 22H2 (CPU lacks SSE4.2/POPCNT)",
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

    # Optional Win8 → Win8.1 when a local 8.1 ISO is provided (CDN does not serve it)
    os_fam = getattr(r, "os_family", "") or ""
    if os_fam == "win8" and r.build < 9600:
        try:
            from .legacy_os import find_optional_win81_iso

            w81 = find_optional_win81_iso()
        except Exception:
            w81 = None
        if w81 is not None:
            steps.append(
                ChainStep(
                    id="win81",
                    label=f"Intermediate: Windows 8 -> Windows 8.1 (local ISO)",
                    kind="iso_upgrade",
                    win="81",
                    arch=r.architecture if r.architecture in ("x64", "x86") else "x64",
                    iso_path=str(w81),
                    note=(
                        "Optional hop via MAGIC_WIN81_ISO / local 8.1 media; "
                        "keeps Media Center edition for the later Win10 offer path"
                    ),
                )
            )

    # Step A: Win10 below 22H2 OR legacy Vista/7/8/8.1 must pass by 22H2 first
    needs_win10_hop = (r.is_win10 and r.build < WIN10_22H2_BUILD) or getattr(r, "is_legacy", False)
    if needs_win10_hop:
        legacy_note = (
            "Legacy Windows → Win10 22H2 (registry + Media Center media fixes when needed)"
            if getattr(r, "is_legacy", False)
            else "Required stepping stone before Windows 11 (keeps files/apps)"
        )
        if _media_center_chain_eligible(r):
            legacy_note += (
                "; Media Center Key Edition → Pro via ei.cfg/pid.txt "
                "(inplace keep-apps; DVD Player eligibility persisted)"
            )
        idx = len([s for s in steps if s.kind == "iso_upgrade"]) + 1
        steps.append(
            ChainStep(
                id="win10_22h2",
                label=f"Intermediate {idx}: Windows {cur} -> Windows 10 22H2",
                kind="iso_upgrade",
                win="10",
                arch="x64",
                note=legacy_note,
            )
        )

    # After Win10: claim Media Center / DVD Player carry-forward before Win11
    # Eligibility is persisted in state.json before the Win10 hop (EditionID becomes Pro).
    if _media_center_chain_eligible(r):
        try:
            from .legacy_os import media_offers_already_claimed

            already = media_offers_already_claimed()
        except Exception:
            already = False
        if not already:
            steps.append(
                ChainStep(
                    id="media_offers",
                    label="Claim Media Center offers (Windows DVD Player + carry-forward)",
                    kind="media_offers",
                    note=(
                        "Runs after Win10 hop: nudge Windows Update / detect "
                        "Microsoft.WindowsDVDPlayer; then continue to latest Win11"
                    ),
                )
            )

    # Settle: WU + 2nd media-offers pass after Win10 hop (before Win11)
    if needs_win10_hop or _media_center_chain_eligible(r):
        try:
            from .logutil import load_state

            settle_done = bool(load_state().get("SettleDone"))
        except Exception:
            settle_done = False
        if not settle_done:
            steps.append(
                ChainStep(
                    id="settle",
                    label="Settle after Win10 (WU + Media Center offers 2nd pass)",
                    kind="settle",
                    note="UsoClient nudge; re-claim DVD Player; optional MAGIC_INSTALL_VLC=1",
                )
            )

    # Step B: MBR -> GPT only when the firmware matrix says the PC can boot UEFI afterwards.
    # Legacy-only BIOS keeps MBR. IA32 UEFI keeps the hybrid/legacy handoff.
    if str(getattr(r, "partition_style", "")).upper() == "MBR" and not getattr(r, "partition_hybrid", False):
        if _mbr2gpt_wanted(r) and (r.mbr2gpt_available or needs_win10_hop):
            steps.append(
                ChainStep(
                    id="mbr2gpt",
                    label="Intermediate: MBR -> GPT + Boot Manager (no wipe)",
                    kind="mbr2gpt",
                    note="UEFI-capable firmware; runs after Win10 22H2 if mbr2gpt was missing",
                )
            )
    _maybe_openduet(steps, r)

    # Step C: Windows 11 latest (from Win10 22H2, legacy hosts, older Win10, or older Win11)
    if r.is_win10 or getattr(r, "is_legacy", False) or (r.is_win11 and r.build < latest_target):
        from_l = mapped_version(r.build)
        if getattr(r, "is_legacy", False):
            from_label = f"Windows {from_l} (legacy build {r.build})"
        elif r.is_win10 and r.build < WIN10_22H2_BUILD:
            from_label = "Windows 10 22H2"
        else:
            from_label = f"Windows {from_l} (build {r.build})"
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
        steps.append(
            ChainStep(
                id="win11_cumulative",
                label=f"Latest cumulative for Windows 11 {mapped_version(latest_target)}",
                kind="cumulative",
                note="After the feature update, nudge Windows Update if the ISO lagged the current cumulative",
            )
        )

    # After OS hops: restore soft-uninstalled blockers (Acronis/EaseUS/LGS/…) via winget.
    # Always schedule when the chain includes ISO upgrades (queue filled during patch phase).
    try:
        from .blocker_apps import pending_restores_remaining

        need_restore = pending_restores_remaining()
    except Exception:
        need_restore = False
    try:
        from .logutil import load_state

        st_pending = bool(load_state().get("PendingAppRestores")) and not load_state().get(
            "AppRestoreDone"
        )
    except Exception:
        st_pending = False
    has_iso = any(s.kind == "iso_upgrade" for s in steps)
    if has_iso or need_restore or st_pending:
        if not any(s.id == "restore_apps" for s in steps):
            steps.append(
                ChainStep(
                    id="restore_apps",
                    label="Restore compatible apps (soft-uninstalled blockers)",
                    kind="restore_apps",
                    note=(
                        "Reinstall modern packages via winget when the same product was "
                        "removed before Setup (LGS→GHUB, EaseUS, Macrium, VPN, …)"
                    ),
                )
            )

    if has_iso or any(s.kind in ("restore_apps", "settle", "media_offers") for s in steps):
        if not any(s.id == "post_migration" for s in steps):
            steps.append(
                ChainStep(
                    id="post_migration",
                    label="Post-migration checklist + activation snapshot",
                    kind="post_migration",
                    note="Desktop checklist; activation snapshot; final app restore pass",
                )
            )

    if getattr(r, "is_ltsc", False):
        for s in steps:
            if s.kind == "iso_upgrade" and s.win == "11":
                s.note = (s.note + "; LTSC host — prefer MAGIC_ENTERPRISE_ISO_DIR volume media").strip("; ")

    if not steps:
        steps.append(
            ChainStep(id="done", label="Nothing to do", kind="done", note=f"build {r.build}")
        )
    return steps


def _mbr2gpt_wanted(report: Report) -> bool:
    try:
        from .firmware_matrix import mbr2gpt_wanted

        return bool(mbr2gpt_wanted(report))
    except Exception:
        # Detection failed: keep the historical "MBR gets converted" behavior.
        return True


def _maybe_openduet(steps: list[ChainStep], report: Report) -> None:
    if any(s.id == "openduet_prepare" for s in steps):
        return
    try:
        from .firmware_matrix import decide_boot_plan, snapshot_from_report

        plan = decide_boot_plan(snapshot_from_report(report))
    except Exception:
        return
    if not plan.suggest_openduet:
        return
    steps.append(
        ChainStep(
            id="openduet_prepare",
            label="Optional: prepare OpenDuet/OpenCore files for a legacy-only BIOS",
            kind="openduet_prepare",
            note=(
                "Stages a GPT+FAT32 bridge folder. Does not write a disk. "
                "Writing needs --openduet-write and a confirm token. The stick stays plugged in."
            ),
        )
    )


def format_chain(steps: list[ChainStep]) -> str:
    parts = [s.label for s in steps if s.kind != "done"]
    if not parts:
        return steps[0].label if steps else "n/a"
    return "  =>  ".join(parts)
