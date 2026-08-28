"""
Smart host + ISO version evaluation and upgrade-chain planning.

Research-backed paths (forums + Microsoft docs, 2024–2026):
  - Win10 1511/1607/1809 cannot jump directly to Win11 or 22H2 in one step reliably;
    Microsoft CDN still serves Win10 22H2 + Win11 latest retail ISOs only.
  - Supported keep-apps path: obsolete Win10 → Win10 22H2 (in-place) → Win11 latest.
  - Old Win11 (21H2–23H2) can in-place upgrade to latest Win11 from one ISO.
  - CPUs without SSE4.2/POPCNT: max Win10 22H2 (Win11 24H2+ will not boot).
  - 32-bit Windows: max Win10 22H2 x86; Win11 requires x64 clean install.

See docs/ARCHITECTURE.md and SuperUser / Microsoft upgrade FAQ.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chain import ChainStep, WIN10_22H2_BUILD, WIN11_24H2_BUILD, mapped_version
from .detect import Report
from .logutil import log, load_state, save_state

# Known retail baselines (updated when Microsoft ships new GA ISOs)
KNOWN_WIN11_BUILDS = (26200, 26100, 22631, 22621, 22000)
DEFAULT_LATEST_WIN11_BUILD = 26200  # 25H2-class (Installation Assistant target 2026)
DEFAULT_LATEST_WIN10_BUILD = 19045  # 22H2 — only Win10 ISO on Microsoft CDN


@dataclass
class VersionAssessment:
    host_build: int
    host_version: str
    host_family: str  # "10" | "11"
    host_ubr: int
    target_win11_build: int
    target_win11_label: str
    needs_win10_intermediate: bool
    needs_win11_upgrade: bool
    iso_steps_needed: list[str]
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "host_build": self.host_build,
            "host_version": self.host_version,
            "host_family": self.host_family,
            "host_ubr": self.host_ubr,
            "target_win11_build": self.target_win11_build,
            "target_win11_label": self.target_win11_label,
            "needs_win10_intermediate": self.needs_win10_intermediate,
            "needs_win11_upgrade": self.needs_win11_upgrade,
            "iso_steps_needed": self.iso_steps_needed,
            "notes": self.notes,
        }


def latest_win11_build_from_state() -> int:
    st = load_state()
    try:
        b = int(st.get("LatestWin11Build") or 0)
        if b >= WIN11_24H2_BUILD:
            return b
    except (TypeError, ValueError):
        pass
    return DEFAULT_LATEST_WIN11_BUILD


def remember_latest_win11_build(build: int, *, label: str = "") -> None:
    if build < WIN11_24H2_BUILD:
        return
    patch: dict[str, Any] = {"LatestWin11Build": build}
    if label:
        patch["LatestWin11Label"] = label
    save_state(patch)
    log(f"Latest Win11 ISO baseline recorded: build {build} ({label or 'retail'})", "OK")


def probe_win11_iso_build(iso_path: Path | None) -> int:
    """Mount-inspect Win11 ISO to learn true retail build (for skip logic)."""
    if not iso_path or not iso_path.exists():
        return latest_win11_build_from_state()
    try:
        from .iso_inspect import inspect_iso

        info = inspect_iso(iso_path, compute_hash=False, remount=True)
        if info.win_family == "11" and info.build >= WIN11_24H2_BUILD:
            remember_latest_win11_build(info.build, label=info.display_version)
            return info.build
    except Exception as e:
        log(f"Win11 ISO probe skipped: {e}", "INFO")
    return latest_win11_build_from_state()


def min_build_for_step(step_id: str) -> int:
    if step_id == "win10_22h2":
        return WIN10_22H2_BUILD
    if step_id == "win11_latest":
        return 22000
    return 0


def iso_suitable_for_step(info, step: ChainStep) -> bool:
    """True when mounted ISO build meets the chain step requirement."""
    from .iso_inspect import iso_matches_target

    if not info or not getattr(info, "verified", False):
        return False
    win = step.win or "11"
    arch = step.arch or "x64"
    if not iso_matches_target(info, win, arch):
        return False
    mb = min_build_for_step(step.id)
    if mb and int(getattr(info, "build", 0) or 0) < mb:
        return False
    return True


def evaluate_host(report: Report, *, latest_win11: int | None = None) -> VersionAssessment:
    """Compare host OS to latest reachable target; list ISO hops still required."""
    latest = latest_win11 or latest_win11_build_from_state()
    latest = max(latest, WIN11_24H2_BUILD)
    host_ver = mapped_version(report.build)
    family = "11" if report.is_win11 else "10"
    notes: list[str] = []
    iso_steps: list[str] = []

    needs_win10 = bool(report.is_win10 and report.build < WIN10_22H2_BUILD)
    needs_win11 = False

    if report.architecture != "x64":
        if report.build < WIN10_22H2_BUILD:
            iso_steps.append("win10_22h2")
        notes.append("32-bit host: max keep-apps path is Win10 22H2 x86")
    elif report.sse42 is False:
        if report.build < WIN10_22H2_BUILD:
            iso_steps.append("win10_22h2")
        notes.append("No SSE4.2/POPCNT: stop at Win10 22H2 (Win11 24H2+ non-bootable)")
    else:
        if needs_win10:
            iso_steps.append("win10_22h2")
            notes.append(
                f"Host Win10 {host_ver} (build {report.build}) must step through 22H2 before Win11"
            )
        if report.is_win10 or (report.is_win11 and report.build < latest):
            needs_win11 = True
            iso_steps.append("win11_latest")
            if report.is_win11 and report.build < latest:
                notes.append(
                    f"Win11 {host_ver} → latest retail (build {latest}) via in-place ISO upgrade"
                )

    target_label = mapped_version(latest)
    if latest >= 26200:
        target_label = "25H2"
    elif latest >= 26100:
        target_label = "24H2"

    return VersionAssessment(
        host_build=report.build,
        host_version=host_ver,
        host_family=family,
        host_ubr=int(getattr(report, "ubr", 0) or 0),
        target_win11_build=latest,
        target_win11_label=target_label,
        needs_win10_intermediate=needs_win10,
        needs_win11_upgrade=needs_win11,
        iso_steps_needed=iso_steps,
        notes=notes,
    )


def should_skip_chain_step(step: ChainStep, report: Report, *, latest_win11: int | None = None) -> bool:
    """After reboot, skip steps already satisfied on the live OS."""
    latest = latest_win11 or latest_win11_build_from_state()
    if step.kind == "done":
        return True
    if step.id == "win10_22h2":
        if report.is_win11:
            return True
        if report.is_win10 and report.build >= WIN10_22H2_BUILD:
            return True
    if step.id == "mbr2gpt" and report.partition_style == "GPT":
        return True
    if step.id == "win11_latest" and report.is_win11 and report.build >= latest:
        return True
    return False


def format_assessment(va: VersionAssessment) -> str:
    hops = " → ".join(va.iso_steps_needed) if va.iso_steps_needed else "none"
    return (
        f"Host: Win{va.host_family} {va.host_version} (build {va.host_build}.{va.host_ubr}) | "
        f"Target Win11: {va.target_win11_label} (≥{va.target_win11_build}) | ISO hops: {hops}"
    )
