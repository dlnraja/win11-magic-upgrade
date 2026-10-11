"""
Autonomous boot guard — never leave the PC needing a manual Live CD.

Before Setup / any risky reboot:
  1) Snapshot ESP (Microsoft + Linux GRUB/shim EFI trees) + BCD + partition backup
  2) Enable WinRE, recoveryenabled, bootstatuspolicy
  3) Install ONSTART watchdog (SYSTEM) that heals boot after logon/resume
  4) If deep verify is weak, arm one-shot WinRE/WinPE ramdisk automatically

After resume / on failure:
  5) Deep verify → intelligent repair → restore GRUB EFI files → bcdboot
  6) Re-verify; only then allow another reboot

Disable: MAGIC_SKIP_BOOT_GUARD=1
Force WinRE next boot: MAGIC_WINRE_BOOT=1
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, load_state, log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WATCHDOG_TASK = "Win11MagicUpgradeBootWatchdog"
LINUX_EFI_VENDORS = (
    "ubuntu",
    "debian",
    "fedora",
    "centos",
    "rhel",
    "arch",
    "manjaro",
    "opensuse",
    "suse",
    "gentoo",
    "kali",
    "pop",
    "elementary",
    "linuxmint",
    "neon",
    "grub",
    "systemd",
    "refind",
)


def _env_skip() -> bool:
    return os.environ.get("MAGIC_SKIP_BOOT_GUARD", "").strip().lower() in ("1", "true", "yes")


def _run(cmd: list[str], *, timeout: int = 180) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except OSError as e:
        return 1, str(e)


def _letter_root(mounted: str) -> Path:
    L = mounted.rstrip(":\\").rstrip(":")
    return Path(f"{L}:\\")


def snapshot_linux_efi_trees() -> dict[str, Any]:
    """Copy EFI\\<linux-vendor>\\* into boot-snapshots (GRUB/shim preserve)."""
    from .sysreserved import mount_esp, unmount_letter

    out: dict[str, Any] = {"ok": False, "vendors": [], "files": 0}
    if os.environ.get("MAGIC_GRUB_PRESERVE", "1").strip().lower() in ("0", "false", "no"):
        out["skipped"] = True
        return out
    snap_root = STATE_DIR / "boot-snapshots" / "linux-efi"
    snap_root.mkdir(parents=True, exist_ok=True)
    mounted = None
    try:
        mounted = mount_esp()
        if not mounted:
            return out
        efi = _letter_root(mounted) / "EFI"
        if not efi.is_dir():
            return out
        n = 0
        vendors: list[str] = []
        for child in efi.iterdir():
            if not child.is_dir():
                continue
            name = child.name.lower()
            if name in ("microsoft", "boot"):
                continue
            if name not in LINUX_EFI_VENDORS and "grub" not in name and "shim" not in name:
                # still copy unknown non-Microsoft EFI vendors (safe — restore only)
                if name in ("dell", "hp", "lenovo", "asus", "oem"):
                    continue
            dest = snap_root / child.name
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            try:
                shutil.copytree(child, dest, dirs_exist_ok=True)
                count = sum(1 for _ in dest.rglob("*") if _.is_file())
                n += count
                vendors.append(child.name)
            except OSError as e:
                log(f"Linux EFI snapshot {child.name}: {e}", "WARN")
        out["ok"] = n > 0
        out["vendors"] = vendors
        out["files"] = n
        (snap_root / "manifest.json").write_text(
            json.dumps(
                {
                    "utc": datetime.now(timezone.utc).isoformat(),
                    "vendors": vendors,
                    "files": n,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if n:
            log(f"Linux/GRUB EFI snapshot: {n} files from {vendors}", "OK")
    finally:
        if mounted:
            try:
                unmount_letter(mounted)
            except Exception:
                pass
    return out


def restore_linux_efi_trees() -> dict[str, Any]:
    """Restore snapshotted GRUB/shim trees onto ESP after Windows bcdboot."""
    from .sysreserved import mount_esp, unmount_letter

    out: dict[str, Any] = {"ok": False, "restored": 0, "vendors": []}
    snap_root = STATE_DIR / "boot-snapshots" / "linux-efi"
    if not snap_root.is_dir():
        return out
    if os.environ.get("MAGIC_GRUB_PRESERVE", "1").strip().lower() in ("0", "false", "no"):
        out["skipped"] = True
        return out
    mounted = None
    try:
        mounted = mount_esp()
        if not mounted:
            return out
        efi = _letter_root(mounted) / "EFI"
        efi.mkdir(parents=True, exist_ok=True)
        n = 0
        vendors: list[str] = []
        for child in snap_root.iterdir():
            if not child.is_dir() or child.name.lower() == "manifest.json":
                continue
            dest = efi / child.name
            try:
                dest.mkdir(parents=True, exist_ok=True)
                for f in child.rglob("*"):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(child)
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copy2(f, target)
                        n += 1
                vendors.append(child.name)
            except OSError as e:
                log(f"Linux EFI restore {child.name}: {e}", "WARN")
        out["ok"] = n > 0 or bool(vendors)
        out["restored"] = n
        out["vendors"] = vendors
        if vendors:
            log(f"Restored Linux/GRUB EFI trees: {vendors} ({n} new files)", "OK")
            # Prefer Windows Boot Manager first, keep Linux entries present on disk
            _run(["bcdedit", "/set", "{fwbootmgr}", "displayorder", "{bootmgr}", "/addfirst"])
    finally:
        if mounted:
            try:
                unmount_letter(mounted)
            except Exception:
                pass
    return out


def write_autoheal_scripts() -> dict[str, str]:
    """Scripts used by WinRE / ONSTART watchdog (no Live CD)."""
    paths: dict[str, str] = {}
    rem = STATE_DIR / "remediation"
    rem.mkdir(parents=True, exist_ok=True)
    auto = rem / "AutoHeal-Boot.cmd"
    auto.write_text(
        "\r\n".join(
            [
                "@echo off",
                "REM Win11 Magic Upgrade — autonomous boot heal (WinRE or elevated Windows)",
                "set SYS=%SystemRoot%",
                "if not defined SYS set SYS=C:\\Windows",
                "echo === AutoHeal Boot ===",
                "reagentc /enable >nul 2>&1",
                "bcdedit /set {current} recoveryenabled Yes >nul 2>&1",
                "bcdedit /set {current} bootstatuspolicy IgnoreAllFailures >nul 2>&1",
                "bcdboot %SYS% /f UEFI",
                "if errorlevel 1 bcdboot %SYS% /f ALL",
                "bootrec /fixboot",
                "bootrec /rebuildbcd",
                "echo Done AutoHeal.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths["autoheal"] = str(auto)

    # Also copy into Windows Recovery folder when possible (WinRE can find it)
    try:
        recovery = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Recovery"
        recovery.mkdir(parents=True, exist_ok=True)
        dest = recovery / "Win11MagicUpgrade-AutoHeal.cmd"
        shutil.copy2(auto, dest)
        paths["recovery_autoheal"] = str(dest)
    except OSError:
        pass

    # Desktop
    try:
        desk = Path.home() / "Desktop" / "Win11MagicUpgrade-AutoHeal-Boot.cmd"
        shutil.copy2(auto, desk)
        paths["desktop"] = str(desk)
    except OSError:
        pass
    return paths


def install_boot_watchdog() -> bool:
    """SYSTEM ONSTART task: run boot heal after every boot while migration active."""
    if getattr(__import__("sys"), "frozen", False):
        exe = __import__("sys").executable
        cmdline = f'"{exe}" --cli --boot-heal'
    else:
        py = __import__("sys").executable
        script = Path(__file__).resolve().parents[1] / "magic_upgrade.py"
        cmdline = f'"{py}" "{script}" --cli --boot-heal'

    # schtasks ONSTART /RU SYSTEM — runs before user logon when possible
    _run(["schtasks", "/Delete", "/TN", WATCHDOG_TASK, "/F"], timeout=30)
    code, out = _run(
        [
            "schtasks",
            "/Create",
            "/TN",
            WATCHDOG_TASK,
            "/TR",
            cmdline,
            "/SC",
            "ONSTART",
            "/RU",
            "SYSTEM",
            "/RL",
            "HIGHEST",
            "/F",
        ],
        timeout=60,
    )
    if code == 0:
        log(f"Boot watchdog installed ({WATCHDOG_TASK}) — heals EFI/BCD/GRUB after reboot", "OK")
        save_state({"BootWatchdog": True})
        return True
    log(f"Boot watchdog install: {out[:200]}", "WARN")
    return False


def clear_boot_watchdog() -> None:
    _run(["schtasks", "/Delete", "/TN", WATCHDOG_TASK, "/F"], timeout=30)
    save_state({"BootWatchdog": False})


def arm_boot_safety_net(*, before_setup: bool = False) -> dict[str, Any]:
    """
    Call before Setup / MBR / ESP edits / any scheduled reboot.
    Snapshots + WinRE enable + watchdog + optional WinRE one-shot if weak.
    """
    result: dict[str, Any] = {"ok": False, "actions": []}
    if _env_skip():
        result["skipped"] = True
        return result

    log("=== BOOT GUARD — arm safety net (no Live CD needed) ===", "STEP")

    # Snapshots
    try:
        from .boot_safe import backup_bcd, snapshot_esp_critical_files

        backup_bcd()
        snapshot_esp_critical_files()
        result["actions"].append("bcd_esp_snapshot")
    except Exception as e:
        result["actions"].append(f"snapshot_warn:{e}")
        log(f"Boot snapshot: {e}", "WARN")

    linux = snapshot_linux_efi_trees()
    result["linux_efi"] = linux
    if linux.get("ok"):
        result["actions"].append("linux_efi_snapshot")

    try:
        from .boot_partition_backup import backup_boot_partitions

        backup_boot_partitions()
        result["actions"].append("partition_backup")
    except Exception as e:
        result["actions"].append(f"part_backup_warn:{type(e).__name__}")

    scripts = write_autoheal_scripts()
    result["scripts"] = scripts
    result["actions"].append("autoheal_scripts")

    # WinRE + recovery policy
    try:
        from .boot_safe import ensure_winre_enabled

        wr = ensure_winre_enabled()
        result["winre"] = wr
        if wr.get("enabled"):
            result["actions"].append("winre_enabled")
    except Exception as e:
        result["actions"].append(f"winre_warn:{e}")

    _run(["bcdedit", "/set", "{current}", "recoveryenabled", "Yes"])
    _run(["bcdedit", "/set", "{current}", "bootstatuspolicy", "IgnoreAllFailures"])
    result["actions"].append("recovery_policy")

    if install_boot_watchdog():
        result["actions"].append("watchdog")

    # Deep verify — if weak before Setup, pre-heal and optionally arm WinRE
    try:
        from .boot_emergency import deep_boot_verification
        from .boot_safe import guarantee_bootable, stage_temporary_winre_ramdisk_boot

        deep = deep_boot_verification()
        result["deep"] = {
            "ok": deep.get("ok"),
            "score": deep.get("score"),
            "max": deep.get("max_score"),
            "issues": deep.get("issues"),
        }
        score = int(deep.get("score") or 0)
        max_s = int(deep.get("max_score") or 1)
        weak = (not deep.get("ok")) or (max_s > 0 and score / max_s < 0.75)
        if weak or before_setup:
            gu = guarantee_bootable(force_restore=weak)
            result["guarantee"] = {
                "bootable": gu.get("bootable"),
                "safe_reboot_path": gu.get("safe_reboot_path"),
            }
            result["actions"].append("pre_heal")
            # Restore GRUB after any heal/bcdboot
            restore_linux_efi_trees()
            result["actions"].append("grub_restore_attempt")
            if weak and not gu.get("bootable"):
                wr = stage_temporary_winre_ramdisk_boot(one_shot=True)
                result["winre_oneshot"] = wr
                if wr.get("staged"):
                    result["actions"].append("winre_oneshot_armed")
                    log(
                        "Boot weak — next reboot will enter WinRE ramdisk once (auto), then Windows",
                        "WARN",
                    )
    except Exception as e:
        result["actions"].append(f"deep_warn:{type(e).__name__}")
        log(f"Boot guard deep verify: {e}", "WARN")

    result["ok"] = True
    save_state({"BootGuardArmed": True, "BootGuardActions": result["actions"][-20:]})
    log(f"Boot safety net armed ({len(result['actions'])} actions)", "OK")
    return result


def verify_and_heal_boot(*, force: bool = False) -> dict[str, Any]:
    """
    Autonomous heal after reboot / Setup failure / watchdog.
    Never requires Live CD; stages WinRE/WinPE if Windows boot still broken.
    """
    result: dict[str, Any] = {"ok": False, "healed": False, "bootable": False}
    if _env_skip() and not force:
        result["skipped"] = True
        return result

    log("=== BOOT GUARD — verify & heal (autonomous) ===", "STEP")
    write_autoheal_scripts()

    try:
        from .boot_emergency import deep_boot_verification
        from .boot_safe import guarantee_bootable

        deep = deep_boot_verification()
        result["deep_before"] = {
            "ok": deep.get("ok"),
            "score": deep.get("score"),
            "max": deep.get("max_score"),
            "issues": deep.get("issues"),
        }
        need = force or (not deep.get("ok")) or bool(deep.get("issues"))
        # Always restore GRUB trees (cheap) after Windows updates may have touched ESP
        grub = restore_linux_efi_trees()
        result["grub"] = grub

        if need:
            gu = guarantee_bootable(force_restore=True)
            result["guarantee"] = {
                "bootable": gu.get("bootable"),
                "safe_reboot_path": gu.get("safe_reboot_path"),
                "winre": (gu.get("winre_stage") or {}).get("staged"),
                "winpe": (gu.get("winpe_stage") or {}).get("staged"),
            }
            result["healed"] = True
            # After repair, put Linux EFI back again (bcdboot may have run)
            restore_linux_efi_trees()
        else:
            # Light heal: bcdboot refresh + recovery flags
            from .boot_safe import rewrite_boot_files_from_windows, heal_bcd_store

            rewrite_boot_files_from_windows()
            heal_bcd_store()
            restore_linux_efi_trees()
            gu = guarantee_bootable(force_restore=False)
            result["guarantee"] = {"bootable": gu.get("bootable"), "safe_reboot_path": gu.get("safe_reboot_path")}

        deep2 = deep_boot_verification()
        result["deep_after"] = {
            "ok": deep2.get("ok"),
            "score": deep2.get("score"),
            "max": deep2.get("max_score"),
        }
        result["bootable"] = bool(
            deep2.get("ok")
            or (result.get("guarantee") or {}).get("bootable")
            or (result.get("guarantee") or {}).get("safe_reboot_path")
        )
        result["ok"] = result["bootable"]
    except Exception as e:
        log(f"Boot guard heal error: {e}", "ERROR")
        result["error"] = str(e)
        try:
            from .boot_safe import stage_temporary_winre_ramdisk_boot

            wr = stage_temporary_winre_ramdisk_boot(one_shot=True)
            result["winre_fallback"] = wr
            result["ok"] = bool(wr.get("staged"))
        except Exception:
            pass

    save_state({"BootGuardHeal": result})
    log(
        f"Boot guard heal done: ok={result.get('ok')} bootable={result.get('bootable')} "
        f"healed={result.get('healed')}",
        "OK" if result.get("ok") else "WARN",
    )
    return result


def disarm_boot_safety_net() -> None:
    """Call when migration Phase=Done — keep scripts, remove ONSTART watchdog."""
    clear_boot_watchdog()
    save_state({"BootGuardArmed": False})
    log("Boot watchdog cleared (migration done); AutoHeal scripts kept", "OK")
