"""Full upgrade pipeline with intelligent auto-diagnosis - max compatibility."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .autodiag import build_plan, print_plan
from .bypass import apply_hardware_bypass, setup_bypass_args
from .detect import collect_report, is_admin, print_report
from .iso import get_iso
from .logutil import STATE_DIR, init_logging, log, save_state
from .mbrgpt import convert_mbr_to_gpt, repair_boot_manager
from .patches import apply_migration_patches
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
    log("Registered RunOnce continuation after reboot", "OK")


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
        log(f"Launching inplace upgrade via {exe.name}", "STEP")

    if not exe.exists():
        raise FileNotFoundError(exe)

    cmd = [str(exe), *args]
    log(" ".join(cmd), "INFO")
    save_state({"Phase": "SetupRunning", "Cmd": cmd})
    proc = subprocess.Popen(cmd)
    return proc.wait()


def run_diagnose(sink: Callable[[str], None] | None = None) -> dict:
    init_logging(sink)
    r = collect_report()
    print_report(r)
    plan = build_plan(r)
    print_plan(plan)
    out = STATE_DIR / "last-diagnose.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"report": r.as_dict(), "plan": plan.as_dict()}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"Diagnosis + plan written to {out}", "OK")
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
    log("Engine: pure Python - no .NET 4.x / no PowerShell", "OK")
    log("Mode: intelligent auto-diagnosis + max compatibility", "OK")

    if not is_admin():
        raise PermissionError("Administrator required for upgrade pipeline")

    r = collect_report()
    print_report(r)
    plan = build_plan(r)
    print_plan(plan)
    save_state({"Phase": "Diagnosed", "Plan": plan.as_dict(), "Report": r.as_dict()})

    action_ids = {a.id for a in plan.actions}

    if plan.target == "already_done":
        apply_hardware_bypass()
        save_state({"Phase": "Done"})
        return 0

    # Always: patches + registry (unless plan somehow omitted them)
    if "patches" in action_ids or "space" in action_ids:
        apply_migration_patches()
    else:
        apply_migration_patches()
    apply_hardware_bypass()

    if resume:
        skip_intermediate = True

    # MBR conversion when planned
    if not skip_mbr and ("mbr2gpt" in action_ids or "bootmgr" in action_ids):
        if r.partition_style == "MBR" and r.mbr2gpt_available:
            ok, code, msg = convert_mbr_to_gpt(r.disk_number)
            if not ok:
                log(f"MBR conversion failed ({msg}) - continuing if possible", "WARN")
            else:
                save_state({"NeedsUefiFirmware": True, "Mbr2gptCode": code})
        elif r.partition_style == "GPT":
            repair_boot_manager(prefer_uefi=True)

    # 32-bit / no-SSE42 -> Win10 22H2 max path
    if plan.target == "win10_22h2":
        arch = "x86" if r.architecture != "x64" else "x64"
        if r.is_win10 and r.build >= 19045 and not r.needs_intermediate:
            log("Already on Win10 22H2-class; Win11 not possible on this hardware/arch.", "OK")
            save_state({"Phase": "MaxReached", "Target": "win10_22h2"})
            return 0
        log(f"=== Maximum safe path: Windows 10 22H2 ({arch}) ===", "STEP")
        iso = Path(win10_iso) if win10_iso else get_iso("10", r.locale, arch=arch)
        root = mount_iso(iso)
        save_state({"Phase": "Win10MaxSetup"})
        return _run_setup(root, use_server=False, quiet=quiet)

    # Intermediate obsolete Win10
    need_intermediate = (
        not skip_intermediate
        and (
            "intermediate_win10" in action_ids
            or "intermediate_then_mbr" in action_ids
            or r.needs_intermediate
        )
    )
    if need_intermediate:
        log("=== Intermediate Windows 10 22H2 ===", "STEP")
        iso = Path(win10_iso) if win10_iso else get_iso("10", r.locale, arch="x64")
        root = mount_iso(iso)
        _runonce_register()
        save_state({"Phase": "IntermediateSetup", "AfterReboot": "ContinueToWin11"})
        return _run_setup(root, use_server=False, quiet=quiet)

    # Refresh after possible changes
    r = collect_report()
    if not skip_mbr and r.partition_style == "MBR" and r.mbr2gpt_available:
        convert_mbr_to_gpt(r.disk_number)

    if not plan.can_win11:
        log("Plan does not allow Win11 - stopped after max compatible actions.", "WARN")
        return 0

    log("=== Windows 11 latest (inplace /product server) ===", "STEP")
    # Re-apply bypasses immediately before setup
    apply_hardware_bypass()
    iso = Path(win11_iso) if win11_iso else get_iso("11", r.locale, arch="x64")
    root = mount_iso(iso)
    code = _run_setup(root, use_server=True, quiet=quiet)
    save_state({"Phase": "Win11SetupLaunched", "SetupExit": code})
    log("Windows 11 setup launched - keep files and apps.", "OK")
    return code
