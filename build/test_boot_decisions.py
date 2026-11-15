"""Firmware, bootrec, bcdboot, Storage Spaces, and boot-info decisions."""
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

from engine.boot_decisions import (  # noqa: E402
    bcdboot_ready,
    boot_info_text,
    bootrec_fixboot_allowed,
    ca2023_card,
    classify_800f0922,
    remove_stale_allowed,
    stale_boot_entries,
    storage_spaces_warning,
)


def test_decisions() -> None:
    os.environ.pop("MAGIC_REMOVE_STALE_BOOT", None)
    assert ca2023_card(status="Updated")["go"] is True
    assert ca2023_card(status="NotStarted", error="1801")["go"] is False
    assert classify_800f0922("0x800F0922 DBX KB5012170") == "dbx"
    assert classify_800f0922("0x800F0922 ESP insufficient") == "esp"
    assert bootrec_fixboot_allowed("UEFI") is False
    assert bootrec_fixboot_allowed("BIOS") is True
    ok, reason = bcdboot_ready(has_template=False, filesystem="FAT32", esp_type=True, free_mb=50, firmware="UEFI")
    assert ok is False and "BCD-Template" in reason
    ok, reason = bcdboot_ready(has_template=True, filesystem="FAT32", esp_type=True, free_mb=4, firmware="UEFI")
    assert ok is False and "10 MB" in reason
    ok, _ = bcdboot_ready(has_template=True, filesystem="NTFS", esp_type=False, free_mb=20, firmware="BIOS")
    assert ok is True
    assert "not upgraded" in storage_spaces_warning(unhealthy=True, refs=True)
    text = boot_info_text(disks="disk0", esp="esp", bcd="bcd", firmware="fw", reagentc="re", secure_boot="on", bitlocker="off")
    assert "read-only" in text
    stale = stale_boot_entries(["{bootmgr}", "{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}"], {"{bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee}"})
    assert len(stale) == 1
    assert remove_stale_allowed() is False


if __name__ == "__main__":
    test_decisions()
    print("boot decisions OK")
