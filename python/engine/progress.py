"""Progress reporting — overall + step bars, ETA, heartbeat (UI never looks frozen)."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

ProgressCallback = Callable[[dict[str, Any]], None]

_cb: ProgressCallback | None = None
_lock = threading.Lock()

# Typical durations (seconds) used for remaining-time estimates when no byte ETA
# weight = share of overall 0–100 bar
PIPELINE_PHASES: list[tuple[str, str, float, float]] = [
    ("av", "Phase 0/7 — Antivirus trust", 5.0, 40.0),
    ("diag", "Phase 1/7 — Diagnose", 8.0, 30.0),
    ("patch", "Phase 2–4/7 — Patches + bypass", 18.0, 150.0),
    ("iso", "Phase 5/7 — ISO prepare / download", 42.0, 1200.0),
    ("chain", "Phase 6/7 — Migration chain", 20.0, 200.0),
    ("setup", "Phase 7/7 — Setup launch", 7.0, 90.0),
]

_session: dict[str, Any] = {
    "active": False,
    "started": 0.0,
    "phase_id": "",
    "phase_label": "",
    "phase_idx": 0,
    "sub_percent": None,  # 0-100 within phase, or None
    "detail": "",
    "indeterminate": True,
    "last_beat": 0.0,
    "bytes_done": 0,
    "bytes_total": 0,
    "speed_bps": 0.0,
    "override_eta": None,
    "pulse": 0,
}


def set_progress_callback(cb: ProgressCallback | None) -> None:
    global _cb
    _cb = cb


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


def format_elapsed(seconds: float) -> str:
    return format_eta(max(0.0, seconds))


def start_session(profile: str = "oneclick") -> None:
    with _lock:
        _session.update(
            {
                "active": True,
                "started": time.time(),
                "phase_id": "",
                "phase_label": "Starting…",
                "phase_idx": 0,
                "sub_percent": 0.0,
                "detail": profile,
                "indeterminate": True,
                "last_beat": time.time(),
                "bytes_done": 0,
                "bytes_total": 0,
                "speed_bps": 0.0,
                "override_eta": None,
                "pulse": 0,
            }
        )
    _emit()


def end_session(*, success: bool = True) -> None:
    with _lock:
        _session["active"] = False
        _session["phase_label"] = "Done" if success else "Stopped"
        _session["sub_percent"] = 100.0 if success else _session.get("sub_percent")
        _session["indeterminate"] = False
        _session["detail"] = "Finished" if success else "Finished with errors"
        _session["override_eta"] = 0.0
    _emit()


def _phase_index(phase_id: str) -> int:
    for i, (pid, *_rest) in enumerate(PIPELINE_PHASES):
        if pid == phase_id:
            return i
    return max(0, int(_session.get("phase_idx") or 0))


def _overall_percent_locked() -> float:
    idx = int(_session.get("phase_idx") or 0)
    idx = max(0, min(idx, len(PIPELINE_PHASES) - 1))
    base = sum(p[2] for p in PIPELINE_PHASES[:idx])
    weight = PIPELINE_PHASES[idx][2]
    sub = _session.get("sub_percent")
    if sub is None:
        # Mid-phase estimate while indeterminate
        sub = 35.0
    return min(99.5, base + weight * (float(sub) / 100.0))


def _eta_locked() -> float | None:
    if _session.get("override_eta") is not None:
        return float(_session["override_eta"])
    # Byte-based ETA wins when downloading
    total = int(_session.get("bytes_total") or 0)
    done = int(_session.get("bytes_done") or 0)
    speed = float(_session.get("speed_bps") or 0.0)
    if total > 0 and speed > 0 and done < total:
        return (total - done) / speed

    idx = int(_session.get("phase_idx") or 0)
    sub = float(_session.get("sub_percent") or 0.0)
    # Remaining typical time in current + future phases
    remain = 0.0
    for i, (_pid, _label, _w, typical) in enumerate(PIPELINE_PHASES):
        if i < idx:
            continue
        if i == idx:
            frac_left = max(0.0, 1.0 - (sub / 100.0))
            remain += typical * frac_left
        else:
            remain += typical
    return remain


def snapshot() -> dict[str, Any]:
    with _lock:
        now = time.time()
        started = float(_session.get("started") or now)
        elapsed = now - started if _session.get("active") else 0.0
        overall = _overall_percent_locked() if _session.get("active") else float(_session.get("sub_percent") or 0)
        eta = _eta_locked() if _session.get("active") else None
        pulse = int(_session.get("pulse") or 0)
        dots = "." * (1 + (pulse % 3))
        return {
            "active": bool(_session.get("active")),
            "phase": _session.get("phase_label") or "",
            "phase_id": _session.get("phase_id") or "",
            "detail": _session.get("detail") or "",
            "percent": overall,
            "step_percent": _session.get("sub_percent"),
            "indeterminate": bool(_session.get("indeterminate")),
            "elapsed_seconds": elapsed,
            "eta_seconds": eta,
            "bytes_done": int(_session.get("bytes_done") or 0),
            "bytes_total": int(_session.get("bytes_total") or 0),
            "speed_bps": float(_session.get("speed_bps") or 0.0),
            "alive": dots,
            "last_beat": float(_session.get("last_beat") or 0),
            "stale_seconds": now - float(_session.get("last_beat") or now),
        }


def set_phase(phase_id: str, detail: str = "") -> None:
    idx = _phase_index(phase_id)
    label = PIPELINE_PHASES[idx][1] if idx < len(PIPELINE_PHASES) else phase_id
    with _lock:
        _session["phase_id"] = phase_id
        _session["phase_idx"] = idx
        _session["phase_label"] = label
        _session["detail"] = detail or label
        _session["sub_percent"] = 0.0
        _session["indeterminate"] = True
        _session["override_eta"] = None
        _session["bytes_done"] = 0
        _session["bytes_total"] = 0
        _session["speed_bps"] = 0.0
        _session["last_beat"] = time.time()
        _session["pulse"] = int(_session.get("pulse") or 0) + 1
        _session["active"] = True
    _emit()


def set_step(
    *,
    percent: float | None = None,
    detail: str = "",
    eta_seconds: float | None = None,
    indeterminate: bool | None = None,
    bytes_done: int = 0,
    bytes_total: int = 0,
    speed_bps: float = 0.0,
) -> None:
    with _lock:
        if detail:
            _session["detail"] = detail
        if percent is not None:
            _session["sub_percent"] = max(0.0, min(100.0, float(percent)))
        if indeterminate is not None:
            _session["indeterminate"] = bool(indeterminate)
        elif percent is not None:
            _session["indeterminate"] = False
        if eta_seconds is not None:
            _session["override_eta"] = eta_seconds
        if bytes_total:
            _session["bytes_done"] = bytes_done
            _session["bytes_total"] = bytes_total
            _session["speed_bps"] = speed_bps
        _session["last_beat"] = time.time()
        _session["pulse"] = int(_session.get("pulse") or 0) + 1
        _session["active"] = True
    _emit()


def heartbeat(detail: str | None = None) -> None:
    """Pulse activity so the GUI never looks frozen during long ops."""
    with _lock:
        if not _session.get("active"):
            return
        if detail:
            # Keep heartbeat details short
            _session["detail"] = detail[:120]
        _session["last_beat"] = time.time()
        _session["pulse"] = int(_session.get("pulse") or 0) + 1
        # Nudge sub-progress slowly while indeterminate so overall bar inches forward
        if _session.get("indeterminate") and _session.get("sub_percent") is not None:
            sub = float(_session["sub_percent"])
            if sub < 85.0:
                _session["sub_percent"] = min(85.0, sub + 0.35)
    _emit()


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
    """
    Backward-compatible progress API used by iso/pipeline.
    Maps into the session so overall % + ETA stay consistent.
    """
    # Map known phase titles → phase ids
    phase_map = {
        "antivirus": "av",
        "trust": "av",
        "diagnose": "diag",
        "patch": "patch",
        "bypass": "patch",
        "iso": "iso",
        "download": "iso",
        "hash": "iso",
        "inspect": "iso",
        "verify": "iso",
        "prefetch": "iso",
        "chain": "chain",
        "setup": "setup",
        "migration": "chain",
    }
    pid = ""
    low = (phase or "").lower()
    for key, val in phase_map.items():
        if key in low:
            pid = val
            break
    if pid and pid != _session.get("phase_id"):
        # Don't reset subprogress harshly if same family — set_phase resets to 0
        if not _session.get("phase_id") or _phase_index(pid) >= int(_session.get("phase_idx") or 0):
            set_phase(pid, detail or phase)

    with _lock:
        if phase and not pid:
            _session["phase_label"] = phase
        if detail:
            _session["detail"] = detail
        elif phase:
            _session["detail"] = phase
        if percent is not None:
            # If caller sends overall-ish percent during early phases, treat as step %
            # Downloads send 0-100 of file — that's step percent within iso phase
            _session["sub_percent"] = max(0.0, min(100.0, float(percent)))
            _session["indeterminate"] = False
        else:
            _session["indeterminate"] = bool(indeterminate) if indeterminate or percent is None else False
            if indeterminate:
                _session["indeterminate"] = True
        if bytes_total:
            _session["bytes_done"] = bytes_done
            _session["bytes_total"] = bytes_total
            _session["speed_bps"] = speed_bps
        if eta_seconds is not None:
            _session["override_eta"] = eta_seconds
        elif bytes_total and speed_bps > 0:
            _session["override_eta"] = None  # use byte ETA
        _session["last_beat"] = time.time()
        _session["pulse"] = int(_session.get("pulse") or 0) + 1
        _session["active"] = True
    _emit()


def _emit() -> None:
    if not _cb:
        return
    try:
        snap = snapshot()
        _cb(snap)
    except Exception:
        pass
