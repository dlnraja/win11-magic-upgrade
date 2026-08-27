"""Full upgrade pipeline: intermediate version chain across reboots."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .autodiag import build_plan, print_plan
from .bypass import apply_hardware_bypass, setup_bypass_args
from .chain import ChainStep, build_version_chain, format_chain
from .detect import collect_report, is_admin, print_report
from .errors import EXIT_BLOCKED, EXIT_FAILED, UpgradeBlockedError, remember_failure
from .iso import get_iso
from .logutil import STATE_DIR, init_logging, load_state, log, save_state, write_migration_report, get_log_paths
from .mbrgpt import convert_mbr_to_gpt, repair_boot_manager
from .patches import AutonomousRebootRequired, apply_migration_patches
from .progress import end_session, report_progress, set_phase, set_step, start_session
from .sysreserved import inspect_and_fix_system_reserved
from .virtdisk import mount_iso


def _autodiag_links(
    *,
    kind: str,
    message: str,
    report: Any = None,
    srp_result: dict | None = None,
    extra: dict | None = None,
) -> dict[str, str | None]:
    """Privacy-scrubbed GitHub issue (+ optional PR). Never raises."""
    try:
        from .gh_report import report_failure_to_github

        rep = None
        if report is not None:
            rep = report.as_dict() if hasattr(report, "as_dict") else report
        return report_failure_to_github(
            kind=kind,
            message=message,
            report=rep if isinstance(rep, dict) else None,
            srp_result=srp_result,
            extra=extra,
        )
    except Exception as e:
        log(f"autodiag report skipped: {e}", "WARN")
        return {}


def _links_hint(links: dict[str, str | None] | None) -> str:
    if not links:
        return ""
    parts = []
    if links.get("issue"):
        parts.append(f"Issue: {links['issue']}")
    if links.get("pr"):
        parts.append(f"PR: {links['pr']}")
    return (" " + " ".join(parts)) if parts else ""


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


def _runonce_unregister() -> None:
    subprocess.run(
        [
            "reg",
            "delete",
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
            "/v",
            "Win11MagicUpgrade",
            "/f",
        ],
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log("RunOnce cleared", "OK")


# Setup exit codes that mean "launched / reboot needed" rather than hard failure
SETUP_OK_CODES = {0, 3010, 3011, -2147021886}


def _run_setup(setup_root: str, use_server: bool, quiet: bool = False) -> int:
    """
    Launch Setup (Flyby11/FlyOOBE style) and return promptly.
    Prefer sources\\setupprep.exe + /Product Server like Flyby11 IsoHandler.
    """
    root = Path(setup_root)
    setup = root / "setup.exe"
    prep = root / "sources" / "setupprep.exe"
    if use_server:
        apply_hardware_bypass()
        # Flyby11: always prefer setupprep.exe when present
        exe = prep if prep.exists() else setup
        args = setup_bypass_args(quiet=quiet, experimental=True)
        log(
            f"Launching {exe.name} (Flyby11/FlyOOBE: /Product Server + Compat IgnoreWarning + MigrateDrivers All)",
            "STEP",
        )
    else:
        exe = setup
        args = [
            "/auto",
            "upgrade",
            "/Compat",
            "IgnoreWarning",
            "/MigrateDrivers",
            "All",
            "/dynamicupdate",
            "enable",
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
    save_state({"Phase": "SetupRunning", "Cmd": cmd, "Method": "Flyby11Parity"})
    try:
        proc = subprocess.Popen(cmd)
    except OSError as e:
        raise RuntimeError(f"Failed to launch Setup: {e}") from e

    try:
        code = proc.wait(timeout=8)
        log(f"Setup exited quickly with code {code}", "WARN" if code not in SETUP_OK_CODES else "OK")
        save_state({"Phase": "SetupExitedEarly", "SetupPid": proc.pid, "ExitCode": code})
        return int(code)
    except subprocess.TimeoutExpired:
        save_state({"Phase": "SetupRunning", "SetupPid": proc.pid})
        log(f"Setup running (pid={proc.pid}) — chain will resume after reboot via RunOnce", "OK")
        return 0


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
        from .boot_safe import run_esp_srp_with_restore
        from .sysreserved import scan_logs_for_srp_error

        force = scan_logs_for_srp_error()
        disk = getattr(report, "disk_number", None)
        rep = report.as_dict() if hasattr(report, "as_dict") else None
        res = run_esp_srp_with_restore(
            force_expand=force,
            system_disk=disk,
            retries=2,
            report=rep if isinstance(rep, dict) else None,
        )
        if isinstance(res, dict) and not res.get("ok", True):
            links = res.get("autodiag") or {}
            if not links:
                links = _autodiag_links(
                    kind="esp-srp-failed",
                    message="ESP/SRP fix failed — cannot continue autonomous upgrade",
                    report=report,
                    srp_result=res,
                    extra={"step": step.as_dict() if hasattr(step, "as_dict") else str(step)},
                )
            hint = _links_hint(links if isinstance(links, dict) else {})
            bootable = res.get("bootable", False)
            if not bootable:
                log("ESP/SRP failed AND bootability unverified — refusing to continue", "ERROR")
            if os.environ.get("MAGIC_SRP_CONTINUE", "").strip().lower() in ("1", "true", "yes"):
                log("ESP/SRP still failing — continuing because MAGIC_SRP_CONTINUE=1" + hint, "WARN")
                return None
            # PC should still reboot into Windows (restore ran); block upgrade chain only
            full = (
                "ESP/SRP fix failed — cannot continue autonomous upgrade."
                + (" Boot restore OK." if bootable else " Boot restore UNCERTAIN.")
                + (hint or " (sanitized autodiag saved locally)")
            )
            remember_failure(full, kind="esp-srp-failed", links=links if isinstance(links, dict) else {})
            raise UpgradeBlockedError(full, kind="esp-srp-failed", links=links if isinstance(links, dict) else {})
        return None

    if step.kind == "fix_bootmgr":
        from .bootmgr import apply_smart_boot_strategy

        apply_smart_boot_strategy(os_arch=report.architecture, is_uefi=report.is_uefi)
        return None

    if step.kind == "hybrid_ia32":
        from .hybrid_uefi import apply_hybrid_ia32_path

        # Always activate when OS is x64 — One-Click must be fully autonomous
        activate = True
        res = apply_hybrid_ia32_path(activate=activate, prepare_bios=True)
        if not res.get("ok"):
            raise RuntimeError("Hybrid IA32 deploy failed — cannot continue Win11 x64 path")
        return None

    if step.kind == "mbr2gpt":
        if not report.mbr2gpt_available:
            log("mbr2gpt not available yet - will retry after Win10 intermediate", "WARN")
            return None
        if report.partition_style != "MBR":
            from .boot_safe import validated_repair_boot_manager

            validated_repair_boot_manager(prefer_uefi=True)
            return None
        if getattr(report, "disk_number", -1) is None or int(report.disk_number) < 0:
            raise RuntimeError("MBR→GPT refused: system disk # unknown (safety)")
        from .boot_safe import validated_mbr_to_gpt

        ok, code, msg, meta = validated_mbr_to_gpt(report.disk_number)
        if not ok:
            if meta.get("fallback"):
                log(
                    f"MBR→GPT failed — rescue staged: {meta['fallback'].get('guide')}",
                    "WARN",
                )
            if meta.get("autodiag"):
                log(f"Autodiag: {meta['autodiag']}", "INFO")
            bootable = (meta.get("guarantee") or {}).get("bootable", False)
            if not bootable:
                raise RuntimeError(
                    f"MBR→GPT failed ({msg}) — bootability NOT verified; fix WinRE before reboot"
                )
            raise RuntimeError(
                f"MBR→GPT failed ({msg}) — PC kept bootable via restore; stop autonomous chain"
            )
        # Only reboot when conversion OK and bootable
        if not (meta.get("guarantee") or {}).get("bootable", False):
            raise RuntimeError("MBR→GPT reported OK but bootability check failed — reboot refused")
        save_state({"NeedsUefiFirmware": True, "Mbr2gptCode": code, "BootMeta": meta.get("postflight")})
        from .boot_safe import safe_reboot_after_boot_op

        safe_reboot_after_boot_op(
            success=True,
            reason="Win11MagicUpgrade after MBR2GPT",
            seconds=50,
            system_disk=report.disk_number,
            expect_uefi=True,
        )
        raise AutonomousRebootRequired("mbr2gpt")

    if step.kind == "iso_upgrade":
        arch = step.arch or "x64"
        win = step.win or "11"
        # Re-apply intelligent compat right before Setup (fresh Appraiser caches)
        try:
            from .compat import make_system_win11_compatible

            make_system_win11_compatible(report)
        except Exception as e:
            log(f"Pre-Setup compat refresh: {e}", "WARN")
            apply_hardware_bypass(report)
        if win == "10":
            iso = Path(win10_iso) if win10_iso else get_iso("10", report.locale, arch=arch)
        else:
            iso = Path(win11_iso) if win11_iso else get_iso("11", report.locale, arch="x64")
        root = mount_iso(iso)
        # Win11: writable stage + Appraiser neutralize (works when /product server is blocked)
        if win == "11":
            try:
                from .media_bypass import prepare_setup_root

                root = str(prepare_setup_root(root, win11=True))
            except Exception as e:
                log(f"Media Appraiser bypass stage skipped: {e}", "WARN")
        # Win11 always uses /product server + IgnoreWarning; Win10 also IgnoreWarning
        use_server = bool(step.use_server_product) or win == "11"
        return _run_setup(root, use_server=use_server, quiet=quiet)

    return None


def run_diagnose(sink: Callable[[str], None] | None = None) -> dict:
    init_logging(sink)
    r = collect_report()
    print_report(r)
    plan = build_plan(r)
    print_plan(plan)
    chain = build_version_chain(r)
    log('=== Intermediate version chain ===', 'STEP')
    log(format_chain(chain), 'OK')
    for i, s in enumerate(chain, 1):
        log(f'  {i}. [{s.kind}] {s.label}')
    out = STATE_DIR / 'last-diagnose.json'
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        'report': r.as_dict(),
        'plan': plan.as_dict(),
        'chain': [s.as_dict() for s in chain],
        'chain_path': format_chain(chain),
        'logs': get_log_paths(),
    }
    preventive_installed = False
    preventive_ver = 0
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Win11MagicUpgrade'
        ) as k:
            preventive_installed = bool(winreg.QueryValueEx(k, 'PreventivePackInstalled')[0])
            try:
                preventive_ver = int(winreg.QueryValueEx(k, 'PreventivePackVersion')[0])
            except OSError:
                preventive_ver = 0
    except OSError:
        pass
    payload['preventive_pack'] = {
        'installed': preventive_installed,
        'version': preventive_ver,
    }
    log(
        f"Preventive pack: {'INSTALLED v' + str(preventive_ver) if preventive_installed else 'NOT INSTALLED'}",
        'OK' if preventive_installed else 'WARN',
    )
    out.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    log(f'Diagnosis + chain written to {out}', 'OK')
    write_migration_report(
        title='Win11 Magic Upgrade — Diagnose Report',
        extra={
            'Result': 'DIAGNOSE',
            'Target': plan.target,
            'Chain': format_chain(chain),
            'CanWin11': plan.can_win11,
            'PreventivePack': preventive_installed,
            'PreventivePackVersion': preventive_ver,
        },
    )
    try:
        from .support import write_support_pack

        write_support_pack(extra={"Mode": "DIAGNOSE", "Target": plan.target})
    except Exception as e:
        log(f"Support pack: {e}", "WARN")
    return payload


def apply_bypass_only(sink: Callable[[str], None] | None = None) -> None:
    init_logging(sink)
    if not is_admin():
        raise PermissionError('Administrator required')
    from .preventive import install_all_preventive_patches
    from .compat import make_system_win11_compatible
    from .detect import collect_report

    install_all_preventive_patches()
    r = collect_report()
    summary = make_system_win11_compatible(r)
    write_migration_report(
        extra={
            'Result': 'BYPASS_AND_COMPAT_ENGINE',
            'CompatStrategy': (summary or {}).get('Assessment', {}).get('strategy'),
            'Gaps': (summary or {}).get('Assessment', {}).get('gaps'),
        }
    )


def convert_mbr_only(sink: Callable[[str], None] | None = None) -> None:
    init_logging(sink)
    if not is_admin():
        raise PermissionError('Administrator required')
    try:
        r = collect_report()
        if r.partition_style != 'MBR':
            log(f'Disk already {r.partition_style} - repairing boot manager only', 'OK')
            from .boot_safe import validated_repair_boot_manager

            validated_repair_boot_manager(
                prefer_uefi=(r.partition_style == 'GPT' or r.is_uefi)
            )
        else:
            if r.disk_number is None or int(r.disk_number) < 0:
                raise RuntimeError('MBR2GPT refused: system disk # unknown (safety)')
            from .boot_safe import validated_mbr_to_gpt

            ok, code, msg, meta = validated_mbr_to_gpt(r.disk_number)
            if not ok:
                raise RuntimeError(f'MBR2GPT failed: {msg} ({code})')
        write_migration_report(extra={'Result': 'MBR_OK', 'Disk': r.disk_number})
    except Exception as e:
        log(str(e), 'ERROR')
        write_migration_report(extra={'Result': 'MBR_FAILED', 'Exception': str(e)})
        raise


def fix_system_reserved_only(sink: Callable[[str], None] | None = None) -> None:
    init_logging(sink)
    if not is_admin():
        raise PermissionError('Administrator required')
    from .sysreserved import scan_logs_for_srp_error

    try:
        force = scan_logs_for_srp_error()
        from .detect import collect_report

        disk = None
        report_dict = None
        try:
            r = collect_report()
            disk = r.disk_number
            report_dict = r.as_dict()
        except Exception:
            pass
        from .boot_safe import run_esp_srp_with_restore

        result = run_esp_srp_with_restore(
            force_expand=force,
            system_disk=disk,
            retries=2,
            report=report_dict,
        )
        if not result.get('ok'):
            links = result.get('autodiag') or {}
            hint = ''
            if isinstance(links, dict) and links.get('issue'):
                hint = f" Issue: {links['issue']}"
            boot = 'bootable' if result.get('bootable') else 'boot-uncertain'
            raise UpgradeBlockedError(
                f"System Reserved / EFI fix did not complete ({boot}).{hint}",
                kind='esp-srp-failed',
                links=links if isinstance(links, dict) else {},
            )
        write_migration_report(
            extra={
                'Result': 'SRP_OK',
                'mode': result.get('mode'),
                'free_mb': result.get('free_mb'),
                'expanded': result.get('expanded'),
                'system_disk': result.get('system_disk'),
                'bootable': result.get('bootable'),
            }
        )
    except Exception as e:
        log(str(e), 'ERROR')
        write_migration_report(extra={'Result': 'SRP_FAILED', 'Exception': str(e)})
        raise


def deploy_hybrid_only(sink: Callable[[str], None] | None = None, *, activate: bool = False) -> None:
    init_logging(sink)
    if not is_admin():
        raise PermissionError('Administrator required')
    from .hybrid_uefi import apply_hybrid_ia32_path

    try:
        res = apply_hybrid_ia32_path(activate=activate, prepare_bios=True)
        if not res.get('ok'):
            raise RuntimeError('Hybrid IA32 UEFI deploy failed')
        write_migration_report(
            extra={'Result': 'HYBRID_OK', 'Activated': res.get('activated'), 'Tag': res.get('tag')}
        )
    except Exception as e:
        log(str(e), 'ERROR')
        write_migration_report(extra={'Result': 'HYBRID_FAILED', 'Exception': str(e)})
        raise


def run_patch_enrichment(
    sink: Callable[[str], None] | None = None,
    *,
    deep_heal: bool = False,
) -> None:
    """
    Patch / enrich / support mode: install ALL preventive patches + runtime remediation
    without launching setup ISO.
    """
    init_logging(sink)
    if not is_admin():
        raise PermissionError("Administrator required")
    log("=== PATCH mode: INSTALL preventives + runtime remediation (no ISO) ===", "STEP")
    from .preventive import install_all_preventive_patches

    inv = install_all_preventive_patches()
    r = collect_report()
    print_report(r)
    plan = build_plan(r)
    print_plan(plan)
    apply_hardware_bypass()
    apply_migration_patches(install_preventive=False)
    if deep_heal:
        from .enrich import dism_component_cleanup_and_heal

        dism_component_cleanup_and_heal()
    from .support import write_support_pack

    write_support_pack(
        extra={
            "Mode": "PATCH_DEEP" if deep_heal else "PATCH",
            "PreventiveOk": inv.get("CountOk"),
            "Target": plan.target,
            "Chain": format_chain(build_version_chain(r)),
        }
    )
    log("Preventive pack INSTALLED + runtime remediations applied. See SupportGuide.txt", "OK")


def install_preventive_only(sink: Callable[[str], None] | None = None) -> None:
    """Install every durable preventive patch; no runtime cleanup / no ISO."""
    init_logging(sink)
    if not is_admin():
        raise PermissionError("Administrator required")
    from .preventive import install_all_preventive_patches

    inv = install_all_preventive_patches()
    write_migration_report(
        extra={
            "Result": "PREVENTIVE_PACK_INSTALLED",
            "PreventiveOk": inv.get("CountOk"),
            "PreventiveFail": inv.get("CountFail"),
        }
    )
    log("All preventive patches installed persistently on this PC.", "OK")


def _prefetch_chain_isos(
    steps: list[ChainStep],
    report,
    win10_iso: str | None,
    win11_iso: str | None,
) -> dict[str, str]:
    """Download/reuse every ISO the chain will need before the first Setup (intelligent)."""
    cached: dict[str, str] = {}
    for step in steps:
        if step.kind != "iso_upgrade":
            continue
        win = step.win or "11"
        arch = step.arch or "x64"
        key = f"{win}:{arch}"
        if key in cached:
            continue
        log(f"Prefetch ISO for chain step [{step.label}] ({win}/{arch})...", "STEP")
        if win == "10":
            iso = Path(win10_iso) if win10_iso else get_iso("10", report.locale, arch=arch)
        else:
            iso = Path(win11_iso) if win11_iso else get_iso("11", report.locale, arch="x64")
        cached[key] = str(iso)
        log(f"ISO ready: {iso}", "OK")
    return cached


def run_pipeline(
    sink: Callable[[str], None] | None = None,
    *,
    quiet: bool = True,
    skip_mbr: bool = False,
    skip_intermediate: bool = False,
    win10_iso: str | None = None,
    win11_iso: str | None = None,
    resume: bool = False,
) -> int:
    """
    ONE-CLICK intelligent full migration:

      1) Diagnose + plan
      2) Install ALL preventive patches (persistent)
      3) Flyby11/FlyOOBE compat bypass engine
      4) Runtime remediations + enrich + SupportGuide
      5) Prefetch all needed ISOs
      6) Execute version chain (SRP / MBR / hybrid / mount ISO / Setup)
      7) Auto-reboot + RunOnce until Done

    Quiet Setup by default. No extra clicks.
    """
    init_logging(sink)
    start_session("oneclick")
    log("=" * 60, "STEP")
    log("ONE-CLICK INTELLIGENT MIGRATION — full pipeline", "STEP")
    log("=" * 60, "STEP")

    try:
        if not is_admin():
            raise PermissionError('Administrator required for upgrade pipeline')

        # ---- Phase 1: Diagnose ----
        # AV / Kaspersky cloud trust runs in CI/CD Release only (not in One-Click).
        set_phase("diag", "Collecting system report…")
        set_step(percent=15, detail="Hardware / disk / boot mode…", indeterminate=True)
        log(">>> PHASE 1/7 — Auto-diagnose", "STEP")
        r = collect_report()
        set_step(percent=55, detail="Building upgrade plan…", indeterminate=True)
        print_report(r)
        plan = build_plan(r)
        print_plan(plan)
        set_step(percent=80, detail="Building version chain…", indeterminate=True)
        steps = build_version_chain(r)
        if skip_mbr:
            steps = [s for s in steps if s.id != 'mbr2gpt']
        if skip_intermediate:
            steps = [s for s in steps if s.id != 'win10_22h2']
        log('=== Intermediate version chain ===', 'STEP')
        log(format_chain(steps), 'OK')
        try:
            out = STATE_DIR / 'last-diagnose.json'
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {
                        'report': r.as_dict(),
                        'plan': plan.as_dict(),
                        'chain': [s.as_dict() for s in steps],
                        'chain_path': format_chain(steps),
                        'mode': 'ONECLICK',
                    },
                    indent=2,
                ),
                encoding='utf-8',
            )
            log(f'Diagnose saved: {out}', 'OK')
        except Exception as e:
            log(f'Diagnose save skip: {e}', 'WARN')

        if plan.target == 'blocked' and plan.blockers:
            for b in plan.blockers:
                log(f'Plan note: {b}', 'WARN')

        state = load_state()
        saved_index = int(state.get('ChainIndex', 0) or 0) if resume else 0
        if resume:
            log(f'Resuming chain after reboot (saved index={saved_index})', 'STEP')
            start_index = _next_pending_index(steps, saved_index, r)
        else:
            start_index = _next_pending_index(steps, 0, r)
        _persist_chain(steps, start_index)
        set_step(percent=100, detail=f"Plan ready — {format_chain(steps)}", indeterminate=False)

        # ---- Phase 2+3+4: Preventives + bypass + runtime (inside apply_migration_patches) ----
        set_phase("patch", "Preventives · Flyby11 bypass · runtime remediations…")
        set_step(percent=5, detail="Installing preventive patches…", indeterminate=True)
        log(">>> PHASE 2/7 — Install preventive patches (persistent)", "STEP")
        log(">>> PHASE 3/7 — Flyby11/FlyOOBE compat + HwReqChk bypass", "STEP")
        log(">>> PHASE 4/7 — Runtime remediations / enrich / SRP prep", "STEP")
        has_srp_step = any(s.kind == 'fix_srp' for s in steps)
        try:
            apply_migration_patches(
                autonomous=True,
                allow_auto_reboot=not resume,
                system_disk=getattr(r, 'disk_number', None),
                resume=resume,
                skip_srp=has_srp_step,
            )
        except AutonomousRebootRequired as ar:
            write_migration_report(
                extra={
                    'Result': 'AUTO_REBOOT',
                    'Reason': ar.reason,
                    'Chain': format_chain(steps),
                    'Mode': 'ONECLICK',
                }
            )
            log('Exiting for autonomous reboot; RunOnce continues One-Click.', 'OK')
            set_phase("setup", "Reboot scheduled — RunOnce will continue")
            set_step(percent=100, detail=ar.reason, indeterminate=False, eta_seconds=0)
            end_session(success=True)
            return 3010

        set_step(percent=100, detail="Patches + bypass complete", indeterminate=False)

        try:
            from .support import write_support_pack

            write_support_pack(
                extra={
                    'Mode': 'ONECLICK_FULL_MIGRATION',
                    'Target': plan.target,
                    'Chain': format_chain(steps),
                    'QuietSetup': quiet,
                    'Phases': 'diag→preventives→compat→runtime→prefetch→chain→setup',
                }
            )
        except Exception as e:
            log(f'Support pack: {e}', 'WARN')

        # Already latest: still applied bypasses above; exit cleanly
        if all(s.kind == 'done' for s in steps) or (
            plan.target == 'already_done' and start_index >= len(steps)
        ):
            log('Already on target OS class — preventives/compat applied. Nothing left to upgrade.', 'OK')
            save_state({'Phase': 'Done', 'ChainIndex': len(steps)})
            write_migration_report(
                extra={'Result': 'ALREADY_DONE', 'Chain': format_chain(steps), 'Mode': 'ONECLICK'}
            )
            end_session(success=True)
            return 0

        # ---- Phase 5: Prefetch ISOs ----
        set_phase("iso", "Searching / verifying / downloading Microsoft ISOs…")
        set_step(percent=2, detail="Prefetch ISOs for the chain…", indeterminate=True)
        log(">>> PHASE 5/7 — Prefetch / reuse Microsoft ISOs for the whole chain", "STEP")
        iso_cache: dict[str, str] = {}
        if not resume:
            try:
                iso_cache = _prefetch_chain_isos(steps[start_index:], r, win10_iso, win11_iso)
                set_step(percent=100, detail=f"{len(iso_cache)} ISO(s) ready", indeterminate=False)
            except Exception as e:
                log(f'ISO prefetch partial ({e}) — will download on demand per step', 'WARN')

        # ---- Phase 6+7: Execute chain ----
        set_phase("chain", "ESP / MBR / hybrid / mount / Setup…")
        log(">>> PHASE 6/7 — Execute migration chain (ESP/MBR/hybrid/ISO/Setup)", "STEP")
        log(">>> PHASE 7/7 — Quiet Setup + RunOnce auto-resume across reboots", "STEP")

        total = len(steps)
        i = start_index
        while i < total:
            step = steps[i]
            _persist_chain(steps, i)
            step_pct = (100.0 * i / max(total, 1))
            set_step(
                percent=step_pct,
                detail=f"Chain {i + 1}/{total}: {step.label}",
                indeterminate=True,
            )
            log(f"--- Chain {i + 1}/{total}: {step.label} ---", "STEP")

            remaining_after = steps[i + 1 :]
            needs_resume = step.kind in (
                'iso_upgrade',
                'mbr2gpt',
                'fix_srp',
                'hybrid_ia32',
                'fix_bootmgr',
            ) and any(
                s.kind in ('iso_upgrade', 'mbr2gpt', 'fix_srp', 'hybrid_ia32', 'fix_bootmgr')
                for s in remaining_after
            )
            if needs_resume or step.kind in ('iso_upgrade', 'mbr2gpt'):
                _runonce_register()

            # Prefer prefetched ISO paths
            w10 = win10_iso
            w11 = win11_iso
            if step.kind == 'iso_upgrade':
                key = f"{step.win or '11'}:{step.arch or 'x64'}"
                if key in iso_cache:
                    if (step.win or '11') == '10':
                        w10 = iso_cache[key]
                    else:
                        w11 = iso_cache[key]

            try:
                result = _execute_step(
                    step,
                    step_no=i + 1,
                    total=total,
                    report=r,
                    quiet=quiet,
                    win10_iso=w10,
                    win11_iso=w11,
                )
            except AutonomousRebootRequired as ar:
                write_migration_report(
                    extra={
                        'Result': 'AUTO_REBOOT',
                        'Reason': ar.reason,
                        'Step': step.label,
                        'Chain': format_chain(steps),
                        'ChainIndex': i + 1,
                        'Mode': 'ONECLICK',
                    }
                )
                save_state({'Phase': 'AutoReboot', 'ChainIndex': i + 1, 'Reason': ar.reason})
                log('Exiting for autonomous reboot; RunOnce continues One-Click.', 'OK')
                set_phase("setup", "Reboot scheduled — RunOnce continues")
                set_step(percent=100, detail=ar.reason, indeterminate=False, eta_seconds=0)
                end_session(success=True)
                return 3010

            if step.kind == 'iso_upgrade':
                code = int(result if result is not None else -1)
                if code not in SETUP_OK_CODES:
                    save_state(
                        {
                            'Phase': 'SetupFailed',
                            'ChainIndex': i,
                            'LastExitCode': code,
                            'LastStep': step.as_dict(),
                        }
                    )
                    _runonce_unregister()
                    write_migration_report(
                        extra={
                            'Result': 'SETUP_FAILED',
                            'ExitCode': code,
                            'Step': step.label,
                            'Chain': format_chain(steps),
                            'Mode': 'ONECLICK',
                        }
                    )
                    log(f'Setup failed (exit {code}) — chain index not advanced', 'ERROR')
                    end_session(success=False)
                    return code

                save_state(
                    {
                        'Phase': 'WaitingReboot',
                        'ChainIndex': i + 1,
                        'LastStep': step.as_dict(),
                        'LastExitCode': code,
                    }
                )
                log(
                    f'Setup launched ({step.label}). '
                    'PC will reboot; One-Click resumes automatically (RunOnce).',
                    'OK',
                )
                write_migration_report(
                    extra={
                        'Result': 'SETUP_LAUNCHED',
                        'ExitCode': code,
                        'Step': step.label,
                        'Chain': format_chain(steps),
                        'Autonomous': True,
                        'Mode': 'ONECLICK',
                    }
                )
                set_phase("setup", f"Windows Setup launched ({step.label})")
                set_step(percent=100, detail="Waiting for reboot…", indeterminate=False, eta_seconds=0)
                end_session(success=True)
                return code

            i += 1
            r = collect_report()
            i = _next_pending_index(steps, i, r)

        save_state({'Phase': 'Done', 'ChainIndex': total})
        log('ONE-CLICK migration chain completed.', 'OK')
        write_migration_report(
            extra={
                'Result': 'DONE',
                'Chain': format_chain(steps),
                'Target': plan.target,
                'Autonomous': True,
                'Mode': 'ONECLICK',
            }
        )
        end_session(success=True)
        return 0
    except UpgradeBlockedError as e:
        log(str(e), 'ERROR')
        try:
            write_migration_report(
                extra={
                    'Result': 'BLOCKED',
                    'Exception': str(e),
                    'Mode': 'ONECLICK',
                    'Kind': getattr(e, 'kind', ''),
                    'Issue': (getattr(e, 'links', None) or {}).get('issue'),
                }
            )
        except Exception:
            pass
        try:
            end_session(success=False)
        except Exception:
            pass
        remember_failure(str(e), kind=getattr(e, 'kind', ''), links=getattr(e, 'links', None))
        return EXIT_BLOCKED
    except Exception as e:
        log(str(e), 'ERROR')
        try:
            write_migration_report(extra={'Result': 'FAILED', 'Exception': str(e), 'Mode': 'ONECLICK'})
        except Exception:
            pass
        try:
            end_session(success=False)
        except Exception:
            pass
        # File GitHub autodiag if this failure was not already reported (ESP/SRP path embeds Issue:)
        msg = str(e)
        links: dict = {}
        if 'Issue:' not in msg and 'github.com/' not in msg.lower():
            links = _autodiag_links(kind='oneclick-failed', message=msg[:500], extra={'mode': 'ONECLICK'})
        full = msg + (_links_hint(links) if links else '')
        remember_failure(full or msg, kind='oneclick-failed', links=links)
        # Do NOT re-raise — frozen windowed EXE would show PyInstaller "Unhandled exception"
        return EXIT_FAILED
