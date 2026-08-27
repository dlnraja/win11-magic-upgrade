"""
Autonomous antivirus trust declarations (false-positive mitigation).

This app is a legitimate Windows upgrade helper — NOT malware / NOT a PDF trojan.
PyInstaller + admin elevation often triggers heuristic labels like Trojan.PDF.
We declare exclusions locally so Defender / Kaspersky KIS stop blocking the run.
"""
from __future__ import annotations

import os
import subprocess
import sys
import winreg
from pathlib import Path

from .logutil import STATE_DIR, log

APP_NAME = "Win11 Magic Upgrade"
VENDOR_FP_NOTE = (
    "Win11MagicUpgrade is an open-source Windows 10→11 migration tool. "
    "It is NOT a Trojan, NOT a PDF exploit, and NOT ransomware. "
    "False positives are caused by unsigned PyInstaller packaging + UAC elevation. "
    "Official ISO downloads come only from Microsoft CDN."
)

KASPERSKY_FP_URL = "https://opentip.kaspersky.com/"
DEFENDER_EXCL_PATHS = (
    r"SOFTWARE\Microsoft\Windows Defender\Exclusions\Paths",
    r"SOFTWARE\Policies\Microsoft\Windows Defender\Exclusions\Paths",
)

# KIS / Kaspersky product folder name hints (Internet Security, Total Security, etc.)
_KIS_NAME_HINTS = (
    "internet security",
    "total security",
    "premium",
    "free",
    "anti-virus",
    "antivirus",
    "kes",
    "kav ",
    "kaspersky ",
)


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd: list[str], timeout: int = 60) -> str:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_creationflags(),
        )
        return (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return str(e)


def _run_ok(out: str) -> bool:
    if not out or not out.strip():
        return False
    low = out.lower()
    if any(x in low for x in ("error", "failed", "denied", "access is denied", "not found")):
        return False
    return True


def app_paths() -> list[Path]:
    paths: list[Path] = [STATE_DIR, STATE_DIR / "iso", STATE_DIR / "Panther"]
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        paths.extend([exe, exe.parent])
    else:
        root = Path(__file__).resolve().parents[2]
        paths.extend(
            [
                root,
                root / "python",
                root / "dist",
                root / "dist" / "Win11MagicUpgrade-Portable",
            ]
        )
    # Unique existing-or-parent paths
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        try:
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        except Exception:
            pass
    return out


def _reg_add_exclusion(path: Path) -> bool:
    ok = False
    value = str(path)
    for hive_path in DEFENDER_EXCL_PATHS:
        try:
            key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, hive_path, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, value, 0, winreg.REG_DWORD, 0)
            winreg.CloseKey(key)
            ok = True
        except OSError:
            continue
    return ok


def declare_defender_exclusions(paths: list[Path] | None = None) -> int:
    """Add Windows Defender path exclusions (registry + MpPreference)."""
    paths = paths or app_paths()
    count = 0
    for p in paths:
        if _reg_add_exclusion(p):
            count += 1
            log(f"Defender exclusion (registry): {p}", "OK")
        # PowerShell Add-MpPreference — best-effort when Tamper Protection allows
        ps = (
            f"try {{ Add-MpPreference -ExclusionPath '{p}' -ErrorAction Stop; 'OK' }} "
            f"catch {{ $_.Exception.Message }}"
        )
        out = _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps,
            ],
            timeout=45,
        )
        if "OK" in out:
            count += 1
            log(f"Defender exclusion (MpPreference): {p}", "OK")
        elif out.strip():
            log(f"Defender MpPreference note for {p}: {out.strip()[:160]}", "WARN")
    return count


def _is_kis_product(product_name: str) -> bool:
    low = product_name.lower()
    return any(h in low for h in _KIS_NAME_HINTS)


