"""Signed WinRE rescue plan, opt-in Linux, and arm limits."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.post_op_verify import empty_regen_ledger, save_regen_ledger  # noqa: E402
from engine.rescue_env import bcd_rescue_commands, plan_rescue_environment  # noqa: E402


def _fresh() -> tuple[Path, Path]:
    folder = Path(tempfile.mkdtemp())
    regen = folder / "regen.json"
    save_regen_ledger(empty_regen_ledger(), regen)
    return folder / "esp", regen


def test_signed_winre_is_armed_once_per_budget() -> None:
    esp, regen = _fresh()
    first = plan_rescue_environment(esp_root=esp, recovery_guid="{11111111-2222-3333-4444-555555555555}", regen_ledger=regen)
    assert first["armed"] is True
    assert first["signed_wim_preserved"] is True
    assert first["verdict"] == "GO"
    flat = [" ".join(cmd) for cmd in first["bcd"]]
    assert any("recoveryenabled Yes" in item for item in flat)
    assert any("recoverysequence {11111111-2222-3333-4444-555555555555}" in item for item in flat)
    assert any("bootstatuspolicy DisplayAllFailures" in item for item in flat)
    assert any(item.startswith("bcdedit /bootsequence") for item in flat)
    assert "IgnoreAllFailures" not in " ".join(flat)
    script = Path(first["script"]).read_text(encoding="ascii")
    assert "bcdboot.exe" in script
    assert "post-op-verify-and-rollback" in script
    assert first["linux"]["enabled"] is False
    second = plan_rescue_environment(esp_root=esp, regen_ledger=regen)
    assert second["armed"] is True
    third = plan_rescue_environment(esp_root=esp, regen_ledger=regen)
    assert third["armed"] is False
    assert third["verdict"] == "NO-GO"
    log = Path(first["log"]).read_text(encoding="utf-8")
    assert "rescue armed 1/2" in log
    assert "arm count exhausted" in log
    print("winre OK")


def test_linux_ramdisk_is_opt_in_and_secure_boot_blocks_it() -> None:
    esp, regen = _fresh()
    blocked = plan_rescue_environment(esp_root=esp, secure_boot="on", linux_opt_in=True, regen_ledger=regen)
    assert blocked["linux"]["enabled"] is False
    assert blocked["signed_wim_preserved"] is True
    allowed = plan_rescue_environment(
        esp_root=esp,
        secure_boot="off",
        linux_opt_in=True,
        regen_ledger=regen,
    )
    assert allowed["linux"]["enabled"] is True
    assert allowed["linux"]["writes_disk"] is False
    assert allowed["linux"]["paid_tools"] is False
    injected = plan_rescue_environment(esp_root=Path(tempfile.mkdtemp()) / "esp", inject=True, regen_ledger=regen)
    assert injected["armed"] is False or injected["signed_wim_preserved"] is False
    commands = bcd_rescue_commands(recovery_guid="{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}", one_shot=False)
    assert not any(cmd[1] == "/bootsequence" for cmd in commands)
    print("opt-in OK")


def test_exhausted_regen_ledger_refuses_rescue() -> None:
    esp, regen = _fresh()
    save_regen_ledger({"attempts": 4, "tried": ["bcdboot-uefi"], "fingerprints": [], "started": 1.0, "exhausted": True}, regen)
    report = plan_rescue_environment(esp_root=esp, regen_ledger=regen)
    assert report["verdict"] == "NO-GO"
    assert report["reason"] == "regen-exhausted"
    assert report["signed_wim_preserved"] is True
    print("regen guard OK")


if __name__ == "__main__":
    test_signed_winre_is_armed_once_per_budget()
    test_linux_ramdisk_is_opt_in_and_secure_boot_blocks_it()
    test_exhausted_regen_ledger_refuses_rescue()
    print("ALL rescue env tests passed")
