"""Maximal autonomy helpers — auto-remediate instead of asking the user.

No PowerShell. Native tools + registry only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .logutil import STATE_DIR, log, save_state


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
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
    except Exception as e:
        return 1, str(e)


def register_runonce_resume() -> None:
    exe = sys.executable
    if getattr(sys, "frozen", False):
        runonce = f'"{exe}" --cli --resume'
    else:
        script = Path(__file__).resolve().parents[1] / "magic_upgrade.py"
        runonce = f'"{exe}" "{script}" --cli --resume'
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


def schedule_reboot(seconds: int = 45, reason: str = "Win11 Magic Upgrade prep") -> None:
    """Schedule reboot; RunOnce must already be registered."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    save_state({"Phase": "AutoRebootScheduled", "Reason": reason})
    register_runonce_resume()
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
    code, out = _run(
        ["wmic", "logicaldisk", "where", "DriveType=2", "get", "DeviceID"]
    )
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
    Safety: never offline disk 0 if system_disk unknown; never offline the boot disk.
    """
    if system_disk is None:
        system_disk = 0
    # List disks via diskpart
    script = "list disk\n"
    tmp = STATE_DIR / "list-disk.txt"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp.write_text(script, encoding="utf-8")
    code, out = _run(["diskpart", "/s", str(tmp)])
    nums = [int(x) for x in re.findall(r"Disk\s+(\d+)", out or "", re.I)]
    offlined = 0
    for n in sorted(set(nums)):
        if n == int(system_disk):
            continue
        body = f"select disk {n}\nonline disk\ndetail disk\n"
        # Only offline if Online and not containing system/boot (heuristic: skip if "Boot" or "System" in detail)
        detail_script = STATE_DIR / f"detail-disk-{n}.txt"
        detail_script.write_text(f"select disk {n}\ndetail disk\n", encoding="utf-8")
        _, detail = _run(["diskpart", "/s", str(detail_script)])
        if re.search(r"\b(Boot|System|Pagefile|Hibernation|Crashdump)\b", detail or "", re.I):
            log(f"Keep disk {n} online (system-related volume)", "INFO")
            continue
        if re.search(r"Status\s*:\s*Offline", detail or "", re.I):
            continue
        off_script = STATE_DIR / f"offline-disk-{n}.txt"
        off_script.write_text(f"select disk {n}\noffline disk\n", encoding="utf-8")
        c, o = _run(["diskpart", "/s", str(off_script)])
        if c == 0 and "error" not in (o or "").lower():
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
