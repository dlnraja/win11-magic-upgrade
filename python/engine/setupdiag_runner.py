"""
Download + run Microsoft SetupDiag (research: learn.microsoft.com/windows/deployment/upgrade/setupdiag).

Pure urllib + subprocess — no PowerShell. Results land in STATE_DIR for support ZIP.
"""
from __future__ import annotations

import os
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Official CDN path used by MS docs (may redirect)
SETUPDIAG_URL = (
    "https://go.microsoft.com/fwlink/?linkid=870142"
)
SETUPDIAG_DIR = STATE_DIR / "SetupDiag"


def _download(url: str, dest: Path) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Win11MagicUpgrade/SetupDiag"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return dest.is_file() and dest.stat().st_size > 10_000
    except Exception as e:
        log(f"SetupDiag download failed: {e}", "WARN")
        return False


def ensure_setupdiag() -> Path | None:
    """Return path to SetupDiag.exe if available/downloaded."""
    if os.environ.get("MAGIC_SKIP_SETUPDIAG", "").strip().lower() in ("1", "true", "yes"):
        return None
    local = (os.environ.get("MAGIC_SETUPDIAG") or "").strip().strip('"')
    if local and Path(local).is_file():
        return Path(local)
    exe = SETUPDIAG_DIR / "SetupDiag.exe"
    if exe.is_file():
        return exe
    # Try zip or direct exe from fwlink
    zip_path = SETUPDIAG_DIR / "SetupDiag.zip"
    raw = SETUPDIAG_DIR / "SetupDiag-download.bin"
    if not _download(SETUPDIAG_URL, raw):
        return None
    # Detect zip vs exe
    head = raw.read_bytes()[:4]
    if head[:2] == b"MZ":
        raw.replace(exe)
        return exe
    if head[:2] == b"PK":
        raw.replace(zip_path)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(SETUPDIAG_DIR)
            for p in SETUPDIAG_DIR.rglob("SetupDiag.exe"):
                return p
        except zipfile.BadZipFile as e:
            log(f"SetupDiag zip bad: {e}", "WARN")
    log("SetupDiag binary not recognized after download", "WARN")
    return None


def run_setupdiag(*, logs_path: Path | None = None) -> dict[str, Any]:
    """
    Run SetupDiag against Panther / Rollback logs.
    Safe: analysis only; writes SetupDiagResults.xml + .txt under STATE_DIR.
    """
    result: dict[str, Any] = {"ok": False, "exe": "", "output": "", "xml": ""}
    exe = ensure_setupdiag()
    if exe is None:
        result["output"] = "SetupDiag unavailable (skip or download failed)"
        save_state({"SetupDiag": result})
        return result
    result["exe"] = str(exe)
    out_txt = STATE_DIR / "SetupDiag-Results.txt"
    out_xml = STATE_DIR / "SetupDiagResults.xml"
    # Prefer BT Panther if present
    default_logs = Path(r"C:\$WINDOWS.~BT\Sources\Panther")
    if logs_path is None:
        logs_path = default_logs if default_logs.is_dir() else Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Panther"
    args = [
        str(exe),
        f"/Output:{out_txt}",
        f"/Format:xml",
    ]
    if logs_path.is_dir():
        args.append(f"/LogsPath:{logs_path}")
    log(f"Running SetupDiag → {out_txt}", "STEP")
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            cwd=str(exe.parent),
            creationflags=CREATE_NO_WINDOW,
        )
        result["output"] = ((r.stdout or "") + (r.stderr or ""))[-4000:]
        result["ok"] = r.returncode == 0 or out_txt.is_file()
    except (OSError, subprocess.TimeoutExpired) as e:
        result["output"] = str(e)
        log(f"SetupDiag run: {e}", "WARN")
        save_state({"SetupDiag": result})
        return result
    # Copy xml if produced beside output
    for cand in (out_xml, exe.parent / "SetupDiagResults.xml", STATE_DIR / "Logs" / "SetupDiag" / "SetupDiagResults.xml"):
        if cand.is_file():
            try:
                if cand.resolve() != out_xml.resolve():
                    out_xml.write_bytes(cand.read_bytes())
            except OSError:
                pass
            result["xml"] = str(out_xml)
            break
    if out_txt.is_file():
        try:
            text = out_txt.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines()[:40]:
                if line.strip():
                    log(f"SetupDiag: {line.strip()[:200]}", "INFO")
        except OSError:
            pass
        log(f"SetupDiag results: {out_txt}", "OK")
    save_state({"SetupDiag": result})
    return result
