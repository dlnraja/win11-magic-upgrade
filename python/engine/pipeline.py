"""Full upgrade pipeline — pure Python, no .NET Framework 4.x, no PowerShell."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .bypass import apply_hardware_bypass, setup_bypass_args
from .detect import collect_report, is_admin, print_report
from .iso import get_iso
from .logutil import STATE_DIR, init_logging, log, save_state
from .mbrgpt import convert_mbr_to_gpt
from .patches import apply_migration_patches
from .virtdisk import mount_iso


def _run_setup(setup_root: str, use_server: bool, quiet: bool = False) -> int:
    root = Path(setup_root)
    setup = root / "setup.exe"
    prep = root / "sources" / "setupprep.exe"
    if use_server:
        apply_hardware_bypass()
        exe = prep if prep.exists() else setup
        args = setup_bypass_args(quiet=quiet)
        log(f"Launching {exe.name} /product server (Flyby11 method, no .NET app)", "STEP")
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
        log(f"Launching intermediate upgrade via {exe.name}", "STEP")

    if not exe.exists():
        raise FileNotFoundError(exe)

    cmd = [str(exe), *args]
    log(" ".join(cmd), "INFO")
    save_state({"Phase": "SetupRunning", "Cmd": cmd})
    # Visible window so user can confirm Keep apps/files if not quiet
    proc = subprocess.Popen(cmd)
    return proc.wait()


def run_diagnose(sink: Callable[[str], None] | None = None) -> dict:
    init_logging(sink)
    r = collect_report()
    print_report(r)
    out = STATE_DIR / "last-diagnose.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r.as_dict(), indent=2), encoding="utf-8")
    log(f"Diagnosis written to {out}", "OK")
    return r.as_dict()


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
        log(f"Disk already {r.partition_style}", "OK")
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
    log("Engine: pure Python portable — does NOT require .NET Framework 4.x", "OK")
    log("Engine: does NOT call powershell.exe / FlyOOBE", "OK")

    if not is_admin():
        raise PermissionError("Administrator required for upgrade pipeline")

    r = collect_report()
    print_report(r)
    save_state({"Phase": "Detected", "Report": r.as_dict()})

    if r.is_win11 and r.build >= 26100:
        log("Already on Windows 11 24H2+. Nothing mandatory.", "OK")
        save_state({"Phase": "Done"})
        return 0

    if r.sse42 is False:
        raise RuntimeError("CPU incompatible with Win11 24H2+ (no SSE4.2/POPCNT)")
    if r.architecture != "x64":
        raise RuntimeError("Windows 11 requires 64-bit Windows")

    apply_migration_patches()
    apply_hardware_bypass()

    if not skip_mbr and r.partition_style == "MBR":
        if r.mbr2gpt_available:
            ok, code, msg = convert_mbr_to_gpt(r.disk_number)
            if not ok:
                log(f"MBR conversion failed ({msg}) — continuing cautiously", "WARN")
            else:
                save_state({"NeedsUefiFirmware": True, "Mbr2gptCode": code})
        else:
            log("mbr2gpt unavailable — convert after intermediate Win10 upgrade", "WARN")
            save_state({"PendingMbrConvert": True})

    if resume:
        skip_intermediate = True

    if not skip_intermediate and r.needs_intermediate:
        log("=== Intermediate Windows 10 22H2 ===", "STEP")
        iso = Path(win10_iso) if win10_iso else get_iso("10", r.locale)
        root = mount_iso(iso)
        # Register RunOnce via reg.exe (no PowerShell)
        exe = sys.executable
        if getattr(sys, "frozen", False):
            runonce = f'"{exe}" --cli --resume'
        else:
            runonce = f'"{exe}" "{Path(__file__).resolve().parents[1] / "magic_upgrade.py"}" --cli --resume'
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
        save_state({"Phase": "IntermediateSetup", "AfterReboot": "ContinueToWin11"})
        return _run_setup(root, use_server=False, quiet=quiet)

    # Refresh report after possible intermediate
    r = collect_report()
    if r.partition_style == "MBR" and r.mbr2gpt_available and not skip_mbr:
        convert_mbr_to_gpt(r.disk_number)

    log("=== Windows 11 latest (inplace /product server) ===", "STEP")
    iso = Path(win11_iso) if win11_iso else get_iso("11", r.locale)
    root = mount_iso(iso)
    code = _run_setup(root, use_server=True, quiet=quiet)
    save_state({"Phase": "Win11SetupLaunched", "SetupExit": code})
    log("Windows 11 setup launched — keep files and apps.", "OK")
    return code
