"""
Boot remediation bot — EFI / MBR / intelligent repartition / failover ladder.

Ordered tiers (fail-over to next on soft failure; always try to keep Windows bootable):
  1) Snapshot BCD + ESP critical files
  2) Autonomous remediations (filters, secondary disks)
  3) ESP/SRP cleanup + intelligent partition grow/shrink (partition_smart)
  4) bcdboot / Boot Manager repair
  5) MBR→GPT (when MBR + mbr2gpt available) with restore guarantee
  6) Hybrid IA32 path when firmware is IA32 UEFI
  7) Stage GParted/WinRE rescue scripts + Desktop .cmd (never auto-boot GParted)

Also writes remediation scripts for WinRE / manual use under
%LOCALAPPDATA%\\Win11MagicUpgrade\\remediation\\ and Desktop copies.

GUI: « Boot Fix » / MAGIC_BOOT_AUTO_FIX=1
CLI: --boot-fix
Disable: MAGIC_SKIP_BOOT_REMEDIATE=1
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .logutil import STATE_DIR, log, save_state

REMEDIATE_DIR = STATE_DIR / "remediation"


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_skip() -> bool:
    return _env_true("MAGIC_SKIP_BOOT_REMEDIATE")


def write_remediation_scripts(
    *,
    system_disk: int | None = None,
    mode: str = "auto",
    summary: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    Emit .cmd / .txt scripts for WinRE and Desktop (human or bot-assisted).
    """
    REMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    disk = 0 if system_disk is None else int(system_disk)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    paths: dict[str, str] = {}

    # --- WinRE bcdboot heal ---
    bcd = REMEDIATE_DIR / "WinRE-bcdboot-heal.cmd"
    bcd.write_text(
        "\r\n".join(
            [
                "@echo off",
                "REM Win11 Magic Upgrade — run from WinRE / Command Prompt",
                "REM Assign a letter to Windows (usually C:) and ESP (e.g. S:)",
                "echo === bcdboot heal ===",
                "bcdboot C:\\Windows /s S: /f UEFI",
                "if errorlevel 1 bcdboot C:\\Windows /s S: /f ALL",
                "bootrec /fixboot",
                "bootrec /fixmbr",
                "bootrec /rebuildbcd",
                "echo Done. Remove USB and reboot.",
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths["winre_bcdboot"] = str(bcd)

    # --- diskpart: list + select system disk (read-only safe list) ---
    dp_list = REMEDIATE_DIR / "diskpart-list-system.txt"
    dp_list.write_text(
        f"list disk\r\nselect disk {disk}\r\nlist partition\r\nlist volume\r\nexit\r\n",
        encoding="utf-8",
    )
    paths["diskpart_list"] = str(dp_list)

    # --- diskpart: shrink C then create EFI (manual — review before run) ---
    dp_efi = REMEDIATE_DIR / "diskpart-create-efi-MANUAL-REVIEW.txt"
    dp_efi.write_text(
        "\r\n".join(
            [
                f"REM MANUAL REVIEW — shrink C: and create 512MB EFI on disk {disk}",
                f"REM Only if One-Click Boot Fix failed and you understand the risk.",
                f"select disk {disk}",
                "list partition",
                "select volume C",
                "shrink desired=512 minimum=300",
                "create partition efi size=512",
                "format quick fs=fat32 label=System",
                "assign letter=S",
                "exit",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths["diskpart_efi_manual"] = str(dp_efi)

    # --- diskpart: System Reserved grow hint (MBR) ---
    dp_srp = REMEDIATE_DIR / "diskpart-srp-extend-MANUAL-REVIEW.txt"
    dp_srp.write_text(
        "\r\n".join(
            [
                f"REM MANUAL REVIEW — extend System Reserved on disk {disk} if free space follows it",
                f"select disk {disk}",
                "list partition",
                "REM select partition N  (System Reserved number from list)",
                "REM extend size=300",
                "exit",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths["diskpart_srp_manual"] = str(dp_srp)

    # --- One-shot launcher for elevated tool ---
    launcher = REMEDIATE_DIR / "Run-BootFix-Elevated.cmd"
    exe_hint = "Win11MagicUpgrade.exe"
    launcher.write_text(
        "\r\n".join(
            [
                "@echo off",
                "REM Re-run Boot Fix (ESP/MBR/repartition failover) as Administrator",
                f"cd /d \"%~dp0..\"",
                f"if exist \"{exe_hint}\" (",
                f"  \"{exe_hint}\" --auto boot-fix",
                ") else (",
                "  python \"%~dp0..\\..\\python\\magic_upgrade.py\" --auto boot-fix 2>nul",
                "  if errorlevel 1 python -m magic_upgrade --auto boot-fix 2>nul",
                ")",
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths["launcher"] = str(launcher)

    # --- Human guide ---
    guide = REMEDIATE_DIR / f"BootRemediation-Guide-{stamp}.txt"
    lines = [
        "Win11 Magic Upgrade — Boot remediation guide",
        "=" * 48,
        f"Mode: {mode}",
        f"System disk #: {disk}",
        f"UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Automatic failover order:",
        "  1. BCD/ESP snapshot",
        "  2. Autonomy (filters / secondary disks)",
        "  3. ESP/SRP cleanup + smart grow/shrink",
        "  4. bcdboot / Boot Manager repair",
        "  5. MBR→GPT (if MBR) with boot restore",
        "  6. Hybrid IA32 (if IA32 UEFI)",
        "  7. Stage GParted/WinRE scripts (never auto-executed)",
        "",
        "Scripts in this folder:",
    ]
    for k, p in paths.items():
        lines.append(f"  - {k}: {p}")
    if summary:
        lines.append("")
        lines.append("Last run summary:")
        lines.append(json.dumps(summary, indent=2, default=str)[:4000])
    lines.append("")
    lines.append("WinRE: boot recovery media → Troubleshoot → Command Prompt → run WinRE-bcdboot-heal.cmd")
    guide.write_text("\n".join(lines), encoding="utf-8")
    paths["guide"] = str(guide)

    # Desktop copies (best-effort)
    desk = Path.home() / "Desktop"
    for name in ("WinRE-bcdboot-heal.cmd", "Run-BootFix-Elevated.cmd"):
        src = REMEDIATE_DIR / name
        if src.is_file():
            try:
                dest = desk / f"Win11MagicUpgrade-{name}"
                shutil.copy2(src, dest)
                paths[f"desktop_{name}"] = str(dest)
            except OSError:
                pass
    try:
        shutil.copy2(guide, desk / "Win11MagicUpgrade-BootRemediation.txt")
        paths["desktop_guide"] = str(desk / "Win11MagicUpgrade-BootRemediation.txt")
    except OSError:
        pass

    log(f"Remediation scripts written under {REMEDIATE_DIR}", "OK")
    return paths


def run_boot_auto_remediate(
    report: Any | None = None,
    *,
    force_esp: bool = True,
    try_mbr2gpt: bool = True,
    try_hybrid: bool = True,
    write_scripts: bool = True,
    sink: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    Full failover ladder. Safe: restores bootability between tiers.
    """
    out: dict[str, Any] = {
        "ok": False,
        "tiers": [],
        "bootable": False,
        "scripts": {},
        "skipped": False,
    }
    if _env_skip():
        log("MAGIC_SKIP_BOOT_REMEDIATE=1 — skip boot remediation bot", "INFO")
        out["skipped"] = True
        return out

    def _say(msg: str, level: str = "INFO") -> None:
        log(msg, level)
        if sink:
            try:
                sink(msg + "\n")
            except Exception:
                pass

    _say("=== BOOT REMEDIATION BOT (EFI / MBR / failover) ===", "STEP")

    # Snapshots GRUB/EFI + WinRE + ONSTART watchdog — no Live CD
    try:
        from .boot_guard import arm_boot_safety_net

        arm_boot_safety_net(before_setup=False)
        out["tiers"].append({"tier": "boot_guard_arm", "ok": True})
    except Exception as e:
        out["tiers"].append({"tier": "boot_guard_arm", "ok": False, "error": str(e)})

    disk = None
    is_mbr = False
    is_uefi = True
    ia32 = False
    mbr2gpt_ok = False
    if report is not None:
        disk = getattr(report, "disk_number", None)
        is_mbr = str(getattr(report, "partition_style", "")).upper() == "MBR"
        is_uefi = bool(getattr(report, "is_uefi", True))
        ia32 = bool(getattr(report, "firmware_likely_ia32", False)) or getattr(
            report, "boot_strategy", ""
        ) == "hybrid_ia32_csmwrap"
        mbr2gpt_ok = bool(getattr(report, "mbr2gpt_available", False))
    else:
        try:
            from .detect import collect_report

            report = collect_report()
            disk = getattr(report, "disk_number", None)
            is_mbr = str(getattr(report, "partition_style", "")).upper() == "MBR"
            is_uefi = bool(getattr(report, "is_uefi", True))
            ia32 = bool(getattr(report, "firmware_likely_ia32", False))
            mbr2gpt_ok = bool(getattr(report, "mbr2gpt_available", False))
        except Exception as e:
            _say(f"Detect for boot remediate: {e}", "WARN")

    if os.environ.get("MAGIC_BOOT_SCRIPTS", "1").strip().lower() in ("0", "false", "no", "off"):
        write_scripts = False

    # Tier 0 — scripts first so user always has a fallback even if we crash
    if write_scripts:
        try:
            out["scripts"] = write_remediation_scripts(
                system_disk=disk if isinstance(disk, int) else None,
                mode="pre-run",
            )
            out["tiers"].append({"tier": "scripts", "ok": True})
        except Exception as e:
            out["tiers"].append({"tier": "scripts", "ok": False, "error": str(e)})

    # Tier 1 — autonomy
    try:
        from .autonomy import apply_autonomous_remediations

        apply_autonomous_remediations(system_disk=disk if isinstance(disk, int) else None)
        out["tiers"].append({"tier": "autonomy", "ok": True})
        _say("Tier1 OK: autonomy remediations", "OK")
    except Exception as e:
        out["tiers"].append({"tier": "autonomy", "ok": False, "error": str(e)})
        _say(f"Tier1 autonomy: {e}", "WARN")

    # Tier 2 — ESP/SRP + intelligent repartition
    esp_ok = False
    if force_esp or _env_true("MAGIC_BOOT_AUTO_FIX"):
        try:
            from .boot_safe import run_esp_srp_with_restore
            from .sysreserved import scan_logs_for_srp_error

            force = bool(scan_logs_for_srp_error()) or force_esp
            rep = report.as_dict() if report is not None and hasattr(report, "as_dict") else None
            res = run_esp_srp_with_restore(
                force_expand=force,
                system_disk=disk if isinstance(disk, int) else None,
                retries=3,
                report=rep if isinstance(rep, dict) else None,
            )
            esp_ok = bool(isinstance(res, dict) and res.get("ok"))
            out["esp"] = res if isinstance(res, dict) else {"ok": False}
            out["bootable"] = bool((res or {}).get("bootable"))
            out["tiers"].append({"tier": "esp_srp_smart", "ok": esp_ok})
            _say(f"Tier2 ESP/SRP smart: ok={esp_ok}", "OK" if esp_ok else "WARN")
        except Exception as e:
            out["tiers"].append({"tier": "esp_srp_smart", "ok": False, "error": str(e)})
            _say(f"Tier2 ESP/SRP: {e}", "WARN")

    # Tier 3 — Boot Manager / bcdboot + restore GRUB/shim EFI trees
    try:
        from .boot_safe import validated_repair_boot_manager
        from .boot_guard import restore_linux_efi_trees, verify_and_heal_boot

        bm = validated_repair_boot_manager(prefer_uefi=is_uefi or not is_mbr)
        bm_ok = bool(bm.get("ok", True) if isinstance(bm, dict) else True)
        grub = restore_linux_efi_trees()
        heal = verify_and_heal_boot(force=not bm_ok)
        out["tiers"].append(
            {
                "tier": "bootmgr_bcdboot_grub",
                "ok": bm_ok or heal.get("ok"),
                "grub": grub.get("vendors"),
            }
        )
        _say(
            f"Tier3 bootmgr/bcdboot+GRUB: ok={bm_ok or heal.get('ok')} "
            f"grub={grub.get('vendors')}",
            "OK" if (bm_ok or heal.get("ok")) else "WARN",
        )
    except Exception as e:
        out["tiers"].append({"tier": "bootmgr_bcdboot_grub", "ok": False, "error": str(e)})
        _say(f"Tier3 bootmgr: {e}", "WARN")

    # Tier 4 — MBR→GPT failover (only when appropriate)
    if try_mbr2gpt and is_mbr and mbr2gpt_ok and isinstance(disk, int) and disk >= 0:
        if _env_true("MAGIC_SKIP_MBR2GPT"):
            out["tiers"].append({"tier": "mbr2gpt", "ok": False, "skipped": True})
        else:
            try:
                from .boot_safe import validated_mbr_to_gpt

                ok, code, msg, meta = validated_mbr_to_gpt(disk)
                out["mbr2gpt"] = {"ok": ok, "code": code, "msg": msg, "meta": meta}
                out["bootable"] = bool((meta.get("guarantee") or {}).get("bootable")) or out["bootable"]
                out["tiers"].append({"tier": "mbr2gpt", "ok": ok})
                _say(f"Tier4 MBR→GPT: ok={ok} ({msg})", "OK" if ok else "WARN")
                if ok:
                    esp_ok = True
            except Exception as e:
                out["tiers"].append({"tier": "mbr2gpt", "ok": False, "error": str(e)})
                _say(f"Tier4 MBR→GPT: {e}", "WARN")
    else:
        out["tiers"].append(
            {
                "tier": "mbr2gpt",
                "ok": None,
                "skipped": True,
                "reason": "not_mbr_or_unavailable",
            }
        )

    # Tier 5 — Hybrid IA32
    if try_hybrid and ia32:
        try:
            from .hybrid_uefi import apply_hybrid_ia32_path

            hy = apply_hybrid_ia32_path(activate=True, prepare_bios=True)
            hy_ok = bool(hy.get("ok"))
            out["tiers"].append({"tier": "hybrid_ia32", "ok": hy_ok})
            _say(f"Tier5 hybrid IA32: ok={hy_ok}", "OK" if hy_ok else "WARN")
        except Exception as e:
            out["tiers"].append({"tier": "hybrid_ia32", "ok": False, "error": str(e)})
            _say(f"Tier5 hybrid: {e}", "WARN")

    # Tier 6 — guarantee bootable + stage partition fallbacks if still bad
    try:
        from .boot_safe import guarantee_bootable, prepare_partition_fallbacks

        gu = guarantee_bootable(
            system_disk=disk if isinstance(disk, int) else None,
            expect_uefi=(not is_mbr) or is_uefi,
            force_restore=not esp_ok,
        )
        out["guarantee"] = gu
        out["bootable"] = bool(gu.get("bootable"))
        out["tiers"].append({"tier": "guarantee_bootable", "ok": out["bootable"]})
        if not esp_ok:
            fb = prepare_partition_fallbacks(
                reason="boot_remediation_incomplete",
                system_disk=disk if isinstance(disk, int) else None,
                mode="UEFI" if not is_mbr else "MBR",
            )
            out["fallback"] = fb
            out["tiers"].append({"tier": "stage_gparted_winre", "ok": True, "detail": fb})
            _say("Tier6 staged GParted/WinRE fallback (not auto-executed)", "WARN")
    except Exception as e:
        out["tiers"].append({"tier": "guarantee_bootable", "ok": False, "error": str(e)})
        _say(f"Tier6 guarantee: {e}", "WARN")

    # Refresh scripts with summary
    if write_scripts:
        try:
            out["scripts"] = write_remediation_scripts(
                system_disk=disk if isinstance(disk, int) else None,
                mode="post-run",
                summary={"tiers": out["tiers"], "bootable": out["bootable"]},
            )
        except Exception:
            pass

    # Success if ESP ok OR bootable after restore (PC usable even if expand failed)
    out["ok"] = bool(esp_ok or out.get("bootable"))
    save_state({"BootRemediation": {"ok": out["ok"], "tiers": out["tiers"], "bootable": out["bootable"]}})
    _say(
        f"Boot remediation done: ok={out['ok']} bootable={out['bootable']} "
        f"tiers={len(out['tiers'])}",
        "OK" if out["ok"] else "WARN",
    )
    return out
