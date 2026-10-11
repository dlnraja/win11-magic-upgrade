# Smoke tests for boot remediation failover bot.
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.boot_remediation import (  # noqa: E402
    run_boot_auto_remediate,
    write_remediation_scripts,
)
from gui_panel import OPTIONS  # noqa: E402


def test_gui_has_boot_options():
    keys = {o.key for o in OPTIONS}
    assert "boot_auto_fix" in keys


def test_write_scripts():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        desk = base / "Desktop"
        desk.mkdir()
        with patch("engine.boot_remediation.REMEDIATE_DIR", base / "remediation"), patch(
            "engine.boot_remediation.Path.home", return_value=base
        ):
            paths = write_remediation_scripts(system_disk=0, mode="test")
        assert "winre_bcdboot" in paths
        assert (base / "remediation" / "WinRE-bcdboot-heal.cmd").is_file()


def test_skip_env():
    with patch.dict(os.environ, {"MAGIC_SKIP_BOOT_REMEDIATE": "1"}):
        r = run_boot_auto_remediate(None, write_scripts=False)
    assert r.get("skipped") is True


def test_bot_tiers_mocked():
    report = MagicMock(
        disk_number=0,
        partition_style="GPT",
        is_uefi=True,
        firmware_likely_ia32=False,
        mbr2gpt_available=False,
        boot_strategy="",
    )
    report.as_dict.return_value = {}
    with patch("engine.boot_remediation.write_remediation_scripts", return_value={}), patch(
        "engine.autonomy.apply_autonomous_remediations", return_value={}
    ), patch(
        "engine.boot_safe.run_esp_srp_with_restore",
        return_value={"ok": True, "bootable": True},
    ), patch(
        "engine.boot_safe.validated_repair_boot_manager", return_value={"ok": True}
    ), patch(
        "engine.boot_safe.guarantee_bootable",
        return_value={"bootable": True},
    ), patch("engine.boot_remediation.save_state"), patch("engine.boot_remediation.log"):
        r = run_boot_auto_remediate(report, force_esp=True, try_mbr2gpt=False, write_scripts=True)
    assert r.get("ok") is True
    assert any(t.get("tier") == "esp_srp_smart" for t in r.get("tiers", []))


if __name__ == "__main__":
    test_gui_has_boot_options()
    test_write_scripts()
    test_skip_env()
    test_bot_tiers_mocked()
    print("OK test_v151_boot_remediation")
