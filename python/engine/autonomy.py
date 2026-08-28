"""Maximal autonomy helpers — auto-remediate instead of asking the user.

No PowerShell. Native tools + registry only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .logutil import STATE_DIR, load_state, log, save_state


def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except Exception as e:
        return 1, str(e)


def _resume_command_line() -> str:
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return f'"{exe}" --cli --boot-resume'
    script = Path(__file__).resolve().parents[1] / "magic_upgrade.py"
    return f'"{exe}" "{script}" --cli --boot-resume'


def register_runonce_resume() -> None:
    runonce = _resume_command_line().replace("--boot-resume", "--resume")
    _run(
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
        ]
    )
    log("RunOnce registered for autonomous resume after reboot", "OK")


def _reg_set_migration_active(active: bool) -> None:
    val = "1" if active else "0"
    _run(
        [
            "reg",
            "add",
            r"HKLM\SOFTWARE\Win11MagicUpgrade",
            "/v",
            "MigrationActive",
            "/t",
            "REG_SZ",
            "/d",
            val,
            "/f",
        ]
    )


def register_scheduled_task_resume() -> None:
    """Logon scheduled task — retries One-Click until Phase=Done (RunOnce fallback)."""
    task = "Win11MagicUpgradeResume"
    tr = _resume_command_line()
    _run(["schtasks", "/Delete", "/TN", task, "/F"])
    code, out = _run(
        [
            "schtasks",
            "/Create",
            "/TN",
            task,
            "/TR",
            tr,
            "/SC",
            "ONLOGON",
            "/RL",
            "HIGHEST",
            "/F",
        ],
        timeout=60,
    )
    if code == 0:
        log(f"Scheduled task {task} registered (every logon until migration Done)", "OK")
    else:
        log(f"schtasks create skipped ({out[:160]})", "INFO")


def clear_boot_persistence() -> None:
    """Remove RunOnce + logon task when migration finished or aborted."""
    _run(
        [
            "reg",
            "delete",
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
            "/v",
            "Win11MagicUpgrade",
            "/f",
        ]
    )
    _run(["schtasks", "/Delete", "/TN", "Win11MagicUpgradeResume", "/F"])
    _reg_set_migration_active(False)
    save_state({"MigrationActive": False})
    log("Boot persistence cleared (RunOnce + scheduled task)", "OK")


def register_boot_persistence() -> None:
    """RunOnce (next boot) + logon task (fallback) while migration is active."""
    register_runonce_resume()
    register_scheduled_task_resume()
    _reg_set_migration_active(True)
    save_state({"MigrationActive": True, "Phase": load_state().get("Phase") or "Active"})


def migration_in_progress(state: dict | None = None) -> bool:
    st = state if state is not None else load_state()
    phase = str(st.get("Phase") or "").strip()
    if phase == "Done":
        return False
    if st.get("MigrationActive") in (True, "1", 1):
        return True
    active_phases = {
        "WaitingReboot",
        "AutoReboot",
        "AutoRebootScheduled",
        "SetupRunning",
        "PendingRebootCycle",
        "Active",
        "SetupFailed",
    }
    if phase in active_phases:
        return True
    try:
        idx = int(st.get("ChainIndex") or 0)
        chain = st.get("Chain") or []
        if idx > 0 and isinstance(chain, list) and idx < len(chain):
            return True
    except (TypeError, ValueError):
        pass
    return False


def should_auto_resume_on_startup() -> bool:
    return migration_in_progress(load_state())


def schedule_reboot(seconds: int = 45, reason: str = "Win11 Magic Upgrade prep") -> None:
    """Schedule reboot; RunOnce must already be registered."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    save_state({"Phase": "AutoRebootScheduled", "Reason": reason})
    register_boot_persistence()
    # /t delay, /c comment — no interactive prompt
    code, out = _run(
        [
            "shutdown",
            "/r",
            "/t",
            str(max(15, int(seconds))),
            "/c",
            reason[:512],
            "/f",
        ]
    )
    if code == 0:
        log(f"Autonomous reboot in ~{seconds}s ({reason}). Chain resumes via RunOnce.", "OK")
    else:
        log(f"shutdown schedule failed ({out}) — reboot manually; RunOnce is set", "WARN")


def unload_risky_filters(names: list[str]) -> None:
    for name in names:
        code, out = _run(["fltmc", "unload", name])
        if code == 0 or "unloaded" in out.lower():
            log(f"Unloaded filter {name}", "OK")
        else:
            log(f"Could not unload filter {name}: {out[:120]}", "INFO")


