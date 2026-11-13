"""Startup stays usable when GitHub and the network do not answer."""
from __future__ import annotations

import os
import socket
import sys
import time
import types
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
if "engine" not in sys.modules:
    pkg = types.ModuleType("engine")
    pkg.__path__ = [str(ROOT / "python" / "engine")]  # type: ignore[attr-defined]
    pkg.__package__ = "engine"
    sys.modules["engine"] = pkg

from engine.issue_url import file_report  # noqa: E402
from engine.offline import CONNECT_TIMEOUT, retry_pending_reports  # noqa: E402
from engine.pre_reboot import no_args_entrypoint  # noqa: E402
from engine.segment_download import download_image  # noqa: E402
from engine.self_update import startup_update_async  # noqa: E402
from engine.win11_release import nudge_cumulative_update, refresh_latest_win11  # noqa: E402


def _down(*_args, **_kwargs):
    raise TimeoutError("timed out")


def test_startup_completes_when_the_network_is_down(tmp: Path) -> None:
    os.environ["MAGIC_PENDING_DIR"] = str(tmp)
    os.environ.pop("MAGIC_NO_UPDATE", None)
    os.environ.pop("MAGIC_SKIP_UPDATE_CHECK", None)
    saved_socket = socket.create_connection
    saved_open = urllib.request.urlopen
    socket.create_connection = _down  # type: ignore[assignment]
    urllib.request.urlopen = _down  # type: ignore[assignment]
    seen: list[str] = []
    try:
        started = time.monotonic()

        def guard() -> None:
            seen.append("guard")

        def update() -> None:
            seen.append("update")
            result = startup_update_async([])
            assert result["status"] == "scheduled"

        def app() -> None:
            seen.append("app")

        no_args_entrypoint(guard=guard, update=update, continue_app=app)
        elapsed = time.monotonic() - started
        assert seen == ["guard", "update", "app"]
        assert elapsed < 2, elapsed
        assert CONNECT_TIMEOUT <= 5

        windows = nudge_cumulative_update("26100.1")
        assert windows["ok"] is False
        assert windows["reason"] == "offline"
        assert "no network" in windows["detail"]

        try:
            download_image("https://example.invalid/win11.iso", tmp / "win11.iso", retries=1, hash_attempts=1)
            raised = False
        except Exception as exc:
            raised = True
            assert "ISO download skipped" in str(exc)
            assert "no network" in str(exc)
        assert raised

        time.sleep(0.3)
        target = refresh_latest_win11(fetch_text=_down, save=False, state={})
        assert target.version

        filed = file_report(
            title="offline diag",
            body="redacted report",
            token="sek",
            http_post=_down,
            gh_run=_down,
        )
        assert filed is None
        queued = list(tmp.glob("*.json"))
        assert queued
        assert "redacted report" in queued[0].read_text(encoding="utf-8")

        def sender(title: str, body: str) -> dict[str, str]:
            assert "redacted" in body
            return {"url": "https://github.com/dlnraja/win11-magic-upgrade/issues/9"}

        assert retry_pending_reports(sender=sender) == 1
        assert list(tmp.glob("*.json")) == []
    finally:
        socket.create_connection = saved_socket  # type: ignore[assignment]
        urllib.request.urlopen = saved_open  # type: ignore[assignment]
        os.environ.pop("MAGIC_PENDING_DIR", None)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as name:
        test_startup_completes_when_the_network_is_down(Path(name))
    print("offline startup OK")
