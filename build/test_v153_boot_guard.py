# Smoke tests for autonomous boot guard.
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.boot_guard import (  # noqa: E402
    LINUX_EFI_VENDORS,
    arm_boot_safety_net,
    verify_and_heal_boot,
    write_autoheal_scripts,
)


def test_linux_vendors():
    assert "ubuntu" in LINUX_EFI_VENDORS
    assert "grub" in LINUX_EFI_VENDORS


def test_skip_env():
    with patch.dict(os.environ, {"MAGIC_SKIP_BOOT_GUARD": "1"}):
        r = arm_boot_safety_net()
    assert r.get("skipped") is True


def test_autoheal_scripts():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        with patch("engine.boot_guard.STATE_DIR", base), patch(
            "engine.boot_guard.Path.home", return_value=base
        ):
            (base / "Desktop").mkdir()
            paths = write_autoheal_scripts()
        assert "autoheal" in paths
        assert Path(paths["autoheal"]).is_file()
        text = Path(paths["autoheal"]).read_text(encoding="utf-8")
        assert "bcdboot" in text
        assert "bootrec" in text


def test_heal_skip():
    with patch.dict(os.environ, {"MAGIC_SKIP_BOOT_GUARD": "1"}):
        r = verify_and_heal_boot(force=False)
    assert r.get("skipped") is True


if __name__ == "__main__":
    test_linux_vendors()
    test_skip_env()
    test_autoheal_scripts()
    test_heal_skip()
    print("OK test_v153_boot_guard")