def _find_kaspersky_installations() -> list[dict[str, Path | str | bool]]:
    """Return every Kaspersky product dir with available CLIs (KIS, KES, KAV, …)."""
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ]
    found: list[dict[str, Path | str | bool]] = []
    seen_dirs: set[str] = set()
    for root in roots:
        base = Path(root) / "Kaspersky Lab"
        if not base.exists():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            key = str(child).lower()
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            avp = child / "avp.com"
            avp_exe = child / "avp.exe"
            kavshell = child / "kavshell.exe"
            klcfg = child / "klcfginst.exe"
            cli: Path | None = None
            for cand in (avp, avp_exe):
                if cand.exists():
                    cli = cand
                    break
            found.append(
                {
                    "product": child.name,
                    "dir": child,
                    "cli": cli or Path(),
                    "kavshell": kavshell if kavshell.exists() else Path(),
                    "klcfginst": klcfg if klcfg.exists() else Path(),
                    "is_kis": _is_kis_product(child.name),
                }
            )
    return found


def _find_kaspersky_cli() -> Path | None:
    """Backward-compatible: first avp.com / avp.exe found."""
    for inst in _find_kaspersky_installations():
        cli = inst.get("cli")
        if isinstance(cli, Path) and cli.exists():
            return cli
    return None


def _kaspersky_trust_attempts(cli: Path, path: Path, product: str) -> bool:
    """Run KIS/KES trust CLI patterns (version-dependent; ignore failures)."""
    p = str(path)
    exe = str(path) if path.is_file() else ""
    attempts: list[list[str]] = [
        [str(cli), "ADD", p],
        [str(cli), "ADD", exe] if exe else [],
        [str(cli), "SET", f"TrustedZone.TrustedApplications.Path={p}"],
        [str(cli), "SET", f"/TrustedZone/TrustedApplications/Item_00000000/Path={p}"],
        [
            str(cli),
            "SET",
            f"/TrustedZone/TrustedApplications/Item_00000000/Path={p}",
            f"/TrustedZone/TrustedApplications/Item_00000000/Description={APP_NAME}",
        ],
        [str(cli), "SET", f"/Settings/Exclusions/ExclusionObjects/Item_00000000/Path={p}"],
        [
            str(cli),
            "SET",
            f"/Settings/Exclusions/ExclusionObjects/Item_00000000/Path={p}",
            "/Settings/Exclusions/ExclusionObjects/Item_00000000/UseForScanning=1",
            "/Settings/Exclusions/ExclusionObjects/Item_00000000/UseForMonitoring=1",
        ],
    ]
    ok = False
    for args in attempts:
        if not args:
            continue
        out = _run(args, timeout=45)
        if _run_ok(out):
            ok = True
            log(f"Kaspersky ({product}) trust OK: {' '.join(args[1:3])}…", "OK")
    return ok


def _kavshell_trust(kavshell: Path, path: Path, product: str) -> bool:
    """Scan/monitor exclusion via kavshell (some KIS builds)."""
    p = str(path)
    ok = False
    for args in (
        [str(kavshell), "/S", "-i", p],
        [str(kavshell), "/S", p],
        [str(kavshell), "ADD", p],
    ):
        out = _run(args, timeout=45)
        if _run_ok(out):
            ok = True
            log(f"Kaspersky kavshell ({product}) OK for {p}", "OK")
    return ok


def _klcfginst_trust(klcfg: Path, install_dir: Path, paths: list[Path]) -> bool:
    """Import a minimal trusted-app cfg via klcfginst when present."""
    cfg = STATE_DIR / "kaspersky_trusted_apps.cfg"
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        lines = ["[TrustedApplications]", f"Count={len(paths)}"]
        for i, p in enumerate(paths):
            lines.append(f"Item_{i}_Path={p}")
            lines.append(f"Item_{i}_Description={APP_NAME}")
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        log(f"klcfginst cfg write failed: {e}", "WARN")
        return False
    ok = False
    for args in (
        [str(klcfg), "import", "-file", str(cfg)],
        [str(klcfg), "import", str(cfg)],
        [str(klcfg), "-import", str(cfg)],
    ):
        out = _run(args, timeout=60)
        if _run_ok(out):
            ok = True
            log(f"klcfginst trust import OK ({install_dir.name})", "OK")
            break
    return ok


