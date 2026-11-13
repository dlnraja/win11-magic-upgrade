"""RAM-loaded rescue plan. The signed Windows RE image stays the default.

The repair script is written next to that image, on the ESP or the data
partition. It is not copied into Winre.wim unless MAGIC_WINRE_INJECT=1.
A Linux ramdisk is planned only when MAGIC_LINUX_RESCUE=1 and Secure Boot is off.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .logutil import log
from .post_op_verify import regen_is_exhausted

RESCUE_MAX_ARMS = 2
_LEDGER = "rescue-arms.json"


def linux_rescue_opt_in() -> bool:
    return os.environ.get("MAGIC_LINUX_RESCUE", "").strip().lower() in ("1", "true", "yes")


def winre_inject_opt_in() -> bool:
    return os.environ.get("MAGIC_WINRE_INJECT", "").strip().lower() in ("1", "true", "yes")


def rescue_script_text() -> str:
    """Commands WinRE can run. The same bounds as the Windows app are named here."""
    lines = [
        "@echo off",
        "REM Signed WinRE is unchanged. This script lives on the ESP.",
        "set LOG=X:\\EFI\\MagicUpgrade\\rescue.log",
        "echo rescue-start>>%LOG%",
        "echo classifier=error_catalog>>%LOG%",
        "echo strategies=bcdboot-uefi,bcdboot-bios,bcdboot-all,bcdedit-import,bootsect-nt60-mbr>>%LOG%",
        "echo post-op-verify-and-rollback>>%LOG%",
        "echo bounds=one-strategy-once,max-4,budget-180s,fingerprint,persisted-counter>>%LOG%",
        "if exist C:\\Windows\\System32\\bcdboot.exe (",
        "  C:\\Windows\\System32\\bcdboot.exe C:\\Windows /s S: /f UEFI /l en-us>>%LOG% 2>&1",
        ")",
        "echo rescue-end>>%LOG%",
    ]
    return "\r\n".join(lines) + "\r\n"


def winpeshl_text() -> str:
    return "\r\n".join(
        [
            "[LaunchApps]",
            "%SYSTEMDRIVE%\\EFI\\MagicUpgrade\\rescue.cmd",
            "",
        ]
    )


def linux_rescue_plan(*, opt_in: bool, secure_boot: str) -> dict[str, Any]:
    """Optional GPL/MIT ramdisk. Never armed while Secure Boot is on."""
    if not opt_in:
        return {"enabled": False, "reason": "set MAGIC_LINUX_RESCUE=1 to plan a Linux ramdisk"}
    if (secure_boot or "").strip().lower() == "on":
        return {
            "enabled": False,
            "reason": "Secure Boot is on. A Linux ramdisk is not the signed Windows boot path.",
        }
    return {
        "enabled": True,
        "writes_disk": False,
        "license": "GPL-2.0-or-later or MIT",
        "chain": "existing OpenDuet or GRUB entry, ramdisk only",
        "paid_tools": False,
        "reason": "Plan only. No image is downloaded or written.",
    }


def bcd_rescue_commands(*, recovery_guid: str, one_shot: bool = True) -> list[list[str]]:
    """BCD changes that boot the existing WinRE ramdisk. The WIM path is not rewritten."""
    guid = recovery_guid
    commands = [
        ["bcdedit", "/set", "{ramdiskoptions}", "description", "Win11Magic Ramdisk Options"],
        ["bcdedit", "/set", "{current}", "recoveryenabled", "Yes"],
        ["bcdedit", "/set", "{current}", "recoverysequence", guid],
        ["bcdedit", "/set", "{current}", "bootstatuspolicy", "DisplayAllFailures"],
        ["bcdedit", "/displayorder", guid, "/addlast"],
    ]
    if one_shot:
        commands.append(["bcdedit", "/bootsequence", guid])
    return commands


def _load_arms(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"arms": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"arms": 0}
    return {"arms": int(data.get("arms") or 0)}


def _save_arms(path: Path, arms: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"arms": arms}), encoding="utf-8")


def plan_rescue_environment(
    *,
    esp_root: Path,
    recovery_guid: str = "{recovery}",
    secure_boot: str = "off",
    linux_opt_in: bool | None = None,
    inject: bool | None = None,
    ledger_path: Path | None = None,
    regen_ledger: Path | None = None,
) -> dict[str, Any]:
    """Stage the rescue files and the BCD plan. Does not call bcdedit."""
    root = Path(esp_root)
    arms_path = ledger_path or (root / "EFI" / "MagicUpgrade" / _LEDGER)
    log_path = root / "EFI" / "MagicUpgrade" / "rescue.log"
    opt_in = linux_rescue_opt_in() if linux_opt_in is None else linux_opt_in
    do_inject = winre_inject_opt_in() if inject is None else inject
    if regen_is_exhausted(regen_ledger):
        _append_log(log_path, "rescue refused: regeneration budget exhausted")
        return {"armed": False, "verdict": "NO-GO", "reason": "regen-exhausted", "signed_wim_preserved": True}
    state = _load_arms(arms_path)
    if int(state["arms"]) >= RESCUE_MAX_ARMS:
        _append_log(log_path, "rescue refused: arm count exhausted")
        return {"armed": False, "verdict": "NO-GO", "reason": "rescue-arms-exhausted", "signed_wim_preserved": True}
    state["arms"] = int(state["arms"]) + 1
    _save_arms(arms_path, state["arms"])
    script_dir = root / "EFI" / "MagicUpgrade"
    script_dir.mkdir(parents=True, exist_ok=True)
    script = script_dir / "rescue.cmd"
    script.write_bytes(rescue_script_text().encode("ascii"))
    peshl = script_dir / "winpeshl.ini"
    peshl.write_bytes(winpeshl_text().encode("ascii"))
    _append_log(log_path, f"rescue armed {state['arms']}/{RESCUE_MAX_ARMS} signed-wim inject={do_inject}")
    linux = linux_rescue_plan(opt_in=opt_in, secure_boot=secure_boot)
    return {
        "armed": True,
        "verdict": "GO",
        "arms": state["arms"],
        "signed_wim_preserved": not do_inject,
        "inject_requested": do_inject,
        "script": str(script),
        "winpeshl": str(peshl),
        "log": str(log_path),
        "bcd": bcd_rescue_commands(recovery_guid=recovery_guid, one_shot=True),
        "linux": linux,
        "reason": "signed WinRE ramdisk; script staged beside the image",
    }


def _append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    log(line, "INFO")
