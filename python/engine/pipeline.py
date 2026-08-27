"""Full upgrade pipeline: intermediate version chain across reboots."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .autodiag import build_plan, print_plan
from .bypass import apply_hardware_bypass, setup_bypass_args
from .chain import ChainStep, build_version_chain, format_chain
from .detect import collect_report, is_admin, print_report
from .iso import get_iso
from .logutil import STATE_DIR, init_logging, load_state, log, save_state
from .mbrgpt import convert_mbr_to_gpt, repair_boot_manager
from .patches import apply_migration_patches
from .sysreserved import inspect_and_fix_system_reserved
from .virtdisk import mount_iso


def _runonce_register() -> None:
    exe = sys.executable
    if getattr(sys, "frozen", False):
        runonce = f'"{exe}" --cli --resume'
    else:
        runonce = (
            f'"{exe}" "{Path(__file__).resolve().parents[1] / "magic_upgrade.py"}" --cli --resume'
        )
    subprocess.run(
        [
            "reg",
            "add",
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
            "/v",
            "Win11MagicUpgrade",
            "/t",
            "REG_SZ",
            "/d",
            runonce,
            "/f",
        ],
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log("RunOnce registered: continue chain after reboot", "OK")


def _run_setup(setup_root: str, use_server: bool, quiet: bool = False) -> int:
    root = Path(setup_root)
    setup = root / "setup.exe"
    prep = root / "sources" / "setupprep.exe"
    if use_server:
        apply_hardware_bypass()
        exe = prep if prep.exists() else setup
        args = setup_bypass_args(quiet=quiet)
        log(f"Launching {exe.name} /product server (keep apps/files)", "STEP")
    else:
        exe = setup
        args = [
            "/auto",
            "upgrade",
            "/compat",
            "IgnoreWarning",
            "/dynamicupdate",
            "disable",
            "/eula",
            "accept",
        ]
        if quiet:
            args += ["/quiet", "/showoobe", "none"]
        log(f"Launching intermediate inplace upgrade via {exe.name}", "STEP")

    if not exe.exists():
        raise FileNotFoundError(exe)

    cmd = [str(exe), *args]
    log(" ".join(cmd), "INFO")
    save_state({"Phase": "SetupRunning", "Cmd": cmd})
    return subprocess.Popen(cmd).wait()


def _persist_chain(steps: list[ChainStep], index: int) -> None:
    save_state(
        {
            "Chain": [s.as_dict() for s in steps],
            "ChainIndex": index,
            "ChainLabel": format_chain(steps),
        }
    )


def _next_pending_index(steps: list[ChainStep], start: int, report) -> int:
    """Skip steps already satisfied after a reboot."""
    i = start
    while i < len(steps):
        s = steps[i]
        if s.kind == "done":
            return i
        if s.id == "win10_22h2" and report.is_win10 and report.build >= 19045:
            log(f"Skip already done: {s.label}", "OK")
            i += 1
            continue
        if s.id == "win10_22h2" and report.is_win11:
            log(f"Skip (already past Win10): {s.label}", "OK")
            i += 1
            continue
        if s.id == "mbr2gpt" and report.partition_style == "GPT":
            log(f"Skip already GPT: {s.label}", "OK")
            i += 1
            continue
        if s.id == "win11_latest" and report.is_win11 and report.build >= 26100:
            log(f"Skip already Win11 latest-class: {s.label}", "OK")
            i += 1
            continue
        return i
    return i


def _execute_step(
    step: ChainStep,
    step_no: int,
    total: int,
    report,
    quiet: bool,
    win10_iso: str | None,
    win11_iso: str | None,
) -> int | None:
    """
    Run one chain step.
    Returns setup exit code if an ISO upgrade was launched (caller should stop),
    or None if step finished in-process and chain can continue.
    """
    log(f"=== Chain step {step_no}/{total}: {step.label} ===", "STEP")
    if step.note:
        log(step.note, "INFO")

    if step.kind == "done":
        log(step.label, "OK")
        return None

    if step.kind == "fix_srp":
        inspect_and_fix_system_reserved(force_expand=False)
        return None

    if step.kind == "fix_bootmgr":
        from .bootmgr import apply_smart_boot_strategy

        apply_smart_boot_strategy(os_arch=report.architecture, is_uefi=report.is_uefi)
        return None

    if step.kind == "hybrid_ia32":
        from .hybrid_uefi import apply_hybrid_ia32_path

        # Activate default bootia32 only when OS is already x64 (BIOS handoff ready)
        activate = report.architecture == "x64"
        res = apply_hybrid_ia32_path(activate=activate, prepare_bios=True)
        if not res.get("ok"):
            log("Hybrid IA32 deploy failed - continuing with safest keep-apps path", "WARN")
        return None

    if step.kind == "mbr2gpt":
        if not report.mbr2gpt_available:
            log("mbr2gpt not available yet - will retry after Win10 intermediate", "WARN")
            return None
        if report.partition_style != "MBR":
            repair_boot_manager(prefer_uefi=True)
            return None
        ok, code, msg = convert_mbr_to_gpt(report.disk_number)
        if not ok:
            log(f"MBR conversion failed ({msg}) - continue chain cautiously", "WARN")
        else:
            save_state({"NeedsUefiFirmware": True, "Mbr2gptCode": code})
        return None

    if step.kind == "iso_upgrade":
        arch = step.arch or "x64"
        win = step.win or "11"
        if win == "10":
            iso = Path(win10_iso) if win10_iso else get_iso("10", report.locale, arch=arch)
        else:
            iso = Path(win11_iso) if win11_iso else get_iso("11", report.locale, arch="x64")
        root = mount_iso(iso)
        # More steps remain after this ISO? Register resume.
        return _run_setup(root, use_server=bool(step.use_server_product), quiet=quiet)

    return None


def run_diagnose(sink: Callable[[str], None] | None = None) -> dict:
    init_logging(sink)
    r = collect_report()
    print_report(r)
    plan = build_plan(r)
    print_plan(plan)
    chain = build_version_chain(r)
    log("=== Intermediate version chain ===", "STEP")
    log(format_chain(chain), "OK")
    for i, s in enumerate(chain, 1):
        log(f"  {i}. [{s.kind}] {s.label}")
    out = STATE_DIR / "last-diagnose.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "report": r.as_dict(),
        "plan": plan.as_dict(),
        "chain": [s.as_dict() for s in chain],
        "chain_path": format_chain(chain),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"Diagnosis + chain written to {out}", "OK")
    return payload


def apply_bypass_only(sink: Callable[[str], None] | None = None) -> None:
    init_logging(sink)
    if not is_admin():
        raise PermissionError("Administrator required")
    apply_hardware_bypass()


def convert_mbr_only(sink: Callable[[str], None] | None = None) -> None:
    init_logging(sink)
    if not is_admin():
        raise PermissionError("Administrator required")
    r = collect_report()
    if r.partition_style != "MBR":
        log(f"Disk already {r.partition_style} - repairing boot manager only", "OK")
        repair_boot_manager(prefer_uefi=(r.partition_style == "GPT" or r.is_uefi))
        return
    ok, code, msg = convert_mbr_to_gpt(r.disk_number)
    if not ok:
        raise RuntimeError(f"MBR2GPT failed: {msg} ({code})")


def fix_system_reserved_only(sink: Callable[[str], None] | None = None) -> None:
    init_logging(sink)
    if not is_admin():
        raise PermissionError("Administrator required")
    from .sysreserved import inspect_and_fix_system_reserved, scan_logs_for_srp_error

    force = scan_logs_for_srp_error()
    result = inspect_and_fix_system_reserved(force_expand=force)
    if not result.get("ok"):
        raise RuntimeError("System Reserved / EFI fix did not complete successfully")


def deploy_hybrid_only(sink: Callable[[str], None] | None = None, *, activate: bool = False) -> None:
    init_logging(sink)
    if not is_admin():
        raise PermissionError("Administrator required")
    from .hybrid_uefi import apply_hybrid_ia32_path

    res = apply_hybrid_ia32_path(activate=activate, prepare_bios=True)
    if not res.get("ok"):
        raise RuntimeError("Hybrid IA32 UEFI deploy failed")


def run_pipeline(
    sink: Callable[[str], None] | None = None,
    *,
    quiet: bool = False,
    skip_mbr: bool = False,
    skip_intermediate: bool = False,
    win10_iso: str | None = None,
    win11_iso: str | None = None,
    resume: bool = False,
) -> int:
    init_logging(sink)
    log("Engine: pure Python - intermediate version chain enabled", "OK")

    if not is_admin():
        raise PermissionError("Administrator required for upgrade pipeline")

    r = collect_report()
    print_report(r)

    # Rebuild chain from live system (handles resume after intermediate OS change)
    steps = build_version_chain(r)
    if skip_mbr:
        steps = [s for s in steps if s.id != "mbr2gpt"]
    if skip_intermediate:
        steps = [s for s in steps if s.id != "win10_22h2"]

    plan = build_plan(r)
    print_plan(plan)
    log("=== Intermediate version chain ===", "STEP")
    log(format_chain(steps), "OK")

    state = load_state()
    start_index = int(state.get("ChainIndex", 0) or 0) if resume else 0
    if resume:
        log(f"Resuming chain after reboot (saved index={start_index})", "STEP")
        start_index = _next_pending_index(steps, 0, r)  # re-evaluate from live OS
    else:
        start_index = _next_pending_index(steps, 0, r)

    _persist_chain(steps, start_index)
    apply_migration_patches()
    apply_hardware_bypass()

    total = len(steps)
    i = start_index
    while i < total:
        step = steps[i]
        _persist_chain(steps, i)

        # ISO upgrades need RunOnce if more steps follow
        remaining_after = steps[i + 1 :]
        needs_resume = step.kind == "iso_upgrade" and any(
            s.kind in ("iso_upgrade", "mbr2gpt", "fix_srp") for s in remaining_after
        )
        if needs_resume:
            _runonce_register()

        result = _execute_step(
            step,
            step_no=i + 1,
            total=total,
            report=r,
            quiet=quiet,
            win10_iso=win10_iso,
            win11_iso=win11_iso,
        )

        if step.kind == "iso_upgrade":
            # Setup launched - OS will reboot; chain continues via RunOnce
            save_state(
                {
                    "Phase": "WaitingReboot",
                    "ChainIndex": i + 1,
                    "LastStep": step.as_dict(),
                }
            )
            log(
                f"Intermediate setup launched ({step.label}). "
                "After reboot the tool continues the next step automatically.",
                "OK",
            )
            return int(result or 0)

        # In-process step done - refresh report for next decisions
        i += 1
        r = collect_report()
        # Re-sync skip logic if OS changed unexpectedly
        i = _next_pending_index(steps, i, r)

    save_state({"Phase": "Done", "ChainIndex": total})
    log("Version chain completed.", "OK")
    return 0