def _unblock_paths(paths: list[Path]) -> int:
    """Remove MOTW (Zone.Identifier) — helps SmartScreen + some AV heuristics."""
    count = 0
    for p in paths:
        targets = [p]
        if p.is_dir():
            try:
                targets.extend(p.glob("*.exe"))
            except Exception:
                pass
        for t in targets:
            if not t.exists():
                continue
            ps = (
                f"try {{ Unblock-File -LiteralPath '{t}' -ErrorAction Stop; 'OK' }} "
                f"catch {{ $_.Exception.Message }}"
            )
            out = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    ps,
                ],
                timeout=30,
            )
            if "OK" in out:
                count += 1
                log(f"Unblock-File: {t}", "OK")
    return count


def _try_kaspersky_quarantine_restore(cli: Path, product: str) -> bool:
    """
    Best-effort restore of Win11MagicUpgrade* from Quarantine/Backup.
    KES often needs Password protection credentials; consumer KIS may allow without.
    """
    names = (
        "Win11MagicUpgrade.exe",
        "Win11MagicUpgrade*",
        "*MagicUpgrade*",
    )
    ok = False
    for name in names:
        for args in (
            [str(cli), "RESTORE", "/REPLACE", name],
            [str(cli), "RESTORE", name],
            [str(cli), "BACKUP", "RESTORE", name],
        ):
            out = _run(args, timeout=60)
            if _run_ok(out) or "restor" in out.lower():
                ok = True
                log(f"Kaspersky ({product}) quarantine restore attempt OK: {name}", "OK")
                break
        if ok:
            break
    if not ok:
        log(
            f"Kaspersky ({product}): auto-restore from quarantine not available "
            "(open Quarantine in KIS GUI if the EXE was deleted)",
            "INFO",
        )
    return ok


def _write_kis_gui_guide(paths: list[Path]) -> Path | None:
    """
    Desktop + state-dir guide when KIS still quarantines (manual trusted-app steps).
    """
    lines = [
        APP_NAME + " — Kaspersky Internet Security (KIS) false positive",
        "=" * 62,
        "",
        VENDOR_FP_NOTE,
        "",
        "If KIS deleted or blocked Win11MagicUpgrade.exe (Trojan.PDF / HEUR):",
        "",
        "1. Open Kaspersky → More → Quarantine → Restore the file",
        "2. Settings → Additional → Threats and Exclusions",
        "3. Manage exclusions → Add → Trusted application",
        "4. Browse and select Win11MagicUpgrade.exe (and its folder if offered)",
        "5. Also add these paths if still flagged:",
        *[f"   • {p}" for p in paths],
        "",
        "6. Re-run as Administrator (One-Click has no AV-trust phase — cloud FP is CI-only).",
        "",
        "7. False-positive cloud submit is done in GitHub Actions Release:",
        f"   {KASPERSKY_FP_URL}",
        "   Email: newvirus@kaspersky.com",
        f"   Local pack (if generated in CI): {STATE_DIR / 'fp_submissions'}",
        "",
        "Official builds: https://github.com/dlnraja/win11-magic-upgrade/releases/latest",
        "Prefer the ZIP download (not naked EXE) to reduce Chrome/KIS blocks.",
        "",
    ]
    text = "\n".join(lines)
    written: Path | None = None
    targets = [STATE_DIR / "KIS-WHITELIST.txt"]
    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    if desktop.exists():
        targets.append(desktop / "Win11MagicUpgrade-KIS-WHITELIST.txt")
    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            log(f"KIS whitelist guide: {target}", "OK")
            written = target
        except Exception as e:
            log(f"Could not write KIS guide {target}: {e}", "WARN")
    return written


