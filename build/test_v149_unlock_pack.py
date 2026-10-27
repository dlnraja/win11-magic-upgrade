# Smoke tests for full unlock registry pack.
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.unlock_pack import (  # noqa: E402
    MAGIC_UNLOCK,
    _LEGACY_MIGRATION_DWORDS,
    apply_full_unlock_pack,
    apply_win8_migration_pack,
)


def test_migration_pack_has_core_keys():
    names = {n for _, n, _ in _LEGACY_MIGRATION_DWORDS}
    assert "AllowOSUpgrade" in names
    assert "ReservationsAllowed" in names
    assert "AllowOSUpgradeUnlocked" in names
    assert "MediaCenterOffer" in names
    assert "DvdPlayerOffer" in names


def test_magic_unlock_path():
    assert MAGIC_UNLOCK.endswith(r"Win11MagicUpgrade\Unlock")


def test_skip_env():
    with patch.dict("os.environ", {"MAGIC_SKIP_UNLOCK_PACK": "1"}):
        r = apply_full_unlock_pack(None, phase="pre_setup")
    assert r.get("skipped") is True


def test_win8_migration_callable():
    with patch("engine.unlock_pack._set_dword", return_value=True), patch(
        "engine.unlock_pack.save_state"
    ), patch("engine.unlock_pack.log"):
        n = apply_win8_migration_pack(os_family="win81", build=9600)
    assert n >= 1


def test_full_pack_phases_mock():
    report = MagicMock(build=9600, os_family="win81", has_media_center=True)
    report.product_name = "Windows 8.1 Pro with Media Center"
    report.edition_id = "ProfessionalWMC"
    with patch("engine.unlock_pack.apply_win8_migration_pack", return_value=5), patch(
        "engine.unlock_pack.apply_media_center_dvd_unlock", return_value=3
    ), patch("engine.unlock_pack.apply_hardware_unlock", return_value={"ok": True}), patch(
        "engine.unlock_pack.save_state"
    ), patch("engine.unlock_pack.log"):
        r = apply_full_unlock_pack(report, phase="pre_setup")
    assert r["migration"] == 5
    assert r["media_center"] == 3
    assert r["hw"].get("ok") is True


if __name__ == "__main__":
    test_migration_pack_has_core_keys()
    test_magic_unlock_path()
    test_skip_env()
    test_win8_migration_callable()
    test_full_pack_phases_mock()
    print("OK test_v149_unlock_pack")
