"""Network work must not stop the app.

Auto-update, issue upload, gists, release lookups, and telemetry use a short
connect timeout and a background thread. A failed upload is stored under
%ProgramData%\\Win11MagicUpgrade\\pending-reports and retried on a later run.
Boot repair, mountvol, and a local ISO do not use the network.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

CONNECT_TIMEOUT = 5


class OfflineSkip(RuntimeError):
    """A network step was skipped. Local work can continue."""


def is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, OfflineSkip):
        return True
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    if isinstance(exc, OSError):
        errno = getattr(exc, "errno", None)
        if errno in (11, 100, 101, 103, 110, 111, 113, 10051, 10060, 10061, 10064, 10065):
            return True
    name = type(exc).__name__
    if name in ("URLError", "HTTPError"):
        reason = getattr(exc, "reason", None)
        if isinstance(exc, type(exc)) and getattr(exc, "code", None):
            return False
        return reason is None or is_network_error(reason) or isinstance(reason, OSError)
    text = str(exc).lower()
    return any(
        word in text
        for word in ("timed out", "timeout", "unreachable", "getaddrinfo", "name or service", "network is unreachable")
    )


def explain_network(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    detail = str(reason or exc).strip() or type(exc).__name__
    detail = re.sub(r"\s+", " ", detail)[:240]
    return f"no network ({detail})"


def probe_host(host: str, port: int = 443, timeout: int = CONNECT_TIMEOUT) -> None:
    """Raise OfflineSkip when a TCP connect does not succeed within ``timeout``."""
    try:
        sock = socket.create_connection((host, int(port)), timeout)
    except OSError as exc:
        raise OfflineSkip(f"cannot reach {host}:{port}: {exc}") from exc
    else:
        sock.close()


def host_of(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise OfflineSkip(f"ISO download skipped: the download URL has no host ({url[:80]})")
    if parsed.port:
        port = int(parsed.port)
    else:
        port = 443 if (parsed.scheme or "https") == "https" else 80
    return host, port


def pending_reports_dir() -> Path:
    override = (os.environ.get("MAGIC_PENDING_DIR") or "").strip()
    if override:
        path = Path(override)
    else:
        base = (os.environ.get("PROGRAMDATA") or os.environ.get("ProgramData") or "").strip()
        if base:
            path = Path(base) / "Win11MagicUpgrade" / "pending-reports"
        else:
            from .logutil import STATE_DIR

            path = STATE_DIR / "pending-reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def queue_redacted_report(*, title: str, body: str, reason: str) -> Path:
    """Save one redacted report and log a single warning."""
    from .logutil import log

    directory = pending_reports_dir()
    stamp = str(time.time_ns())
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (title or "report")[:40]).strip("._") or "report"
    path = directory / f"{stamp}-{slug}.json"
    path.write_text(
        json.dumps({"title": title[:180], "body": body or ""}, ensure_ascii=False),
        encoding="utf-8",
    )
    log(
        f"Upload skipped: {reason}. Queued the redacted report at {path}. It will be retried on a later run.",
        "WARN",
    )
    return path


def retry_pending_reports(*, sender=None) -> int:
    """Upload queued reports. One warning covers every report that still cannot leave the machine."""
    from .logutil import log

    directory = pending_reports_dir()
    sent = 0
    failed = 0
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            failed += 1
            continue
        title = str(payload.get("title") or "diagnostic")
        body = str(payload.get("body") or "")
        try:
            if sender is None:
                from .issue_url import file_report

                filed = file_report(title=title, body=body, queue_on_failure=False)
            else:
                filed = sender(title, body)
        except Exception:
            filed = None
        if filed and filed.get("url"):
            try:
                path.unlink()
            except OSError:
                pass
            sent += 1
            log(f"Queued report uploaded: {filed['url']}", "INFO")
        else:
            failed += 1
    if failed:
        log(f"Pending reports still queued ({failed}) under {directory}. Will retry on a later run.", "WARN")
    return sent
