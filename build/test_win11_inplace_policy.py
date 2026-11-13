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
    decide_noargs_startup,
    next_countdown,
    startup_countdown_seconds,
    decide_oneclick,
    next_remediation,
    official_setup_args,
    run_inplace_attempts,
)

TARGET = dict(target_version="26H2", target_build=26300, target_ubr=9725)


def test_older_windows_starts_official_too() -> None:
    hosts = (
        (7601, "Windows 7"),
        (9200, "Windows 8"),
        (9600, "Windows 8.1"),
        (19045, "22H2"),
        (10240, "1507"),
    )
    for build, label in hosts:
        plan = decide_oneclick(build=build, ubr=1, display_version=label, **TARGET)
        assert plan.path == "inplace_least", label
        assert not plan.allow_bypass
        assert not plan.allow_mbr2gpt
        assert not plan.allow_esp
        assert not plan.allow_boot_fix


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


def test_noargs_win11_go_starts_official_update() -> None:
    go = decide_noargs_startup(
        is_win11=True,
        guard_go=True,
        issues=[],
        update_path="inplace_least",
    )
    assert go.action == "official_update"
    assert go.countdown is True
    assert go.allow_bypass is False
    cumulative = decide_noargs_startup(
        is_win11=True,
        guard_go=True,
        update_path="cumulative_update",
    )
    assert cumulative.action == "official_update"
    assert cumulative.allow_bypass is False


def test_noargs_repairs_until_go_and_honors_opt_out() -> None:
    repair = decide_noargs_startup(
        is_win11=True,
        guard_go=False,
        issues=["esp_bcd_missing"],
        update_path="inplace_least",
    )
    assert repair.action == "repair"
    assert repair.countdown is False
    blocked = decide_noargs_startup(
        is_win11=True,
        guard_go=True,
        issues=["efi bootmgfw missing"],
        update_path="inplace_least",
    )
    assert blocked.action == "repair"
    opted = decide_noargs_startup(
        is_win11=True,
        guard_go=True,
        opt_out=True,
        update_path="inplace_least",
    )
    assert opted.action == "idle"
    latest = decide_noargs_startup(is_win11=True, guard_go=True, update_path="health_check_only")
    assert latest.action == "health"
    older = decide_noargs_startup(is_win11=False, guard_go=True, update_path="inplace_least")
    assert older.action == "idle"
    healed = decide_noargs_startup(
        is_win11=True,
        guard_go=True,
        issues=[],
        update_path="inplace_least",
        repaired=True,
        error_count=0,
    )
    assert healed.action == "official_update"
    assert healed.countdown is True
    assert healed.allow_bypass is False
    failed = decide_noargs_startup(
        is_win11=True,
        guard_go=False,
        issues=["bcd_bootmgr_missing"],
        update_path="inplace_least",
        repaired=True,
        error_count=1,
    )
    assert failed.action == "nogo"
    assert failed.countdown is False
    still_wrong = decide_noargs_startup(
        is_win11=True,
        guard_go=True,
        issues=[],
        update_path="inplace_least",
        repaired=True,
        error_count=1,
    )
    assert still_wrong.action == "nogo"


def test_countdown_defaults_to_20_and_can_cancel() -> None:
    assert startup_countdown_seconds(None) == 20 or startup_countdown_seconds("20") == 20
    assert startup_countdown_seconds("20") == 20
    assert startup_countdown_seconds("0") == 1
    assert startup_countdown_seconds("nope") == 20
    assert next_countdown(20) == (19, "wait")
    assert next_countdown(1) == (0, "run")
    assert next_countdown(8, start_now=True) == (0, "run")
    assert next_countdown(8, cancel=True) == (8, "cancel")


def test_failure_picks_one_matching_retry() -> None:
    assert classify_setup_failure(1, "Setup failed 0xC1900200") == "compat"
    assert classify_setup_failure(1, "0xC1900202 hardware block") == "compat"
    assert classify_setup_failure(1, "0xC1900101 driver rollback") == "driver"
    assert classify_setup_failure(1, "0xC1900101-0x20017 boot configuration") == "boot"
    assert classify_setup_failure(1, r"C:\$WINDOWS.~BT\Sources\Rollback\setuperr.log") == "unknown"
    assert next_remediation("compat", used=set(), attempt=1) == ("requirements_bypass",)
    assert next_remediation("compat", used={"compat"}, attempt=1) is None
    assert next_remediation("unknown", used=set(), attempt=1) is None
    assert next_remediation("boot", used=set(), attempt=1, architecture="uefi") == (
        "esp_repair",
        "bcdboot_uefi",
        "bcd_fix",
    )
    assert next_remediation("boot", used=set(), attempt=1, architecture="bios") == (
        "srp_active",
        "bcdboot_bios",
        "bootsect_nt60",
    )
    hybrid = next_remediation("boot", used=set(), attempt=1, architecture="hybrid")
    assert hybrid is not None
    assert "bcdboot_uefi" in hybrid and "bootsect_nt60" in hybrid
    assert next_remediation("boot", used=set(), attempt=1, architecture="unknown") is None
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
        remediate=lambda item: steps.append(item.cause),
    )
    assert code == 0
    assert steps == ["compat"]
    assert "IgnoreWarning" not in calls[0]
    assert "IgnoreWarning" in calls[1]
    assert "/Product" not in calls[1]

    boot_actions: list[tuple[str, ...]] = []
    run_inplace_attempts(
        launch=lambda _args: 1,
        read_text=lambda: "UEFI bootmgfw EFI\\Microsoft\\Boot\\BCD missing",
        remediate=lambda item: boot_actions.append(item.actions),
        firmware="UEFI",
    )
    assert boot_actions[0] == ("esp_repair", "bcdboot_uefi", "bcd_fix")
    assert "bcdboot_bios" not in boot_actions[0]


def main() -> None:
    test_older_windows_starts_official_too()
    test_already_latest_is_health_check_only()
    test_behind_win11_is_official_inplace_only()
    test_noargs_win11_go_starts_official_update()
    test_noargs_repairs_until_go_and_honors_opt_out()
    test_countdown_defaults_to_20_and_can_cancel()
    test_failure_picks_one_matching_retry()
    print("test_win11_inplace_policy ok")


if __name__ == "__main__":
    main()
