"""Logic checks for ISO mount error 183 handling (no real mount)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.virtdisk import (  # noqa: E402
    ERROR_ALREADY_EXISTS,
    ERROR_SHARING_VIOLATION,
    _is_setup_root,
)


def test_error_codes() -> None:
    assert ERROR_ALREADY_EXISTS == 183
    assert ERROR_SHARING_VIOLATION == 32
    print("error codes OK")


def test_is_setup_root_missing() -> None:
    assert _is_setup_root("Z:\\this_drive_should_not_exist_magic_183\\") is False
    print("is_setup_root missing OK")


if __name__ == "__main__":
    test_error_codes()
    test_is_setup_root_missing()
    print("ALL virtdisk logic tests passed")
