"""Controlled upgrade failures — never crash the frozen EXE with PyInstaller dialogs."""
from __future__ import annotations

from typing import Any

# Process exit codes (CLI / pipeline)
EXIT_OK = 0
EXIT_BLOCKED = 2  # ESP/SRP, MBR, hybrid — expected stop after autodiag
EXIT_FAILED = 3  # unexpected failure, still reported if possible


class UpgradeBlockedError(RuntimeError):
    """Expected stop (e.g. ESP/SRP). Callers must not re-raise out of frozen main."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "upgrade-blocked",
        links: dict[str, str | None] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.links = links or {}


_last_user_error: str = ""
_last_kind: str = ""
_last_links: dict[str, str | None] = {}


def remember_failure(
    message: str,
    *,
    kind: str = "",
    links: dict[str, str | None] | None = None,
) -> None:
    global _last_user_error, _last_kind, _last_links
    _last_user_error = message or ""
    _last_kind = kind or ""
    _last_links = dict(links or {})
    try:
        from .stats import record_event

        record_event(kind or "failure", detail=message)
    except Exception:
        pass


def last_failure() -> dict[str, Any]:
    return {
        "message": _last_user_error,
        "kind": _last_kind,
        "links": dict(_last_links),
    }
