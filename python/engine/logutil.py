"""Logging + persistent state — stdlib only."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

STATE_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "Win11MagicUpgrade"
LOG_DIR = STATE_DIR / "logs"
STATE_FILE = STATE_DIR / "state.json"

_log_file: Path | None = None
_sink: Callable[[str], None] | None = None


def init_logging(sink: Callable[[str], None] | None = None) -> Path:
    global _log_file, _sink
    _sink = sink
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _log_file = LOG_DIR / f"upgrade-{stamp}.log"
    log("=== Win11 Magic Upgrade (Python engine, no .NET 4.x) ===")
    return _log_file


def log(msg: str, level: str = "INFO") -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}"
    print(line, flush=True)
    if _sink:
        try:
            _sink(line + "\n")
        except Exception:
            pass
    if _log_file:
        try:
            with _log_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


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
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(cur, indent=2, default=str), encoding="utf-8")
