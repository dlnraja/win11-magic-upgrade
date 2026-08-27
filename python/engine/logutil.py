"""Logging + persistent state - stdlib only.

Mirrors Windows Setup / Migration Tool style:
  %LOCALAPPDATA%\\Win11MagicUpgrade\\Panther\\setupact.log   (all actions)
  %LOCALAPPDATA%\\Win11MagicUpgrade\\Panther\\setuperr.log   (errors + warnings)
  %LOCALAPPDATA%\\Win11MagicUpgrade\\MigrationReport.txt    (human summary)
  %LOCALAPPDATA%\\Win11MagicUpgrade\\logs\\upgrade-*.log     (session transcript)
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

STATE_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "Win11MagicUpgrade"
LOG_DIR = STATE_DIR / "logs"
PANTHER_DIR = STATE_DIR / "Panther"
STATE_FILE = STATE_DIR / "state.json"
SETUPACT = PANTHER_DIR / "setupact.log"
SETUPERR = PANTHER_DIR / "setuperr.log"
MIGRATION_REPORT = STATE_DIR / "MigrationReport.txt"
DESKTOP_REPORT_NAME = "Win11MagicUpgrade-MigrationReport.txt"

_log_file: Path | None = None
_sink: Callable[[str], None] | None = None
_session_id: str = ""
_error_count: int = 0
_warn_count: int = 0
_error_lines: list[str] = []
_warn_lines: list[str] = []
_component = "MAGIC"


def _ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PANTHER_DIR.mkdir(parents=True, exist_ok=True)


def _panther_line(level: str, msg: str, component: str | None = None) -> str:
    """
    Windows Setup-like line:
      2026-08-27 09:32:01, Error                 MAGIC  message
    """
    lvl = {
        "INFO": "Info",
        "OK": "Info",
        "STEP": "Info",
        "WARN": "Warning",
        "ERROR": "Error",
        "FATAL": "FatalError",
    }.get(level.upper(), "Info")
    comp = (component or _component).ljust(6)[:12]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # pad level like setupact (~22 chars before component in MS logs)
    return f"{ts}, {lvl:<20} {comp}  {msg}"


def init_logging(sink: Callable[[str], None] | None = None) -> Path:
    global _log_file, _sink, _session_id, _error_count, _warn_count, _error_lines, _warn_lines
    _sink = sink
    _error_count = 0
    _warn_count = 0
    _error_lines = []
    _warn_lines = []
    _ensure_dirs()
    _session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    _log_file = LOG_DIR / f"upgrade-{_session_id}.log"

    # Session banner in Panther logs (append; keep history across runs)
    banner = (
        f"\n========== SESSION {_session_id} — Win11 Magic Upgrade ==========\n"
        f"Engine: pure Python (no .NET 4.x / no PowerShell)\n"
        f"Host: {os.environ.get('COMPUTERNAME', '?')}  User: {os.environ.get('USERNAME', '?')}\n"
        f"Log folder: {PANTHER_DIR}\n"
    )
    for p in (SETUPACT, SETUPERR, _log_file):
        try:
            with p.open("a", encoding="utf-8", errors="replace") as f:
                f.write(banner)
        except Exception:
            pass

    log("=== Win11 Magic Upgrade (Python engine, no .NET 4.x) ===")
    log(f"Panther logs: {SETUPACT}", "INFO")
    log(f"Error log:    {SETUPERR}", "INFO")
    log(f"Report:       {MIGRATION_REPORT}", "INFO")
    return _log_file


def log(msg: str, level: str = "INFO", *, component: str | None = None) -> None:
    global _error_count, _warn_count
    level_u = (level or "INFO").upper()
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level_u}] {msg}"
    panther = _panther_line(level_u, msg, component)

    # Keep GUI alive on every log line (status bars / ETA)
    try:
        from .progress import heartbeat

        heartbeat(msg[:100])
    except Exception:
        pass

    # Console (ASCII-safe for legacy consoles)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)

    if _sink:
        try:
            _sink(line + "\n")
        except Exception:
            pass

    # Session transcript
    if _log_file:
        try:
            with _log_file.open("a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        except Exception:
            pass

    # setupact.log — everything
    try:
        with SETUPACT.open("a", encoding="utf-8", errors="replace") as f:
            f.write(panther + "\n")
    except Exception:
        pass

    # setuperr.log — errors + warnings (migration-tool style digest)
    if level_u in ("ERROR", "FATAL", "WARN", "WARNING"):
        try:
            with SETUPERR.open("a", encoding="utf-8", errors="replace") as f:
                f.write(panther + "\n")
        except Exception:
            pass
        if level_u in ("ERROR", "FATAL"):
            _error_count += 1
            _error_lines.append(panther)
            if len(_error_lines) > 200:
                _error_lines.pop(0)
        else:
            _warn_count += 1
            _warn_lines.append(panther)
            if len(_warn_lines) > 200:
                _warn_lines.pop(0)


def harvest_windows_setup_errors(max_chars: int = 80_000) -> list[str]:
    """Pull recent ERROR lines from Windows Setup / Migration logs into our report."""
    paths = [
        Path(r"C:\$WINDOWS.~BT\Sources\Panther\setuperr.log"),
        Path(r"C:\$WINDOWS.~BT\Sources\Rollback\setuperr.log"),
        Path(r"C:\$WINDOWS.~BT\Sources\Rollback\setupact.err"),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Panther" / "setuperr.log",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Panther" / "setupact.log",
    ]
    found: list[str] = []
    err_re = re.compile(r"\b(Error|FatalError|0x[C8][0-9A-Fa-f]{7})\b", re.I)
    for p in paths:
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > max_chars:
                text = text[-max_chars:]
            hits = [ln for ln in text.splitlines() if err_re.search(ln)]
            if hits:
                found.append(f"--- From {p} ({len(hits)} matching lines) ---")
                found.extend(hits[-80:])
                log(f"Harvested {len(hits)} error-ish lines from {p.name}", "WARN")
        except PermissionError:
            pass
        except Exception as e:
            log(f"Could not read {p}: {e}", "INFO")
    return found


def _desktop_dir() -> Path | None:
    home = Path.home()
    for name in ("Desktop", "OneDrive\\Desktop", "OneDrive\\Bureau", "Bureau"):
        d = home / name
        if d.is_dir():
            return d
    # Known folder via USERPROFILE
    desk = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    return desk if desk.is_dir() else None


def write_migration_report(
    *,
    title: str = "Win11 Magic Upgrade — Migration Report",
    extra: dict[str, Any] | None = None,
    copy_to_desktop: bool = True,
) -> Path:
    """
    Write a Windows Migration Tool-style plain-text report with errors,
    warnings, log locations, and harvested Setup logs.
    """
    _ensure_dirs()
    harvested = harvest_windows_setup_errors()
    extra = extra or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        title,
        "=" * len(title),
        f"Generated:     {now}",
        f"Session:       {_session_id or 'n/a'}",
        f"Computer:      {os.environ.get('COMPUTERNAME', '?')}",
        f"User:          {os.environ.get('USERNAME', '?')}",
        f"Errors:        {_error_count}",
        f"Warnings:      {_warn_count}",
        "",
        "Log files (like Windows Setup Panther)",
        "-" * 40,
        f"setupact.log (all actions):  {SETUPACT}",
        f"setuperr.log (errors/warns): {SETUPERR}",
        f"Session transcript:          {_log_file or LOG_DIR}",
        f"State JSON:                  {STATE_FILE}",
        "",
    ]
    if extra:
        lines.append("Run summary")
        lines.append("-" * 40)
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
        lines.append("")

    lines.append("Errors recorded this session")
    lines.append("-" * 40)
    if _error_lines:
        lines.extend(_error_lines)
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("Warnings recorded this session")
    lines.append("-" * 40)
    if _warn_lines:
        lines.extend(_warn_lines[-100:])
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("Windows Setup / Migration errors harvested")
    lines.append("-" * 40)
    if harvested:
        lines.extend(harvested)
    else:
        lines.append("(no Windows Panther setuperr.log hits found)")
    lines.append("")
    lines.append("Notes")
    lines.append("-" * 40)
    lines.append("Open setuperr.log for a concise error list (same idea as Windows setuperr.log).")
    lines.append("Open setupact.log for the full chronological action log (like setupact.log).")
    lines.append("If Setup failed, also check C:\\$WINDOWS.~BT\\Sources\\Panther\\setuperr.log")
    lines.append("")

    body = "\n".join(lines) + "\n"
    MIGRATION_REPORT.write_text(body, encoding="utf-8", errors="replace")
    # Stable copies next to Panther
    try:
        shutil.copy2(MIGRATION_REPORT, PANTHER_DIR / "MigrationReport.txt")
    except Exception:
        pass

    desktop_path = None
    if copy_to_desktop:
        desk = _desktop_dir()
        if desk:
            try:
                desktop_path = desk / DESKTOP_REPORT_NAME
                desktop_path.write_text(body, encoding="utf-8", errors="replace")
            except Exception:
                desktop_path = None

    log(f"Migration report written: {MIGRATION_REPORT}", "OK")
    if desktop_path:
        log(f"Desktop copy: {desktop_path}", "OK")
    return MIGRATION_REPORT


def get_log_paths() -> dict[str, str]:
    return {
        "setupact": str(SETUPACT),
        "setuperr": str(SETUPERR),
        "report": str(MIGRATION_REPORT),
        "session": str(_log_file) if _log_file else "",
        "panther_dir": str(PANTHER_DIR),
    }


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(patch: dict[str, Any]) -> None:
    cur = load_state()
    cur.update(patch)
    cur["UpdatedAt"] = datetime.now().isoformat()
    _ensure_dirs()
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(cur, indent=2, default=str), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as e:
        log(f"State save failed: {e}", "ERROR")
        try:
            STATE_FILE.write_text(json.dumps(cur, indent=2, default=str), encoding="utf-8")
        except OSError as e2:
            log(f"State save fallback failed: {e2}", "ERROR")
