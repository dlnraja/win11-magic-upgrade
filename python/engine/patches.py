"""Researched migration patches - stdlib / native exes only.

Sources: Microsoft upgrade resolution docs, SetupDiag rules, Sysnative,
ElevenForum, Microsoft Q&A, VeraCrypt upgrade notes, SuperUser WIM filters.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import winreg
from pathlib import Path

from .logutil import log

# Installed software that frequently causes SafeOS / 0xC1900101 rollbacks
BLOCKERS = [
    (r"Logitech Gaming Software", "Legacy LGS filters / 0xC1900101"),
    (r"Norton|McAfee|Avast|AVG|Kaspersky|Bitdefender|ESET|Sophos|Malwarebytes", "AV filters SafeOS"),
    (r"Acronis|Macrium|EaseUS Todo|AOMEI|Veeam Agent", "Disk/VSS filter drivers"),
    (r"SentinelOne|CrowdStrike|Carbon Black|Crowd Strike", "EDR leftover .sys"),
    (r"Daemon Tools|Alcohol 120%|PowerISO|Virtual CloneDrive", "Virtual CD filters"),
    (r"VeraCrypt|TrueCrypt|BitLocker To Go|Boxcryptor|Cloudfogger|Cryptomator", "Encryption filter - decrypt/uninstall before upgrade"),
    (r"NordVPN|ExpressVPN|Surfshark|ProtonVPN|OpenVPN|WireGuard|Cisco AnyConnect|GlobalProtect|FortiClient|Pulse Secure|Mullvad", "VPN TAP/WFP filters"),
    (r"VMware Tools|VirtualBox Guest|Hyper-V", "Virtualization guest filters (warn)"),
    (r"Intel.*Rapid Storage|IRST|AMD-RAID|Promise", "Storage RAID filter - update before upgrade"),
    (r"CrowdStrike|Falcon|Carbon Black|Cylance|Symantec Endpoint", "EDR kernel filters / 0xC1900101-0x20017"),
    (r"ShadowProtect|StorageCraft|DriveImage|Redo Backup", "Backup filter drivers"),
    (r"Docker Desktop|Oracle VM VirtualBox|VMware Workstation", "Virtualization / Hypervisor interference"),
]

# fltmc names commonly tied to WIM mount / SafeOS failures (forums)
BAD_FILTER_HINTS = re.compile(
    r"veracrypt|truecrypt|cbftlsfs|cloudfogger|boxcryptor|"
    r"acronis|mblctr|macrium|easeus|aomei|"
    r"asw|avg|avast|bdvedisk|klif|mfefire|symds|symefa|"
    r"vpn|tap0901|wintun|wireguard|openvpn|pangpd|fortimon|"
    r"dtsoft|sptd|alcohol|elbycdio",
    re.I,
)

ERROR_PATTERNS = (
    r"0xC1900101|0xC1900208|0xC1900200|0xC1900204|0xC190020E|"
    r"0xC1900107|0xC190012E|0xC1900216|0xC1900223|0x80070070|0x8007001F|"
    r"0x80070002|0x80070003|0x80070422|0x800F081F|0x800F0922|0x80070490|"
    r"0x800704DB|0xC1420121|0x80070057|0x80240034|0x80246007|"
    r"SECOND_BOOT|SAFE_OS|system reserved partition|partition reserv|"
    r"Failed to mount WIM|WIMMount|MOSETUP_E_ROLLBACK_PENDING|BlockMigration"
)


def _run(cmd: list[str], timeout: int = 180) -> str:
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
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
    except Exception as e:
        return str(e)


def _reg_exists(hive: int, path: str) -> bool:
    try:
        with winreg.OpenKey(hive, path):
            return True
    except OSError:
        return False


def _reg_get(hive: int, path: str, name: str):
    try:
        with winreg.OpenKey(hive, path) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return val
    except OSError:
        return None


def _installed_names() -> list[str]:
    names = []
    for hive_path in (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_path) as root:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            name, _ = winreg.QueryValueEx(k, "DisplayName")
                            names.append(str(name))
                    except OSError:
                        pass
        except OSError:
            pass
    return names


def scan_prior_setup_logs() -> None:
    for rel in (
        r"C:\$WINDOWS.~BT\Sources\Panther\setuperr.log",
        r"C:\$WINDOWS.~BT\Sources\Rollback\setuperr.log",
        r"C:\$WINDOWS.~BT\Sources\Panther\setupact.log",
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Panther", "setuperr.log"),
    ):
        p = Path(rel)
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[-250_000:]
            hits = sorted(set(re.findall(ERROR_PATTERNS, text, re.I)))
            if hits:
                log(f"Prior log hits in {p.name}: {', '.join(hits[:12])}", "WARN")
        except Exception:
            pass


def detect_software_blockers() -> None:
    names = _installed_names()
    for name in names:
        for pat, reason in BLOCKERS:
            if re.search(pat, name, re.I):
                log(f"Blocker software: {name} :: {reason}", "WARN")
                break
    maybe_uninstall_allowlist_blockers(names)


def maybe_uninstall_allowlist_blockers(installed_names: list[str] | None = None) -> None:
    """
    Optional: guidance for curated uninstall when MAGIC_UNINSTALL_ALLOWLIST=1.
    Never silent-force uninstall of AV/EDR — only legacy LGS / virtual CD toys.
    """
    if os.environ.get("MAGIC_UNINSTALL_ALLOWLIST", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return
    allow = [
        (r"Logitech Gaming Software", "legacy LGS"),
        (r"Daemon Tools Lite|Alcohol 120%", "virtual CD filter"),
    ]
    names = installed_names or _installed_names()
    for name in names:
        for pat, label in allow:
            if re.search(pat, name, re.I):
                log(
                    f"MAGIC_UNINSTALL_ALLOWLIST=1: uninstall recommended → {name} ({label}). "
                    "Settings → Apps, then reboot before One-Click.",
                    "WARN",
                )


def check_pending_reboot() -> bool:
    """0xC1900107 / MOSETUP_E_ROLLBACK_PENDING / stuck feature update."""
    pending = False
    checks = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Orchestrator\RebootRequired"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\PendingFileRenameOperations"),
    ]
    for hive, path in checks[:3]:
        if _reg_exists(hive, path):
            log(f"Pending reboot marker: {path}", "WARN")
            pending = True
    # PendingFileRenameOperations is a VALUE under Session Manager
    pfr = _reg_get(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager",
        "PendingFileRenameOperations",
    )
    if pfr:
        log("PendingFileRenameOperations present (reboot recommended before upgrade)", "WARN")
        pending = True
    return pending


def audit_filter_drivers() -> list[str]:
    """fltmc: third-party minifilters often break winre.wim mount (0xC1420121)."""
    out = _run(["fltmc", "filters"])
    if not out or "Error" in out[:40]:
        return []
    suspects = []
    for line in out.splitlines():
        parts = line.split()
        if not parts or parts[0].lower() in {"filter", "---", "filt"}:
            continue
        name = parts[0]
        if BAD_FILTER_HINTS.search(name):
            suspects.append(name)
    if suspects:
        log(
            "Risky file-system filters detected (auto-unload attempted): "
            + ", ".join(sorted(set(suspects))),
            "WARN",
        )
    else:
        log("fltmc: no well-known third-party filter names matched", "OK")
    return suspects


def repair_wimmount_service() -> None:
    """
    SetupDiag: WimMountFailure / WimMountDriverIssue.
    Forums (Sysnative): missing WIMMount service -> SafeOS cannot mount winre.wim.
    Only recreate if wimmount.sys exists and service is absent.
    """
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    sys_file = windir / "System32" / "drivers" / "wimmount.sys"
    q = _run(["sc", "query", "WIMMount"])
    if "SERVICE_NAME" in q.upper() or "RUNNING" in q.upper() or "STOPPED" in q.upper():
        log("WIMMount service present", "OK")
        return
    if not sys_file.exists():
        log("wimmount.sys missing - WIM mounts may fail (needs OS repair install)", "WARN")
        return
    log("WIMMount service missing but driver present - recreating service...", "WARN")
    # Official-style filesys driver service
    _run(
        [
            "sc",
            "create",
            "WIMMount",
            "type=",
            "filesys",
            "start=",
            "demand",
            "binPath=",
            r"System32\Drivers\wimmount.sys",
            "DisplayName=",
            "WIMMount",
        ]
    )
    # Also ensure ImagePath style some builds expect
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\WIMMount",
            0,
            winreg.KEY_ALL_ACCESS,
        )
        winreg.SetValueEx(
            key,
            "ImagePath",
            0,
            winreg.REG_EXPAND_SZ,
            r"\SystemRoot\System32\Drivers\wimmount.sys",
        )
        winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, 3)
        winreg.SetValueEx(key, "Type", 0, winreg.REG_DWORD, 2)
        winreg.CloseKey(key)
    except OSError as e:
        log(f"WIMMount registry tweak: {e}", "WARN")
    q2 = _run(["sc", "query", "WIMMount"])
    log(f"WIMMount after fix: {q2.splitlines()[0] if q2 else 'n/a'}", "OK" if "SERVICE" in q2.upper() or "STOPPED" in q2.upper() else "WARN")


def ensure_winre() -> None:
    """Disabled WinRE / tiny recovery partition blocks feature updates (forums)."""
    reagentc = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "reagentc.exe"
    if not reagentc.exists():
        return
    info = _run([str(reagentc), "/info"])
    log(f"WinRE: {(info.splitlines()[0] if info else 'n/a')[:120]}")
    if re.search(r"Windows RE status:\s*Disabled", info, re.I):
        log("WinRE disabled - attempting reagentc /enable...", "WARN")
        out = _run([str(reagentc), "/enable"])
        log(f"reagentc /enable -> {out[:200]}")
    if re.search(r"Windows RE status:\s*Enabled", info + "\n" + _run([str(reagentc), "/info"]), re.I):
        log("WinRE enabled", "OK")


def clear_veracrypt_setupconfig() -> None:
    """
    VeraCrypt leaves Users\\Default\\...\\WSUS\\SetupConfig.ini with ReflectDrivers=
    which breaks later upgrades even after decrypt (0xC190012E reports).
    """
    candidates = [
        Path(os.environ.get("SystemDrive", "C:"))
        / "Users"
        / "Default"
        / "AppData"
        / "Local"
        / "Microsoft"
        / "Windows"
        / "WSUS"
        / "SetupConfig.ini",
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "UpdatePreparation"
        / "SetupConfig.ini",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if re.search(r"ReflectDrivers|VeraCrypt|TrueCrypt|PostOOBE", text, re.I) or text.strip() == "[SetupConfig]":
            bak = p.with_suffix(".ini.bak-magic")
            try:
                if bak.exists():
                    bak.unlink()
                p.rename(bak)
                log(f"Renamed leftover SetupConfig.ini (encryption/WSUS) -> {bak.name}", "WARN")
            except Exception as e:
                log(f"Could not rename {p}: {e}", "WARN")


def stop_risky_services() -> None:
    out = _run(["sc", "query", "type=", "service", "state=", "all"])
    for svc in re.findall(r"SERVICE_NAME:\s+(\S+)", out):
        if re.search(
            r"norton|mcafee|avast|avg|kaspersky|bitdefender|eset|sophos|malwarebytes|"
            r"acronis|macrium|veracrypt|truecrypt|nordvpn|openvpn|wireguard|anyconnect|"
            r"fortishield|forticlient|globalprotect|expressvpn|surfshark",
            svc,
            re.I,
        ):
            log(f"Stopping service {svc}", "WARN")
            _run(["sc", "stop", svc])


def clear_upgrade_leftovers(*, force: bool = False) -> None:
    """
    Remove stale upgrade staging folders.
    NEVER delete $WINDOWS.~BT while Setup/resume is in progress.
    """
    from .logutil import load_state

    state = load_state()
    phase = str(state.get("Phase") or "")
    if not force and phase in ("SetupRunning", "WaitingReboot", "AutoReboot", "PendingRebootCycle"):
        log(f"Skip clearing upgrade leftovers (phase={phase})", "OK")
        return
    bt = Path(r"C:\$WINDOWS.~BT")
    if not force and bt.exists():
        # Live Setup often holds files under ~BT — only clear clearly stale leftovers
        act = bt / "Sources" / "Panther" / "setupact.log"
        try:
            if act.exists():
                import time

                age_h = (time.time() - act.stat().st_mtime) / 3600
                if age_h < 6:
                    log("Skip deleting $WINDOWS.~BT (recent setupact — Setup may be active)", "WARN")
                    return
        except Exception:
            pass

    for junk in (
        r"C:\$WINDOWS.~BT",
        r"C:\$Windows.~WS",
        r"C:\$GetCurrent",
        r"C:\ESD\Windows",
        r"C:\Windows\Panther\UnattendGC",
    ):
        if Path(junk).exists():
            log(f"Removing leftover {junk}", "WARN")
            _run(["cmd", "/c", f'rmdir /s /q "{junk}"'], timeout=300)


def free_space_helpers() -> float:
    _run(["powercfg", "/hibernate", "off"])
    # Prefer system-managed pagefile (setup can fail with tiny/no pagefile on low RAM)
    try:
        from .wmi_compat import set_automatic_managed_pagefile

        set_automatic_managed_pagefile()
    except Exception:
        pass
    temp = Path(os.environ.get("TEMP", "."))
    for child in list(temp.glob("*"))[:800]:
        try:
            if child.is_file():
                child.unlink(missing_ok=True)
        except Exception:
            pass
    # Soft cleanup of Windows Temp
    win_temp = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"
    if win_temp.is_dir():
        for child in list(win_temp.glob("*"))[:400]:
            try:
                if child.is_file():
                    child.unlink(missing_ok=True)
            except Exception:
                pass
    free = shutil.disk_usage(os.environ.get("SystemDrive", "C:\\")).free / (1024**3)
    log(f"Free space now ~{free:.1f} GB", "OK" if free >= 15 else "WARN")
    return free


def clear_appraiser_cache() -> None:
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    appraiser = windir / "appcompat" / "appraiser"
    if appraiser.exists():
        for f in appraiser.rglob("*"):
            if f.suffix.lower() in {".xml", ".sdb", ".cab"}:
                try:
                    f.unlink()
                except Exception:
                    pass
        log("Cleared appraiser cache", "OK")
    # Soft MoSetup telemetry leftovers
    for p in (
        windir / "Logs" / "MoSetup",
        windir / "Logs" / "WindowsUpdate",
    ):
        if p.is_dir():
            for f in list(p.glob("*.log"))[:50]:
                try:
                    f.unlink()
                except Exception:
                    pass


def check_problem_devices() -> None:
    """0x80070490 / driver install failures - warn on Device Manager problems."""
    # Windows 10 2004+ pnputil
    out = _run(["pnputil", "/enum-devices", "/problem"])
    if not out or "Failed" in out[:30]:
        try:
            from .wmi_compat import problem_pnp_entities_text

            out = problem_pnp_entities_text()
        except Exception:
            out = ""
    bad_lines = [
        ln.strip()
        for ln in out.splitlines()
        if ln.strip()
        and not re.match(r"^(Name|ConfigManager|Instance|Extension|Class|Manufacturer|Status|Problem)", ln, re.I)
        and "No devices" not in ln
    ]
    if bad_lines and len(bad_lines) > 1:
        preview = "; ".join(bad_lines[:6])
        log(f"Problem devices detected (disconnect/update drivers): {preview[:300]}", "WARN")
    else:
        log("No obvious Device Manager problem devices", "OK")


def warn_removable_disks() -> list[str]:
    """Microsoft: disconnect non-essential USB storage during upgrade."""
    try:
        from .wmi_compat import removable_logicaldisks_text

        out = removable_logicaldisks_text()
    except Exception:
        out = ""
    letters = re.findall(r"([A-Z]:)", out)
    if letters:
        log(
            "Removable drives present — will auto-dismount: " + ", ".join(letters),
            "WARN",
        )
    return letters


def quick_component_health() -> bool:
    """0x800F081F - CheckHealth; True if repairable corruption detected."""
    dism = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Dism.exe"
    if not dism.exists():
        return False
    out = _run([str(dism), "/Online", "/Cleanup-Image", "/CheckHealth"])
    if re.search(r"repairable|corrupt", out, re.I):
        log("DISM CheckHealth: repairable corruption — auto RestoreHealth", "WARN")
        return True
    if re.search(r"No component store corruption|healthy", out, re.I):
        log("DISM CheckHealth: OK", "OK")
    else:
        log(f"DISM CheckHealth: {out[-180:]}", "INFO")
    return False


def suspend_bitlocker_if_needed() -> None:
    try:
        from .mbrgpt import suspend_bitlocker

        suspend_bitlocker()
    except Exception as e:
        log(f"BitLocker suspend skipped: {e}", "WARN")


class AutonomousRebootRequired(Exception):
    """Raised when prep needs a reboot; RunOnce will resume One-Click."""

    def __init__(self, reason: str = "prep"):
        self.reason = reason
        super().__init__(reason)


def apply_migration_patches(
    *,
    install_preventive: bool = True,
    autonomous: bool = True,
    allow_auto_reboot: bool = False,
    system_disk: int | None = None,
    resume: bool = False,
    skip_srp: bool = False,
) -> None:
    log("=== Migration patches (runtime remediation) ===", "STEP")

    # Always install durable preventives first, then runtime remediations
    if install_preventive:
        try:
            from .preventive import install_all_preventive_patches

            install_all_preventive_patches()
        except Exception as e:
            log(f"Preventive pack install skipped: {e}", "WARN")

    scan_prior_setup_logs()
    detect_software_blockers()
    pending = check_pending_reboot()
    warn_removable_disks()
    check_problem_devices()
    audit_filter_drivers()

    if autonomous and not resume:
        try:
            from .autonomy import apply_autonomous_remediations

            apply_autonomous_remediations(system_disk=system_disk)
        except Exception as e:
            log(f"Autonomous remediations partial: {e}", "WARN")

    repair_wimmount_service()
    ensure_winre()
    clear_veracrypt_setupconfig()

    log("Disconnecting mapped network drives...", "STEP")
    _run(["net", "use", "*", "/delete", "/y"])

    if not resume:
        stop_risky_services()
    suspend_bitlocker_if_needed()
    clear_upgrade_leftovers(force=False)
    free_space_helpers()
    clear_appraiser_cache()
    needs_heal = quick_component_health()
    if needs_heal and not resume:
        try:
            from .enrich import dism_component_cleanup_and_heal

            log("Auto deep heal (DISM RestoreHealth + SFC)...", "STEP")
            dism_component_cleanup_and_heal()
        except Exception as e:
            log(f"Auto deep heal skipped: {e}", "WARN")

    try:
        from .errfix import apply_extra_error_fixes

        apply_extra_error_fixes(soft_wu_reset=not resume)
    except Exception as e:
        log(f"Extra error fixes skipped: {e}", "WARN")

    try:
        from .enrich import apply_forum_enrichments

        apply_forum_enrichments(deep_heal=False)
    except Exception as e:
        log(f"Forum enrichment skipped: {e}", "WARN")

    free = shutil.disk_usage(os.environ.get("SystemDrive", "C:\\")).free / (1024**3)
    if free < 12:
        raise RuntimeError(f"Not enough free disk space ({free:.1f} GB). Need ~20 GB.")

    # SRP: skip in prep when chain will run fix_srp (avoid double shrink)
    if not skip_srp:
        try:
            from .sysreserved import inspect_and_fix_system_reserved, scan_logs_for_srp_error

            force = scan_logs_for_srp_error() and not resume
            srp = inspect_and_fix_system_reserved(force_expand=force, system_disk=system_disk)
            if isinstance(srp, dict) and srp.get("ok") is False:
                log("SRP/ESP fix reported failure — retrying with force expand", "WARN")
                srp = inspect_and_fix_system_reserved(force_expand=True, system_disk=system_disk)
        except Exception as e:
            log(f"System Reserved / EFI fix skipped: {e}", "WARN")

    # Align Boot Manager bitness when OS is x64 but ESP still has IA32 boot files
    try:
        import platform

        from .bootmgr import apply_smart_boot_strategy

        os_arch = "x64" if platform.machine().endswith("64") else "x86"
        is_uefi = False
        try:
            import ctypes

            ft = ctypes.c_uint(0)
            if ctypes.windll.kernel32.GetFirmwareType(ctypes.byref(ft)):
                is_uefi = ft.value == 2
        except Exception:
            pass
        apply_smart_boot_strategy(os_arch=os_arch, is_uefi=is_uefi)
    except Exception as e:
        log(f"Boot Manager smart fix skipped: {e}", "WARN")

    if pending and allow_auto_reboot:
        from .logutil import load_state, save_state
        from .autonomy import schedule_reboot

        st = load_state()
        if st.get("PendingRebootHandled"):
            log(
                "Pending reboot marker still set after prior auto-reboot — continue without looping",
                "WARN",
            )
        else:
            save_state({"PendingRebootHandled": True, "Phase": "PendingRebootCycle"})
            log("Pending reboot detected — scheduling autonomous reboot then resume", "STEP")
            schedule_reboot(seconds=40, reason="Win11MagicUpgrade pending reboot prep")
            raise AutonomousRebootRequired("pending_reboot")
    elif not pending:
        try:
            from .logutil import save_state

            save_state({"PendingRebootHandled": False})
        except Exception:
            pass

    log("Migration patches done.", "OK")
