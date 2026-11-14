"""ESP under 15 MB and WinRE under 250 MB. Resize stays opt-in."""
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

from engine.space_guards import (  # noqa: E402
    cbs_has_esp_failure,
    esp_plan,
    parse_reagentc_info,
    winre_plan,
)


def test_esp_and_winre_plans() -> None:
    os.environ.pop("MAGIC_ESP_ENLARGE", None)
    os.environ.pop("MAGIC_WINRE_RESIZE", None)
    os.environ.pop("MAGIC_REAGENTC_REPAIR", None)
    esp = esp_plan(free_mb=8, cbs_text="Error 0x800F0922 EFI insufficient space")
    assert esp["tight"] is True
    assert esp["enlarge"] is False
    assert "clean-esp-fonts" in esp["actions"]
    assert "opt-in-enlarge" not in esp["actions"]
    assert cbs_has_esp_failure("ESP insufficient") is True
    os.environ["MAGIC_ESP_ENLARGE"] = "1"
    assert esp_plan(free_mb=8)["enlarge"] is True
    os.environ.pop("MAGIC_ESP_ENLARGE", None)

    info = parse_reagentc_info(
        "Windows RE status: Disabled\nWindows RE location: \\\\?\\GLOBALROOT\\device\\harddisk0\\partition4\n"
        "Unable to update Boot Configuration Data\n"
    )
    assert info["enabled"] is False
    assert info["disk"] == 0 and info["partition"] == 4
    assert info["bcd_update_failed"] is True
    winre = winre_plan(free_mb=40, reagentc_text="Unable to update Boot Configuration Data", event_text="Event 4502 Windows Recovery Environment servicing failed")
    assert winre["tight"] is True
    assert winre["resize"] is False
    assert "reagentc-repair-opt-in" in winre["actions"]
    assert "opt-in-resize-kb5028997" not in winre["actions"]
    os.environ["MAGIC_WINRE_RESIZE"] = "1"
    os.environ["MAGIC_REAGENTC_REPAIR"] = "1"
    opted = winre_plan(free_mb=40, reagentc_text="Unable to update Boot Configuration Data")
    assert opted["resize"] is True
    assert "reagentc-setreimage" in opted["actions"]
    os.environ.pop("MAGIC_WINRE_RESIZE", None)
    os.environ.pop("MAGIC_REAGENTC_REPAIR", None)


if __name__ == "__main__":
    test_esp_and_winre_plans()
    print("space guards OK")
