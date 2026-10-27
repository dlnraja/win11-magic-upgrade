# Smoke tests for app license/settings carry.
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.app_carry import (  # noqa: E402
    CARRY_PROFILES,
    apply_app_carry,
    capture_app_carry,
    capture_windows_product_key_local,
)
from engine.sanitize import sanitize_obj, sanitize_text  # noqa: E402
from engine.support_zip import EXCLUDE_PREFIXES, INCLUDE_NAMES  # noqa: E402


def test_profiles_cover_soft_blockers():
    for cid in ("acronis", "easeus_todo", "macrium", "aomei", "logitech_lgs", "nordvpn"):
        assert cid in CARRY_PROFILES
        assert CARRY_PROFILES[cid].reg_paths


def test_skip_env():
    with patch.dict("os.environ", {"MAGIC_SKIP_APP_CARRY": "1"}):
        r = capture_app_carry("acronis", display_name="Acronis")
        assert r.get("ok") is False
        a = apply_app_carry("acronis")
        assert a.get("ok") is False


def test_sanitize_redacts_keys():
    assert "<PRODUCTKEY>" in sanitize_text("key XXXXX-XXXXX-XXXXX-XXXXX-XXXXX done")
    obj = sanitize_obj({"license": "SECRET-12345", "PartialKey": "ABCDE", "build": 22631})
    assert obj["license"] == "<REDACTED>"
    assert obj["PartialKey"] == "<REDACTED>"
    assert obj["build"] == 22631


def test_support_zip_excludes_app_carry():
    assert not any(n.startswith("app-carry") for n in INCLUDE_NAMES)
    assert any("app-carry" in p for p in EXCLUDE_PREFIXES)


def test_windows_key_callable():
    with patch("engine.app_carry.save_state"), patch(
        "engine.activation.snapshot_activation", return_value={}
    ):
        out = capture_windows_product_key_local()
    assert "key_present" in out


def test_capture_unknown_id():
    with patch("engine.app_carry.save_state"):
        r = capture_app_carry("unknown_vendor_xyz")
    assert r.get("ok") is False


if __name__ == "__main__":
    test_profiles_cover_soft_blockers()
    test_skip_env()
    test_sanitize_redacts_keys()
    test_support_zip_excludes_app_carry()
    test_windows_key_callable()
    test_capture_unknown_id()
    print("OK test_v148_app_carry")
