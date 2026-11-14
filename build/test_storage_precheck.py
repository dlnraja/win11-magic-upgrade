"""RAID/VMD and dynamic-disk prechecks. No automatic convert or AHCI switch."""
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

from engine.storage_precheck import dynamic_disk_block, raid_plan  # noqa: E402


def test_raid_and_dynamic() -> None:
    os.environ.pop("MAGIC_RAID_TO_AHCI", None)
    quiet = raid_plan(pnp_ids=["VEN_8086&DEV_0000"], has_rst=False)
    assert quiet["vmd"] is False
    assert quiet["advise_ahci"] is False
    raid = raid_plan(pnp_ids=["PCI\\VEN_8086&DEV_9A0B&iaStorVD"], rescue_has_driver=False)
    assert raid["vmd"] is True
    assert "inject-rescue-driver" in raid["actions"]
    assert raid["advise_ahci"] is False
    os.environ["MAGIC_RAID_TO_AHCI"] = "1"
    opted = raid_plan(has_rst=True, rescue_has_driver=True)
    assert opted["advise_ahci"] is True
    assert "stage-safeboot-minimal" in opted["actions"]
    os.environ.pop("MAGIC_RAID_TO_AHCI", None)
    assert dynamic_disk_block(False)["nogo"] is False
    blocked = dynamic_disk_block(True)
    assert blocked["nogo"] is True
    assert blocked["code"] == "0x80070001"
    assert blocked["convert_basic"] is False


if __name__ == "__main__":
    test_raid_and_dynamic()
    print("storage precheck OK")