def disable_problem_pnp_instances() -> int:
    """Best-effort disable devices with Config Manager problems."""
    code, out = _run(["pnputil", "/enum-devices", "/problem"])
    if code != 0 or not out:
        return 0
    # Instance ID lines often look like: Instance ID: PCI\VEN_...
    ids = re.findall(r"(?im)^\s*Instance ID:\s*(.+)$", out)
    if not ids:
        ids = re.findall(r"(PCI\\VEN_[^\s]+|USB\\VID_[^\s]+|SCSI\\[^\s]+)", out, re.I)
    disabled = 0
    for inst in ids[:12]:
        inst = inst.strip()
        if not inst or inst.lower() in {"instance id:", "n/a"}:
            continue
        c, o = _run(["pnputil", "/disable-device", inst])
        if c == 0 or "disabled" in o.lower():
            log(f"Disabled problem device: {inst[:80]}", "OK")
            disabled += 1
        else:
            log(f"Could not disable {inst[:60]}: {o[:100]}", "INFO")
    return disabled


def dismount_removable_volumes() -> list[str]:
    """Dismount USB/SD volumes so Setup does not scan them."""
    try:
        from .wmi_compat import removable_logicaldisks_text

        out = removable_logicaldisks_text()
    except Exception:
        out = ""
    letters = re.findall(r"([A-Z]:)", out or "")
    done: list[str] = []
    for letter in letters:
        # mountvol X: /p dismounts and takes offline
        c, o = _run(["mountvol", letter, "/p"])
        if c == 0:
            log(f"Dismounted removable volume {letter}", "OK")
            done.append(letter)
        else:
            # fallback: lock via diskpart offline? skip if fail
            log(f"Could not dismount {letter}: {o[:100]}", "INFO")
    return done


def offline_secondary_fixed_disks(system_disk: int | None = None) -> int:
    """
    Offline non-system fixed disks to avoid 0x80070002-0x20009 style Setup confusion.
    Safety: refuse if system_disk unknown / negative; never offline the boot/system disk.
    Uses verified diskpart select (EN/FR) — never invent disk 0.
    """
    from .diskpart_safe import NO_DISK_RE, ensure_select_disk, run_diskpart

    if system_disk is None or int(system_disk) < 0:
        log("system_disk unknown — skipping secondary disk offline (safety)", "WARN")
        return 0

    ok_sys, _ = ensure_select_disk(int(system_disk))
    if not ok_sys:
        log(f"Cannot verify system disk #{system_disk} — skip offline others", "WARN")
        return 0

    _, out = run_diskpart("list disk\nexit\n")
    nums = [int(x) for x in re.findall(r"Disk\s+(\d+)", out or "", re.I)]
    offlined = 0
    for n in sorted(set(nums)):
        if n == int(system_disk):
            continue
        ok_sel, detail = ensure_select_disk(n)
        if not ok_sel:
            log(f"Skip disk {n}: cannot select", "INFO")
            continue
        if re.search(
            r"\b(Boot|System|Pagefile|Hibernation|Crashdump|D[eé]marrage|Syst[eè]me)\b",
            detail or "",
            re.I,
        ):
            log(f"Keep disk {n} online (system-related volume)", "INFO")
            continue
        if re.search(r"Status\s*:\s*Offline|Hors connexion", detail or "", re.I):
            continue
        c_ok, o = run_diskpart(f"select disk {n}\ndetail disk\noffline disk\nexit\n")
        if c_ok and not NO_DISK_RE.search(o or "") and not re.search(
            r"error|failed|échec|echec|erreur",
            o or "",
            re.I,
        ):
            log(f"Offlined secondary disk {n} for Setup autonomy", "OK")
            offlined += 1
        else:
            log(f"Could not offline disk {n}: {(o or '')[:120]}", "INFO")
    return offlined


def apply_autonomous_remediations(*, system_disk: int | None = None) -> dict:
    """Run best-effort auto fixes that previously were warn-only."""
    log("=== Autonomous remediations (no user prompts) ===", "STEP")
    summary = {
        "filters_unloaded": 0,
        "devices_disabled": 0,
        "removable_dismounted": [],
        "disks_offlined": 0,
    }

    # Risky filters: try unload
    code, flt = _run(["fltmc", "filters"])
    suspects: list[str] = []
    if flt:
        bad = re.compile(
            r"veracrypt|truecrypt|cbftlsfs|acronis|macrium|easeus|aomei|"
            r"asw|avg|avast|bdvedisk|klif|mfefire|vpn|tap0901|wintun|"
            r"wireguard|openvpn|dtsoft|sptd|alcohol|elbycdio",
            re.I,
        )
        for line in flt.splitlines():
            parts = line.split()
            if parts and bad.search(parts[0]):
                suspects.append(parts[0])
    if suspects:
        unload_risky_filters(sorted(set(suspects)))
        summary["filters_unloaded"] = len(set(suspects))

    summary["devices_disabled"] = disable_problem_pnp_instances()
    summary["removable_dismounted"] = dismount_removable_volumes()
    try:
        summary["disks_offlined"] = offline_secondary_fixed_disks(system_disk)
    except Exception as e:
        log(f"Secondary disk offline skipped: {e}", "WARN")

    log(
        f"Autonomy summary: filters={summary['filters_unloaded']} "
        f"devices={summary['devices_disabled']} "
        f"removable={summary['removable_dismounted']} "
        f"disks_off={summary['disks_offlined']}",
        "OK",
    )
    return summary
