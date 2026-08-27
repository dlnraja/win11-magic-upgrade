"""Download / phase progress reporting for GUI + CLI."""
from __future__ import annotations

from typing import Any, Callable

ProgressCallback = Callable[[dict[str, Any]], None]

_cb: ProgressCallback | None = None


def set_progress_callback(cb: ProgressCallback | None) -> None:
    global _cb
    _cb = cb


def report_progress(
    *,
    phase: str = "",
    percent: float | None = None,
    detail: str = "",
    bytes_done: int = 0,
    bytes_total: int = 0,
    speed_bps: float = 0.0,
    eta_seconds: float | None = None,
    indeterminate: bool = False,
) -> None:
    if not _cb:
        return
    try:
        _cb(
            {
                "phase": phase,
                "percent": percent,
                "detail": detail,
                "bytes_done": bytes_done,
                "bytes_total": bytes_total,
                "speed_bps": speed_bps,
                "eta_seconds": eta_seconds,
                "indeterminate": indeterminate,
            }
        )
    except Exception:
        pass


def format_bytes(n: int | float) -> str:
    n = float(n)
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return f"{n / div:.2f} {unit}"
    return f"{int(n)} B"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds != seconds:  # NaN
        return "--:--"
    s = int(seconds)
    if s > 3600:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}h {m:02d}m {sec:02d}s"
    m, sec = divmod(s, 60)
    return f"{m:02d}:{sec:02d}"


def format_speed(bps: float) -> str:
    if bps <= 0:
        return "—"
    return f"{format_bytes(bps)}/s"
