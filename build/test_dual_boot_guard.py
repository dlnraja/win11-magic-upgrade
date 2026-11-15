"""Dual-boot repair keeps GRUB's firmware order and does not opt out of SBAT."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
if "engine" not in sys.modules:
    pkg = types.ModuleType("engine")
    pkg.__path__ = [str(ROOT / "python" / "engine")]  # type: ignore[attr-defined]
    pkg.__package__ = "engine"
    sys.modules["engine"] = pkg

from engine.dual_boot_guard import (  # noqa: E402
    grub_recovery_note,
    preserve_firmware_order,
    sbat_opt_out_enabled,
)


def test_linux_preserves_order() -> None:
    os.environ.pop("MAGIC_LINUX_EFI", None)
    os.environ.pop("MAGIC_SBAT_OPTOUT", None)
    assert preserve_firmware_order([]) is False
    assert preserve_firmware_order(["ubuntu"]) is True
    assert sbat_opt_out_enabled() is False
    os.environ["MAGIC_SBAT_OPTOUT"] = "1"
    assert sbat_opt_out_enabled() is True
    os.environ.pop("MAGIC_SBAT_OPTOUT", None)
    note = grub_recovery_note()
    assert "efibootmgr -o" in note
    assert "mokutil --set-sbat-policy delete" in note
    assert "MAGIC_SBAT_OPTOUT=1" in note
    source = (ROOT / "python" / "engine" / "boot_guard.py").read_text(encoding="utf-8")
    assert "preserve_firmware_order" in source
    assert source.index("preserve_firmware_order") < source.index('"/addfirst"')


if __name__ == "__main__":
    test_linux_preserves_order()
    print("dual boot guard OK")
