# Smoke tests for GUI options + frameworks module.
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.frameworks import FRAMEWORK_JOBS, job_selected, selected_framework_ids  # noqa: E402
from gui_panel import OPTIONS, apply_options_to_environ, load_option_values  # noqa: E402


def test_options_cover_frameworks_and_migration():
    keys = {o.key for o in OPTIONS}
    assert "soft_uninstall" in keys
    assert "unlock_pack" in keys
    assert "fw_directx" in keys
    assert "fw_vcredist" in keys
    assert "fw_netfx35" in keys


def test_apply_options_sets_env():
    vals = {o.key: o.default for o in OPTIONS}
    vals["fw_master"] = True
    vals["fw_directx"] = True
    vals["fw_netfx35"] = False
    vals["soft_uninstall"] = True
    with patch.dict(os.environ, {}, clear=False):
        apply_options_to_environ(vals)
        assert os.environ.get("MAGIC_INSTALL_DIRECTX") == "1"
        assert os.environ.get("MAGIC_INSTALL_NETFX35") == "0"
        assert "MAGIC_SKIP_SOFT_UNINSTALL" not in os.environ or os.environ.get(
            "MAGIC_SKIP_SOFT_UNINSTALL"
        ) != "1"


def test_framework_jobs():
    ids = {j.id for j in FRAMEWORK_JOBS}
    assert "directx" in ids
    assert "vcredist" in ids
    assert "netfx35" in ids


def test_job_selected_respects_zero():
    with patch.dict(
        os.environ,
        {
            "MAGIC_INSTALL_FRAMEWORKS": "1",
            "MAGIC_INSTALL_DIRECTX": "0",
            "MAGIC_SKIP_FRAMEWORKS": "",
        },
        clear=False,
    ):
        os.environ.pop("MAGIC_SKIP_FRAMEWORKS", None)
        dx = next(j for j in FRAMEWORK_JOBS if j.id == "directx")
        assert job_selected(dx) is False


def test_load_defaults():
    d = load_option_values()
    assert isinstance(d, dict)
    assert "fw_master" in d


if __name__ == "__main__":
    test_options_cover_frameworks_and_migration()
    test_apply_options_sets_env()
    test_framework_jobs()
    test_job_selected_respects_zero()
    test_load_defaults()
    print("OK test_v150_gui_frameworks")
