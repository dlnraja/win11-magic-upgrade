"""Local opt-in failure/success counters (no network). Set MAGIC_STATS=1."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log

STATS_FILE = STATE_DIR / "local-stats.json"


def stats_enabled() -> bool:
    return os.environ.get("MAGIC_STATS", "").strip().lower() in ("1", "true", "yes")


def _load() -> dict[str, Any]:
    if not STATS_FILE.is_file():
        return {"events": [], "counts": {}}
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"events": [], "counts": {}}


def record_event(kind: str, *, detail: str = "") -> None:
    if not stats_enabled():
        return
    data = _load()
    counts = data.setdefault("counts", {})
    counts[kind] = int(counts.get(kind) or 0) + 1
    events = data.setdefault("events", [])
    events.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "detail": (detail or "")[:200],
        }
    )
    data["events"] = events[-200:]
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log(f"Local stats +1 {kind} → {STATS_FILE}", "INFO")
    except OSError as e:
        log(f"stats write: {e}", "WARN")
