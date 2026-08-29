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


def format_stats_summary() -> str:
    """Human-readable dump for --cli --stats (works even if MAGIC_STATS was off for past events)."""
    data = _load()
    counts = data.get("counts") or {}
    events = data.get("events") or []
    lines = [
        "Win11 Magic Upgrade — local stats",
        f"File: {STATS_FILE}",
        f"MAGIC_STATS enabled now: {stats_enabled()}",
        "",
        "Counts:",
    ]
    if not counts:
        lines.append("  (none yet — set MAGIC_STATS=1 and run One-Click)")
    else:
        for k in sorted(counts.keys()):
            lines.append(f"  {k}: {counts[k]}")
    lines.append("")
    lines.append("Recent events (last 15):")
    if not events:
        lines.append("  (none)")
    else:
        for ev in events[-15:]:
            lines.append(f"  {ev.get('ts', '?')}  {ev.get('kind')}  {ev.get('detail', '')}")
    return "\n".join(lines)


def print_stats() -> None:
    print(format_stats_summary())
