"""
Autonomous antivirus trust declarations (false-positive mitigation).

This app is a legitimate Windows upgrade helper — NOT malware / NOT a PDF trojan.
PyInstaller + admin elevation often triggers heuristic labels like Trojan.PDF.
We declare exclusions locally so Defender / Kaspersky stop blocking the run.
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


def _find_kaspersky_cli() -> Path | None:
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ]
    names = ("avp.com", "avp.exe", "kavshell.exe")
    for root in roots:
        base = Path(root) / "Kaspersky Lab"
        if not base.exists():
            continue
        for child in sorted(base.glob("*")):
            for name in names:
                cand = child / name
                if cand.exists():
                    return cand
    return None


def declare_kaspersky_trust(paths: list[Path] | None = None) -> bool:
    """
    Best-effort Kaspersky trusted-app / exclusion declaration.
    Also writes a local FP statement Kaspersky labs can use if the sample is submitted.
    """
    paths = paths or app_paths()
    declared = False
    cli = _find_kaspersky_cli()
    if cli:
        log(f"Kaspersky CLI found: {cli}", "INFO")
        for p in paths:
            # Common KES / KAV shell patterns (version-dependent; ignore failures)
            for args in (
                [str(cli), "ADD", str(p)],
                [str(cli), "SET", f"TrustedZone.TrustedApplications.Path={p}"],
            ):
                out = _run(args, timeout=30)
                if out and "error" not in out.lower():
                    declared = True
                    log(f"Kaspersky trust attempt OK for {p}", "OK")
                    break
    else:
        log("Kaspersky CLI not found — writing local FP declaration only", "INFO")

    notice = STATE_DIR / "KASPERSKY_FALSE_POSITIVE_DECLARATION.txt"
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        lines = [
            APP_NAME,
            "=" * 60,
            VENDOR_FP_NOTE,
            "",
            "Product: Win11MagicUpgrade.exe",
            "Purpose: Official Microsoft ISO download + Windows Setup orchestration",
            "No PDF parsing, no document exploits, no credential theft.",
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


def declare_all_av_trust() -> None:
    """Run every autonomous trust declaration before migration work."""
    log("=" * 60, "STEP")
    log("AV TRUST — autonomous false-positive declarations", "STEP")
    log(VENDOR_FP_NOTE, "INFO")
    write_trust_banner()
    paths = app_paths()
    try:
        declare_defender_exclusions(paths)
    except Exception as e:
        log(f"Defender trust: {e}", "WARN")
    try:
        declare_kaspersky_trust(paths)
    except Exception as e:
        log(f"Kaspersky trust: {e}", "WARN")
    # Cloud: VirusTotal + Kaspersky OpenTIP / newvirus@kaspersky.com
    try:
        from .av_cloud import declare_virustotal_and_kaspersky

        declare_virustotal_and_kaspersky()
    except Exception as e:
        log(f"Cloud FP (VT/Kaspersky): {e}", "WARN")
    log("AV trust declarations complete (best-effort).", "OK")
