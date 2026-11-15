"""Windows.old stays until the user opts in to a 2-60 day window."""
from __future__ import annotations

import os
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

from engine.rollback_window import apply_keep_windows_old, clamp_days, plan_keep_windows_old, set_window_argv  # noqa: E402


def test_window() -> None:
    os.environ.pop("MAGIC_KEEP_WINDOWS_OLD", None)
    assert clamp_days(1) == 2
    assert clamp_days(90) == 60
    assert set_window_argv(30)[-1] == "/Value:30"
    plan = plan_keep_windows_old(days=30)
    assert plan["apply"] is False
    assert "Windows.old" in plan["warning"]
    calls: list[list[str]] = []

    def runner(cmd, timeout=120):
        calls.append(list(cmd))
        return 0, "ok"

    skipped = apply_keep_windows_old(runner, days=30)
    assert skipped["ran"] is False
    assert calls == []
    os.environ["MAGIC_KEEP_WINDOWS_OLD"] = "1"
    applied = apply_keep_windows_old(runner, days=30)
    assert applied["ran"] is True
    assert calls[0][1:3] == ["/Online", "/Set-OSUninstallWindow"]
    os.environ.pop("MAGIC_KEEP_WINDOWS_OLD", None)


if __name__ == "__main__":
    test_window()
    print("rollback window OK")
