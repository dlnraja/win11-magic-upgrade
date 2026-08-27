"""Logic checks for partition_smart parameter validation + planner (no disk mutation)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.partition_smart import (  # noqa: E402
    _letter_root,
    _norm_letter,
    _valid_disk,
    _valid_mb,
    _valid_part,
    plan_smart_layout,
)


def test_validators() -> None:
    assert _norm_letter("y") == "Y"
    assert _norm_letter("Y:") == "Y"
    assert _norm_letter("Y:\\") == "Y"
    assert _norm_letter("") is None
    assert _norm_letter("C:Windows") is None
    assert _letter_root("s") == "S:"
    assert _valid_disk(0) == 0
    assert _valid_disk(-1) is None
    assert _valid_disk("2") == 2
    assert _valid_part(0) is None
    assert _valid_part(1) == 1
    assert _valid_mb(512) == 512
    assert _valid_mb(0) is None
    assert _valid_mb(99999) is None
    print("validators OK")


def test_plan_extend() -> None:
    layout = {
        "ok": True,
        "disk": 0,
        "style": "GPT",
        "partitions": [
            {
                "number": 1,
                "offset": 1024 * 1024,
                "size": 100 * 1024 * 1024,
                "size_mb": 100,
                "free_mb": 10,
                "type": "System",
                "gpt": "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}",
                "letter": "",
                "fs": "FAT32",
                "is_system": True,
                "is_boot": False,
                "is_active": False,
                "is_esp": True,
            },
            {
                "number": 2,
                "offset": 200 * 1024 * 1024,
                "size": 100 * 1024 ** 3,
                "size_mb": 102400,
                "free_mb": 50000,
                "type": "Basic",
                "gpt": "",
                "letter": "C",
                "fs": "NTFS",
                "is_system": False,
                "is_boot": True,
                "is_active": False,
                "is_esp": False,
            },
        ],
        # free between ESP end (101MB) and C start (200MB) ~= 99MB adjacent after boot
        "free_regions": [
            {
                "offset": 101 * 1024 * 1024,
                "size": 99 * 1024 * 1024,
                "size_mb": 99,
                "before_part": 2,
            }
        ],
    }
    plan = plan_smart_layout(layout, prefer_uefi=True, target_mb=512)
    assert plan["strategy"] == "extend_boot", plan
    assert plan["steps"][0]["op"] == "extend"
    assert plan["steps"][0]["part"] == 1
    print("plan extend OK")


def test_plan_shrink_c() -> None:
    layout = {
        "ok": True,
        "disk": 1,
        "style": "GPT",
        "partitions": [
            {
                "number": 1,
                "offset": 1024 * 1024,
                "size": 100 * 1024 * 1024,
                "size_mb": 100,
                "free_mb": 5,
                "type": "System",
                "gpt": "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}",
                "letter": "",
                "fs": "FAT32",
                "is_system": True,
                "is_boot": False,
                "is_active": False,
                "is_esp": True,
            },
            {
                "number": 2,
                "offset": 101 * 1024 * 1024,
                "size": 200 * 1024 ** 3,
                "size_mb": 204800,
                "free_mb": 40000,
                "type": "Basic",
                "gpt": "",
                "letter": "C",
                "fs": "NTFS",
                "is_system": False,
                "is_boot": True,
                "is_active": False,
                "is_esp": False,
            },
        ],
        "free_regions": [],
    }
    plan = plan_smart_layout(layout, prefer_uefi=True, target_mb=512)
    assert plan["strategy"] == "shrink_c_then_create", plan
    assert plan["steps"][0]["letter"] == "C"
    assert plan["steps"][0]["mb"] == 552
    print("plan shrink_c OK")


def test_plan_noop() -> None:
    layout = {
        "ok": True,
        "disk": 0,
        "style": "GPT",
        "partitions": [
            {
                "number": 1,
                "offset": 1024 * 1024,
                "size": 512 * 1024 * 1024,
                "size_mb": 512,
                "free_mb": 200,
                "type": "System",
                "gpt": "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}",
                "letter": "",
                "fs": "FAT32",
                "is_system": True,
                "is_boot": False,
                "is_active": False,
                "is_esp": True,
            },
            {
                "number": 2,
                "offset": 600 * 1024 * 1024,
                "size": 100 * 1024 ** 3,
                "size_mb": 102400,
                "free_mb": 20000,
                "type": "Basic",
                "gpt": "",
                "letter": "C",
                "fs": "NTFS",
                "is_system": False,
                "is_boot": True,
                "is_active": False,
                "is_esp": False,
            },
        ],
        "free_regions": [],
    }
    plan = plan_smart_layout(layout, prefer_uefi=True)
    assert plan["strategy"] == "noop_space_ok", plan
    print("plan noop OK")


if __name__ == "__main__":
    test_validators()
    test_plan_extend()
    test_plan_shrink_c()
    test_plan_noop()
    print("ALL partition_smart logic checks passed")
