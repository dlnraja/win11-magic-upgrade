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


def _open_sink() -> "_Quiet":
    """A sink that is never a console. Windows treats NUL as a character device."""
    raw = None
    try:
        raw = open(os.devnull, "w", encoding="utf-8", errors="replace")
    except OSError:
        raw = None
    return _Quiet(raw)


class _Quiet:
    encoding = "utf-8"

    def __init__(self, raw: object | None = None) -> None:
        self._raw = raw

    def write(self, text: str = "") -> int:
        if self._raw is not None:
            try:
                written = self._raw.write(text)
                if isinstance(written, int):
                    return written
            except Exception:
                pass
        return len(text or "")

    def flush(self) -> None:
        if self._raw is None:
            return
        try:
            self._raw.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no console")
