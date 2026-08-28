# Unit tests for setup_recovery code parsing (no Windows Setup required).
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.setup_recovery import SUBCODE_ACTIONS, TOP_CODE_ACTIONS, _CODE_RE  # noqa: E402


def test_code_regex():
    sample = """
    Result: 0xC1900101 - 0x20017
    Also saw 0xC1900208 and 0x80070070
    MIGRATE_DATA failed 0x8007042B-0x2000D
    bare code 8007042b in SetupDiag
    """
    found = []
    for m in _CODE_RE.finditer(sample):
        found.append(tuple(g for g in m.groups() if g))
    assert any("0x20017" in str(t).lower() for t in found), found
    assert any("0xC1900208" in str(t) for t in found) or any(
        "c1900208" in str(t).lower() for t in found
    ), found
    assert any("8007042" in str(t).lower() for t in found), found


def test_action_tables():
    assert "0x20017" in SUBCODE_ACTIONS
    assert "0xc1900208" in TOP_CODE_ACTIONS
    assert "0x8007042b" in TOP_CODE_ACTIONS
    assert "0x2000d" in SUBCODE_ACTIONS
    assert "BitLocker" in SUBCODE_ACTIONS["0x20017"]["action"] or "storage" in SUBCODE_ACTIONS[
        "0x20017"
    ]["action"].lower()


if __name__ == "__main__":
    test_code_regex()
    test_action_tables()
    print("OK test_setup_recovery_logic")
