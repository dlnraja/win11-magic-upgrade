"""Forum-driven enrichment & remediation beyond core patches.

Sources: ElevenForum, Microsoft Q&A, SetupDiag discussions (2024-2026):
  - EspPaddingPercent registry for SRP/ESP space shocks
  - Extra / Server language packs causing 0x800f0805 / CBS invalid package
  - System Restore point before risky upgrades
  - DISM StartComponentCleanup + conditional RestoreHealth
  - .NET 3.5 feature enable (some legacy apps / setup paths)
  - SysMain / Print Spooler churn during SafeOS
  - Storage Spaces / Hyper-V soft warnings
"""
from __future__ import annotations

import os
import re
import subprocess
import winreg
from pathlib import Path

from .logutil import log


def _run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
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


def set_esp_padding_workaround() -> None:
    """
    Microsoft Q&A / 24H2-25H2: EspPaddingPercent=0 reduces EFI padding requirement
    when SRP/ESP is tight (complements real cleanup/enlarge).
    """
    path = r"SYSTEM\CurrentControlSet\Control\Bfsvc"
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_ALL_ACCESS)
        winreg.SetValueEx(key, "EspPaddingPercent", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        log("Registry EspPaddingPercent=0 (ESP padding workaround for SRP update error)", "OK")
    except OSError as e:
        log(f"EspPaddingPercent skip: {e}", "WARN")


def create_system_restore_point(description: str = "Win11 Magic Upgrade prep") -> None:
    """Best-effort restore point (forum recommendation before feature upgrades)."""
    log("Creating System Restore point (best effort)...", "STEP")
    # Enable System Restore on C: if disabled
    _run(
        [
            "reg",
            "add",
            r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore",
            "/v",
            "RPSessionInterval",
            "/t",
            "REG_DWORD",
            "/d",
            "1",
            "/f",
        ]
    )
    code, out = _run(
        [
            "wmic.exe",
            "/Namespace:\\\\root\\default",
            "Path",
            "SystemRestore",
            "Call",
            "CreateRestorePoint",
            description,
            "100",
            "7",
        ],
        timeout=180,
    )
    if code == 0 and ("ReturnValue = 0" in out or "ReturnValue=0" in out.replace(" ", "")):
        log("System Restore point created", "OK")
    else:
        log(f"Restore point skipped/unavailable: {out[:180]}", "WARN")


def audit_and_trim_language_packs(*, remove_orphan_server_lp: bool = True) -> None:
    """
    ElevenForum: leftover Server/extra language packs -> 0x800f0805 / CBS_E_INVALID_PACKAGE.
    Warn on extras; optionally remove Microsoft-Windows-Server-LanguagePack not matching UI lang.
    """
    log("Auditing installed language / CBS packages...", "STEP")
    code, out = _run(["dism", "/online", "/get-packages", "/format:table"], timeout=300)
    if code != 0 and not out:
        log("DISM get-packages failed", "WARN")
        return

    ui_lang = (
        os.environ.get("LANG")
        or ""
    )
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Nls\Language",
        ) as k:
            ui_lang = str(winreg.QueryValueEx(k, "InstallLanguage")[0])
    except OSError:
        pass
    # Prefer locale name
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as k:
            ui_lang = str(winreg.QueryValueEx(k, "LocaleName")[0])
    except OSError:
        pass
    ui_lang = (ui_lang or "en-US").replace("_", "-")
    ui_short = ui_lang.split("-")[0].lower()

    server_lps = re.findall(
        r"(Microsoft-Windows-Server-LanguagePack[~0-9a-zA-Z\-_.]+)",
        out,
    )
    # Also client LPs that are clearly listed
    all_lps = re.findall(
        r"(Microsoft-Windows-(?:Server-)?LanguagePack[~0-9a-zA-Z\-_.]+)",
        out,
    )
    extras = []
    for pkg in sorted(set(all_lps)):
        # language token near end: ~~ko-KR or ~amd64~ko-KR~
        m = re.search(r"([a-z]{2}-[A-Z]{2})", pkg)
        if not m:
            continue
        lang = m.group(1)
        if lang.split("-")[0].lower() != ui_short and "Server-LanguagePack" in pkg:
            extras.append(pkg)

    if not extras and not server_lps:
        log(f"Language packs look aligned with UI ({ui_lang})", "OK")
        return

    for pkg in extras[:12]:
        log(f"Extra/orphan language package: {pkg}", "WARN")
        dry = os.environ.get("MAGIC_LP_DRY_RUN", "").strip().lower() in ("1", "true", "yes")
        if dry:
            log(f"DRY-RUN (MAGIC_LP_DRY_RUN=1): would remove {pkg}", "INFO")
            continue
        if remove_orphan_server_lp and "Server-LanguagePack" in pkg:
            log(f"Removing orphan Server LP (forum fix 0x800f0805): {pkg}", "WARN")
            c2, o2 = _run(
                ["dism", "/online", "/remove-package", f"/packagename:{pkg}", "/norestart"],
                timeout=900,
            )
            log(f"remove-package -> {c2}: {o2[-200:]}")


