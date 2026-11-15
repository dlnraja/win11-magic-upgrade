"""bcdedit must not delete {current} or {default}."""
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

from engine.bcd_safety import denies_bcd_delete, low_risk_loop_steps  # noqa: E402
from engine.proc_guard import run_bounded  # noqa: E402


def test_delete_current_is_refused() -> None:
    assert denies_bcd_delete(["bcdedit", "/delete", "{current}"])
    assert denies_bcd_delete(["bcdedit", "/delete", "{default}"])
    assert denies_bcd_delete(["bcdedit", "/enum", "{current}"]) == ""
    result = run_bounded(["bcdedit", "/delete", "{current}"], popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert result.ok is False
    assert "refusing" in result.reason
    steps = " ".join(low_risk_loop_steps()).lower()
    assert "safeboot" in steps
    assert "revertpendingactions" in steps
    assert "/delete" not in steps


if __name__ == "__main__":
    test_delete_current_is_refused()
    print("bcd safety OK")
