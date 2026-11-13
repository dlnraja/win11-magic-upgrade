"""Windows 11 One-Click stays on the least invasive in-place path."""
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

from engine.win11_inplace_policy import (  # noqa: E402
    classify_setup_failure,
    decide_oneclick,
    next_remediation,
    official_setup_args,
    run_inplace_attempts,
)

TARGET = dict(target_version="26H2", target_build=26300, target_ubr=9725)


def test_windows_10_keeps_full_migration() -> None:
    plan = decide_oneclick(build=19045, ubr=3803, display_version="22H2", **TARGET)
    assert plan.path == "full_migration"
    assert plan.allow_bypass and plan.allow_mbr2gpt and plan.allow_esp and plan.allow_boot_fix


def test_already_latest_is_health_check_only() -> None:
    current = decide_oneclick(build=26300, ubr=9725, display_version="26H2", **TARGET)
    newer = decide_oneclick(build=26300, ubr=9900, display_version="26H2", **TARGET)
    device = decide_oneclick(build=28000, ubr=3237, display_version="26H1", **TARGET)
    assert current.path == "health_check_only"
    assert newer.path == "health_check_only"
    assert device.path == "health_check_only"
    for plan in (current, newer, device):
        assert not plan.allow_bypass
        assert not plan.allow_mbr2gpt
        assert not plan.allow_esp
        assert not plan.allow_boot_fix


def test_behind_win11_is_official_inplace_only() -> None:
    feature = decide_oneclick(build=26100, ubr=1000, display_version="24H2", **TARGET)
    cumulative = decide_oneclick(build=26300, ubr=1000, display_version="26H2", **TARGET)
    assert feature.path == "inplace_least"
    assert cumulative.path == "cumulative_update"
    for plan in (feature, cumulative):
        assert not plan.allow_bypass
        assert not plan.allow_mbr2gpt
        assert not plan.allow_esp
        assert not plan.allow_boot_fix
    first = official_setup_args()
    assert first[:2] == ["/auto", "upgrade"]
    assert "IgnoreWarning" not in first
    assert "/Product" not in first


def test_failure_picks_one_matching_retry() -> None:
    assert classify_setup_failure(1, "Setup failed 0xC1900200") == "compat"
    assert classify_setup_failure(1, "0xC1900202 hardware block") == "compat"
    assert classify_setup_failure(1, "0xC1900101 driver rollback") == "driver"
    assert classify_setup_failure(1, "0xC1900101-0x20017 boot configuration") == "boot"
    assert classify_setup_failure(1, r"C:\$WINDOWS.~BT\Sources\Rollback\setuperr.log") == "unknown"
    assert next_remediation("compat", used=set(), attempt=1) == "compat"
    assert next_remediation("compat", used={"compat"}, attempt=1) is None
    assert next_remediation("unknown", used=set(), attempt=1) is None
    calls: list[list[str]] = []
    steps: list[str] = []

    def launch(args: list[str]) -> int:
        calls.append(args)
        if len(calls) == 1:
            return 1
        return 0

    code = run_inplace_attempts(
        launch=launch,
        read_text=lambda: "0xC1900200",
        remediate=steps.append,
    )
    assert code == 0
    assert steps == ["compat"]
    assert "IgnoreWarning" not in calls[0]
    assert "IgnoreWarning" in calls[1]
    assert "/Product" not in calls[1]


def main() -> None:
    test_windows_10_keeps_full_migration()
    test_already_latest_is_health_check_only()
    test_behind_win11_is_official_inplace_only()
    test_failure_picks_one_matching_retry()
    print("test_win11_inplace_policy ok")


if __name__ == "__main__":
    main()