def dism_component_cleanup_and_heal() -> None:
    """CBS corruption remediation path used on ElevenForum for 24H2 breakage."""
    log("DISM StartComponentCleanup (enrichment)...", "STEP")
    code, out = _run(
        ["dism", "/online", "/cleanup-image", "/startcomponentcleanup"],
        timeout=900,
    )
    log(f"StartComponentCleanup -> {code}: {out[-180:]}")

    code2, out2 = _run(
        ["dism", "/online", "/cleanup-image", "/checkhealth"],
        timeout=180,
    )
    if re.search(r"repairable|corrupt", out2, re.I):
        log("Component store repairable - running RestoreHealth (may take long)...", "WARN")
        code3, out3 = _run(
            ["dism", "/online", "/cleanup-image", "/restorehealth"],
            timeout=3600,
        )
        log(f"RestoreHealth -> {code3}: {out3[-220:]}")
        _run(["sfc", "/scannow"], timeout=3600)
    else:
        log("DISM CheckHealth after cleanup: OK", "OK")


def enable_netfx3_best_effort() -> None:
    """Some setups/apps expect NetFx3; enable without local source if possible."""
    log("Ensuring .NET Framework 3.5 feature (best effort)...", "STEP")
    code, out = _run(
        [
            "dism",
            "/online",
            "/enable-feature",
            "/featurename:NetFx3",
            "/all",
            "/norestart",
        ],
        timeout=600,
    )
    if code == 0 or "already" in out.lower() or "enabled" in out.lower():
        log(".NET 3.5 OK / already enabled", "OK")
    else:
        log(f".NET 3.5 enable skipped: {out[-160:]}", "INFO")


def quiet_background_churn() -> None:
    """Reduce SysMain / Spooler interference during SafeOS (forum tips)."""
    for svc in ("SysMain", "Spooler", "WSearch"):
        q = _run(["sc", "query", svc])[1]
        if "RUNNING" in q.upper():
            log(f"Temporarily stopping {svc} for upgrade stability", "INFO")
            _run(["sc", "stop", svc])


def warn_storage_spaces_and_hyperv() -> None:
    spaceport = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "spaceport.sys"
    if spaceport.exists():
        q = _run(["sc", "query", "Spaceport"])[1]
        if "RUNNING" in q.upper():
            log("Storage Spaces stack present - ensure pools healthy before upgrade", "WARN")
    q2 = _run(["sc", "query", "vmms"])[1]
    if "RUNNING" in q2.upper():
        log("Hyper-V VMMS running - pause VMs before feature upgrade if issues occur", "WARN")


def apply_forum_enrichments(*, deep_heal: bool = False) -> None:
    log("=== Forum enrichment & intelligent remediation ===", "STEP")
    set_esp_padding_workaround()
    create_system_restore_point()
    audit_and_trim_language_packs(remove_orphan_server_lp=True)
    quiet_background_churn()
    warn_storage_spaces_and_hyperv()
    enable_netfx3_best_effort()
    if deep_heal:
        dism_component_cleanup_and_heal()
    else:
        # Light cleanup only
        _run(["dism", "/online", "/cleanup-image", "/startcomponentcleanup"], timeout=600)
    log("Forum enrichment done.", "OK")