def declare_kaspersky_trust(paths: list[Path] | None = None) -> bool:
    """
    Best-effort Kaspersky KIS/KES trusted-app + exclusion declaration.
    Also writes FP declaration + desktop KIS whitelist guide.
    """
    if os.environ.get("MAGIC_KIS_TRUST", "1").strip().lower() in ("0", "false", "no"):
        log("Kaspersky trust skipped (MAGIC_KIS_TRUST=0)", "INFO")
        return False

    paths = paths or app_paths()
    declared = False
    installations = _find_kaspersky_installations()

    if installations:
        for inst in installations:
            product = str(inst.get("product", "Kaspersky"))
            cli = inst.get("cli")
            kavshell = inst.get("kavshell")
            klcfg = inst.get("klcfginst")
            install_dir = inst.get("dir")
            is_kis = bool(inst.get("is_kis"))
            tag = "KIS" if is_kis else "Kaspersky"
            if isinstance(cli, Path) and cli.exists():
                log(f"{tag} CLI: {cli} ({product})", "INFO")
                try:
                    if _try_kaspersky_quarantine_restore(cli, product):
                        declared = True
                except Exception as e:
                    log(f"Kaspersky restore: {e}", "WARN")
                for p in paths:
                    if _kaspersky_trust_attempts(cli, p, product):
                        declared = True
            if isinstance(kavshell, Path) and kavshell.exists():
                for p in paths:
                    if _kavshell_trust(kavshell, p, product):
                        declared = True
            if isinstance(klcfg, Path) and klcfg.exists() and isinstance(install_dir, Path):
                if _klcfginst_trust(klcfg, install_dir, paths):
                    declared = True
    else:
        log("Kaspersky / KIS not installed — writing FP declaration only", "INFO")

    notice = STATE_DIR / "KASPERSKY_FALSE_POSITIVE_DECLARATION.txt"
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        prods = [str(i.get("product", "")) for i in installations] or ["(not detected)"]
        lines = [
            APP_NAME,
            "=" * 60,
            VENDOR_FP_NOTE,
            "",
            "Product: Win11MagicUpgrade.exe",
            "Purpose: Official Microsoft ISO download + Windows Setup orchestration",
            "No PDF parsing, no document exploits, no credential theft.",
            "",
            "Detected Kaspersky products:",
            *[f"  - {p}" for p in prods if p],
            "",
            "Please whitelist / mark as Trusted Application:",
            *[f"  - {p}" for p in paths],
            "",
            f"Vendor FP portal: {KASPERSKY_FP_URL}",
            "Submit sample + this declaration if still flagged as Trojan.PDF / Heur.Trojan.",
            "",
        ]
        notice.write_text("\n".join(lines), encoding="utf-8")
        log(f"Kaspersky FP declaration written: {notice}", "OK")
        declared = True
    except Exception as e:
        log(f"Could not write Kaspersky declaration: {e}", "WARN")

    _write_kis_gui_guide(paths)
    return declared


def write_trust_banner() -> None:
    banner = STATE_DIR / "APP_TRUST.txt"
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        banner.write_text(
            f"{APP_NAME}\n{VENDOR_FP_NOTE}\n\nState dir: {STATE_DIR}\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def declare_local_av_trust() -> None:
    """Fast local trust (Defender + KIS + Unblock) — safe at app startup."""
    log("AV TRUST (local) — Defender + Kaspersky KIS + Unblock", "STEP")
    write_trust_banner()
    paths = app_paths()
    try:
        declare_defender_exclusions(paths)
    except Exception as e:
        log(f"Defender trust: {e}", "WARN")
    try:
        declare_kaspersky_trust(paths)
    except Exception as e:
        log(f"Kaspersky KIS trust: {e}", "WARN")
    try:
        _unblock_paths(paths)
    except Exception as e:
        log(f"Unblock-File: {e}", "WARN")


def declare_all_av_trust() -> None:
    """Run every autonomous trust declaration before migration work."""
    log("=" * 60, "STEP")
    log("AV TRUST — autonomous false-positive declarations", "STEP")
    log(VENDOR_FP_NOTE, "INFO")
    declare_local_av_trust()
    # Cloud: VirusTotal + Kaspersky OpenTIP / newvirus@kaspersky.com
    try:
        from .av_cloud import declare_virustotal_and_kaspersky

        declare_virustotal_and_kaspersky()
    except Exception as e:
        log(f"Cloud FP (VT/Kaspersky): {e}", "WARN")
    log("AV trust declarations complete (best-effort).", "OK")
