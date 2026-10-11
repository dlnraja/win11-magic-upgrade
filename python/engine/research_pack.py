"""
Research remediation pack — runs investigation-backed preflight before Setup.

Sources: Microsoft SetupDiag docs, upgrade resolution procedures, CompatData guides,
Flyby11/setupprep parity, profile duplicate Q&A, unsigned driver guidance.
"""
from __future__ import annotations

from typing import Any

from .logutil import log, save_state


def apply_research_pack(report: Any | None = None, *, resume: bool = False) -> dict[str, Any]:
    """
    Best-effort suite. Never blocks One-Click unless a sub-module raises UpgradeBlockedError.
    """
    summary: dict[str, Any] = {"steps": [], "errors": []}
    log("=== Research remediation pack (MS + forums) ===", "STEP")

    def _run(name: str, fn) -> None:
        try:
            fn()
            summary["steps"].append(name)
            log(f"Research OK: {name}", "OK")
        except Exception as e:
            summary["errors"].append(f"{name}: {e}")
            log(f"Research step {name}: {e}", "WARN")

    def _preflight() -> None:
        from .preflight_power import harden_power_for_setup

        harden_power_for_setup()

    def _compat() -> None:
        from .compat_scan import run_compat_scan

        run_compat_scan(soften=True)

    def _profiles() -> None:
        from .profile_heal import audit_profile_list

        audit_profile_list()

    def _drivers() -> None:
        from .unsigned_drivers import scan_unsigned_and_problem_drivers

        scan_unsigned_and_problem_drivers()

    def _rollback() -> None:
        from .rollback_scan import scan_rollback_folder

        scan_rollback_folder()

    def _setupdiag() -> None:
        from .setupdiag_runner import run_setupdiag

        run_setupdiag()

    def _inventory() -> None:
        if report is None:
            return
        from .inventory_export import export_inventory

        export_inventory(report)

    def _confidence() -> None:
        if report is None:
            return
        from .chain import build_version_chain, format_chain
        from .confidence import evaluate_confidence

        evaluate_confidence(report, chain_label=format_chain(build_version_chain(report)))

    _run("preflight_power", _preflight)
    _run("compat_scan", _compat)
    _run("profile_heal", _profiles)
    _run("unsigned_drivers", _drivers)
    _run("rollback_scan", _rollback)
    if not resume:
        _run("setupdiag", _setupdiag)
    _run("inventory", _inventory)
    _run("confidence", _confidence)

    save_state({"ResearchPack": summary})
    log(
        f"Research pack done: {len(summary['steps'])} ok, {len(summary['errors'])} warn",
        "OK" if not summary["errors"] else "WARN",
    )
    return summary
