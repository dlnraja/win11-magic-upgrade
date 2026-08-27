"""Unit checks for disk # parsing (FR Disque / EN Disk)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.diskpart_safe import disk_number_from_detail  # noqa: E402


def test_en() -> None:
    assert disk_number_from_detail("Disk #: 0\nType : NTFS") == 0
    assert disk_number_from_detail("Disk # : 1\n") == 1
    assert disk_number_from_detail("  Disk: 2\n") == 2
    print("EN OK")


def test_fr_disque() -> None:
    # Real FR diskpart uses "Disque" — old regex Disk(?:e)? missed this.
    assert disk_number_from_detail("Disque n° : 0\nType : NTFS") == 0
    assert disk_number_from_detail("Disque #: 0\n") == 0
    assert disk_number_from_detail("Disque : 1\n") == 1
    assert disk_number_from_detail("Disque###  0\n") == 0
    assert disk_number_from_detail("  Disque n°0\n") == 0
    print("FR Disque OK")


def test_no_match() -> None:
    assert disk_number_from_detail("Volume ### Ltr Label") is None
    print("no match OK")


if __name__ == "__main__":
    test_en()
    test_fr_disque()
    test_no_match()
    print("ALL diskpart parse tests passed")
