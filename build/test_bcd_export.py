"""The live BCD hive is exported, not copied."""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
try:
    import winreg  # noqa: F401
except ImportError:
    pkg = types.ModuleType("engine")
    pkg.__path__ = [str(ROOT / "python" / "engine")]  # type: ignore[attr-defined]
    sys.modules["engine"] = pkg

from engine.boot_partition_backup import export_file_ok, live_bcd_copy_action  # noqa: E402


def test_live_hive_is_not_copied_when_export_works() -> None:
    assert live_bcd_copy_action("BCD", export_ok=True, offline=False) == "skip-info"
    assert live_bcd_copy_action("BCD.LOG", export_ok=True, offline=False) == "skip-info"
    assert live_bcd_copy_action("BCD.LOG1", export_ok=False, offline=False) == "skip-warn"
    assert live_bcd_copy_action("BCD", export_ok=False, offline=True) == "copy"
    assert live_bcd_copy_action("bootmgfw.efi", export_ok=True, offline=False) == "copy"


def test_export_must_be_nonempty() -> None:
    folder = Path(__file__).resolve().parent / "_bcd_export_tmp"
    folder.mkdir(exist_ok=True)
    empty = folder / "empty"
    empty.write_bytes(b"")
    good = folder / "good"
    good.write_bytes(b"BCD")
    try:
        assert export_file_ok(empty) is False
        assert export_file_ok(good) is True
        assert export_file_ok(folder / "missing") is False
    finally:
        empty.unlink()
        good.unlink()
        folder.rmdir()


if __name__ == "__main__":
    test_live_hive_is_not_copied_when_export_works()
    test_export_must_be_nonempty()
    print("bcd export tests OK")
