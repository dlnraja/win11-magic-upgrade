"""BitLocker suspend covers the repair window and blocks boot writes while On."""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
if "engine" not in sys.modules:
    pkg = types.ModuleType("engine")
    pkg.__path__ = [str(ROOT / "python" / "engine")]  # type: ignore[attr-defined]
    pkg.__package__ = "engine"
    sys.modules["engine"] = pkg

from engine.bitlocker_guard import (  # noqa: E402
    boot_writes_allowed,
    clamp_reboot_count,
    parse_protection,
    parse_reboot_count,
    protector_ids,
    suspend_argv,
    suspend_for_window,
)

STATUS_OFF = """
Volume C: []
Protection Status:    Off
Lock Status:          Unlocked
Reboot Count:         3
"""
PROTECTORS = """
Numerical Password:
  ID: {11111111-2222-3333-4444-555555555555}
"""


def test_reboot_count_and_command() -> None:
    assert clamp_reboot_count(99) == 15
    assert clamp_reboot_count(-1) == 0
    argv = suspend_argv("manage-bde.exe", "C:", 3)
    assert argv[-2:] == ["-rebootcount", "3"]
    assert parse_protection(STATUS_OFF) == "off"
    assert parse_protection("Protection Status: On") == "on"
    assert parse_protection("Lock Status: Locked") == "locked"
    assert parse_reboot_count(STATUS_OFF) == 3
    assert protector_ids(PROTECTORS) == ["{11111111-2222-3333-4444-555555555555}"]


def test_on_refuses_boot_writes_and_off_can_proceed(monkeypatch: None = None) -> None:
    import os

    os.environ.pop("MAGIC_BITLOCKER_KEY_ACK", None)
    allowed, reason = boot_writes_allowed("on", has_password_protector=True)
    assert allowed is False
    assert "still On" in reason
    allowed, reason = boot_writes_allowed("off", has_password_protector=True)
    assert allowed is False
    assert "MAGIC_BITLOCKER_KEY_ACK" in reason
    os.environ["MAGIC_BITLOCKER_KEY_ACK"] = "1"
    allowed, _reason = boot_writes_allowed("off", has_password_protector=True)
    assert allowed is True
    os.environ.pop("MAGIC_BITLOCKER_KEY_ACK", None)

    calls: list[list[str]] = []

    def runner(cmd, timeout=90):
        calls.append(list(cmd))
        return 0, STATUS_OFF

    result = suspend_for_window(
        "C:",
        manage="manage-bde.exe",
        reboot_count=3,
        runner=runner,
        status_text=STATUS_OFF,
        protectors_text=PROTECTORS,
    )
    assert calls[0][-2:] == ["-rebootcount", "3"]
    assert result["status"] == "off"
    assert result["boot_writes_allowed"] is False
    os.environ["MAGIC_BITLOCKER_KEY_ACK"] = "1"
    result = suspend_for_window(
        "C:",
        manage="manage-bde.exe",
        reboot_count=3,
        runner=runner,
        status_text=STATUS_OFF,
        protectors_text=PROTECTORS,
    )
    assert result["boot_writes_allowed"] is True
    os.environ.pop("MAGIC_BITLOCKER_KEY_ACK", None)


if __name__ == "__main__":
    test_reboot_count_and_command()
    test_on_refuses_boot_writes_and_off_can_proceed()
    print("bitlocker guard OK")
