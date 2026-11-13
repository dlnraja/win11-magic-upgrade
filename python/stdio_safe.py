"""Console streams are None in a windowed PyInstaller build."""
from __future__ import annotations

import os
import sys


def stream_isatty(stream: object) -> bool:
    """False when the stream is missing or cannot answer isatty."""
    if stream is None:
        return False
    check = getattr(stream, "isatty", None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return False


def ensure_stdio() -> None:
    """Give print() a stream when stdout or stderr was not created."""
    if sys.stdout is None:
        sys.stdout = _open_sink()
    if sys.stderr is None:
        sys.stderr = _open_sink()


def _open_sink():
    try:
        return open(os.devnull, "w", encoding="utf-8", errors="replace")
    except OSError:
        return _Silent()


class _Silent:
    encoding = "utf-8"

    def write(self, text: str = "") -> int:
        return len(text or "")

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no console")
