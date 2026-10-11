# Unit tests: blocker catalog matching + soft-uninstall cmd builder (no disk I/O uninstall).
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.blocker_apps import (  # noqa: E402
    CATALOG,
    InstalledMatch,
    _build_soft_uninstall_cmd,
    match_installed_blockers,
)


def test_catalog_has_core_products():
    ids = {e.id for e in CATALOG}
    assert "acronis" in ids
    assert "easeus_todo" in ids
    assert "logitech_lgs" in ids
    assert "macrium" in ids
    lgs = next(e for e in CATALOG if e.id == "logitech_lgs")
    assert lgs.winget_successor == "Logitech.GHUB"
    assert lgs.soft_uninstall is True
    edr = next(e for e in CATALOG if e.id == "crowdstrike")
    assert edr.soft_uninstall is False


def test_match_from_fake_registry():
    fake = [
        {
            "display_name": "Logitech Gaming Software",
            "publisher": "Logitech",
            "version": "9.04",
            "uninstall_string": r'"C:\Program Files\Logitech\Uninstall.exe" /S',
            "quiet_uninstall": "",
            "install_location": "",
        },
        {
            "display_name": "EaseUS Todo Backup",
            "publisher": "EaseUS",
            "version": "17.5",
            "uninstall_string": "msiexec.exe /X{12345678-1234-1234-1234-1234567890AB}",
            "quiet_uninstall": "",
            "install_location": "",
        },
        {
            "display_name": "Notepad++",
            "publisher": "Don",
            "version": "8",
            "uninstall_string": "",
            "quiet_uninstall": "",
            "install_location": "",
        },
    ]
    with patch("engine.blocker_apps._enum_uninstall_entries", return_value=fake):
        matches = match_installed_blockers()
    ids = {m.catalog_id for m in matches}
    assert "logitech_lgs" in ids
    assert "easeus_todo" in ids
    assert len(matches) == 2


def test_msi_soft_uninstall_cmd():
    m = InstalledMatch(
        catalog_id="easeus_todo",
        display_name="EaseUS Todo Backup",
        uninstall_string="msiexec.exe /X{ABCDEF12-3456-7890-ABCD-EF1234567890}",
        soft_uninstall=True,
    )
    cmd = _build_soft_uninstall_cmd(m)
    assert cmd is not None
    assert cmd[0].lower().startswith("msiexec")
    assert "/X" in cmd
    assert "/qn" in cmd


if __name__ == "__main__":
    test_catalog_has_core_products()
    test_match_from_fake_registry()
    test_msi_soft_uninstall_cmd()
    print("OK test_v143_blocker_apps")
