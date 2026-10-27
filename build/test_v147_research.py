# Smoke tests for research pack modules.
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.profile_heal import ProfileAudit, audit_profile_list  # noqa: E402
from engine.rollback_scan import scan_rollback_folder  # noqa: E402
from engine.preflight_power import on_ac_power  # noqa: E402


def test_ideas_200_exists():
    p = ROOT / "docs" / "IDEAS_200.md"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert text.count("| ") >= 200
    assert "SetupDiag" in text or "CompatData" in text


def test_profile_audit_runs():
    with patch("engine.profile_heal.save_state"):
        a = audit_profile_list(apply_bak_rename=False)
    assert isinstance(a, ProfileAudit)
    assert a.entries >= 0


def test_rollback_scan_safe():
    with patch("engine.rollback_scan.save_state"):
        r = scan_rollback_folder()
    assert "present" in r


def test_ac_power_callable():
    # May be True/False/None depending on host
    v = on_ac_power()
    assert v is None or isinstance(v, bool)


if __name__ == "__main__":
    test_ideas_200_exists()
    test_profile_audit_runs()
    test_rollback_scan_safe()
    test_ac_power_callable()
    print("OK test_v147_research")
