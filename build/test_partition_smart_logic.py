"""Logic checks for partition_smart parameter validation + planner (no disk mutation)."""
from __future__ import annotations

import ctypes
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

if not hasattr(ctypes, "windll"):
    class _Dll:
        def __getattr__(self, _name):
            return self

        def __call__(self, *_a, **_k):
            return self

    ctypes.windll = _Dll()  # type: ignore[attr-defined]

if "winreg" not in sys.modules:
    try:
        import winreg  # noqa: F401
    except ModuleNotFoundError:
        _winreg = types.ModuleType("winreg")
        _winreg.HKEY_LOCAL_MACHINE = 1

        def _open_key(*_a, **_k):
            raise OSError("winreg stub")

        _winreg.OpenKey = _open_key
        _winreg.QueryValueEx = lambda *_a, **_k: (0, 1)
        _winreg.CloseKey = lambda *_a, **_k: None
        sys.modules["winreg"] = _winreg

from engine.diskpart_safe import lint_diskpart_script  # noqa: E402
from engine.partition_smart import (  # noqa: E402
    MAX_BOOT_CREATE_MB,
    TARGET_BOOT_MB,
    _letter_root,
    _norm_letter,
    _valid_disk,
    _valid_mb,
    _valid_part,
    bounded_esp_size_mb,
    efi_create_diskpart_script,
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
    add = plan["steps"][0]["add_mb"]
    assert 16 <= add <= 99, add
    assert add <= 10 * 1024, add
    print("plan extend OK")


def test_plan_extend_huge_free_capped() -> None:
    """Adjacent free of 50 GB must NOT grow boot by 50 GB — smart +10 GB cap."""
    from engine.partition_smart import MAX_BOOT_GROW_MB, smart_boot_grow_mb

    assert smart_boot_grow_mb(50_000, free_mb=50_000, current_mb=100, target_mb=512) <= MAX_BOOT_GROW_MB
    assert smart_boot_grow_mb(50_000, free_mb=50_000, current_mb=100, target_mb=512) <= 512
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
                "offset": 60 * 1024 ** 3,
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
        "free_regions": [
            {
                "offset": 101 * 1024 * 1024,
                "size": 50 * 1024 ** 3,
                "size_mb": 50 * 1024,
                "before_part": 2,
            }
        ],
    }
    plan = plan_smart_layout(layout, prefer_uefi=True, target_mb=512)
    assert plan["strategy"] == "extend_boot", plan
    add = plan["steps"][0]["add_mb"]
    assert add <= MAX_BOOT_GROW_MB, add
    assert add <= 512, f"should only grow toward target, got {add}"
    assert plan["steps"][0].get("cap_mb") == MAX_BOOT_GROW_MB
    print("plan extend huge free capped OK")


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


def test_efi_create_size_is_not_the_whole_disk() -> None:
    """Rufus 4.16 fixed "Write as ESP" using the whole drive instead of 1 GB.

    Our ESP scripts always pass an explicit size, capped at MAX_BOOT_CREATE_MB,
    and smaller than the disk when that size is known. A bare
    ``create partition efi`` (diskpart's whole-remaining-space form) is rejected.
    """
    disk_mb = 2 * 1024 * 1024
    size = bounded_esp_size_mb(disk_mb, disk_mb=disk_mb)
    assert size == MAX_BOOT_CREATE_MB
    assert size < disk_mb
    assert bounded_esp_size_mb(512) == 512
    assert bounded_esp_size_mb(None) == TARGET_BOOT_MB
    assert bounded_esp_size_mb(0) == TARGET_BOOT_MB
    script = efi_create_diskpart_script(0, disk_mb, "S", disk_mb=disk_mb, detail=True)
    assert f"create partition efi size={size}" in script
    assert "create partition efi\n" not in script
    assert lint_diskpart_script(script, style="GPT") == []
    bare = "select disk 0\ncreate partition efi\nformat fs=fat32 quick label=ESP\nassign letter=S\nexit"
    assert lint_diskpart_script(bare, style="GPT")
    small_disk = bounded_esp_size_mb(512, disk_mb=400)
    assert small_disk == 399
    assert small_disk < 400
    print("efi size cap OK")


if __name__ == "__main__":
    test_validators()
    test_plan_extend()
    test_plan_extend_huge_free_capped()
    test_plan_shrink_c()
    test_plan_noop()
    test_efi_create_size_is_not_the_whole_disk()
    print("ALL partition_smart logic checks passed")
