"""Small helpers for the resizable log pane. No Tk imports."""
from __future__ import annotations

import json
from pathlib import Path

LOG_FOLDER = Path(r"C:\MagicUpgrade\logs")
MIN_PANE = 120


def log_folder() -> Path:
    return LOG_FOLDER


def layout_file(state_dir: Path) -> Path:
    return Path(state_dir) / "gui-layout.json"


def load_sash(path: Path, default: int = 280) -> int:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return int(data.get("log_sash", default))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return default


def save_sash(path: Path, pos: int) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"log_sash": int(pos)}), encoding="utf-8")


def clamp_sash(pos: int, total: int, minimum: int = MIN_PANE) -> int:
    if total <= minimum * 2:
        return max(1, total // 2)
    return max(minimum, min(int(pos), int(total) - minimum))


def newest_log(folder: Path) -> Path | None:
    directory = Path(folder)
    if not directory.is_dir():
        return None
    files = [item for item in directory.glob("*.log") if item.is_file()]
    if not files:
        return None
    return max(files, key=lambda item: item.stat().st_mtime)
