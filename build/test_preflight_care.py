"""Offline preflight warnings do not write unless a flag is set."""
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

from engine.preflight_care import (  # noqa: E402
    device_encryption_unattend_snippet,
    driver_inject_plan,
    hiberboot_plan,
    onedrive_warning,
    onedrive_redirects,
    uup_fallback_plan,
    ventoy_mok_note,
)


def test_warnings_stay_offline() -> None:
    os.environ.pop("MAGIC_PREVENT_DEVICE_ENCRYPTION", None)
    os.environ.pop("MAGIC_INJECT_RESCUE_DRIVERS", None)
    assert hiberboot_plan(enabled=True)["commands"] == ["powercfg /h off"]
    assert hiberboot_plan(enabled=False)["commands"] == []
    moved = onedrive_redirects(
        {"Desktop": r"C:\Users\a\OneDrive\Desktop", "Personal": r"C:\Users\a\Documents"},
        r"C:\Users\a",
    )
    assert len(moved) == 1
    assert "OneDrive" in onedrive_warning(moved)
    assert device_encryption_unattend_snippet() == ""
    os.environ["MAGIC_PREVENT_DEVICE_ENCRYPTION"] = "1"
    assert "PreventDeviceEncryption" in device_encryption_unattend_snippet()
    os.environ.pop("MAGIC_PREVENT_DEVICE_ENCRYPTION", None)
    assert driver_inject_plan(export_dir="D:\\drv", boot_wim="R:\\")["commands"] == []
    os.environ["MAGIC_INJECT_RESCUE_DRIVERS"] = "1"
    commands = driver_inject_plan(export_dir="D:\\drv", boot_wim="R:\\")["commands"]
    assert commands[0].startswith("pnputil /export-driver")
    assert "dism" in commands[1].lower()
    os.environ.pop("MAGIC_INJECT_RESCUE_DRIVERS", None)
    assert "0x1A" in ventoy_mok_note(True, True)
    assert ventoy_mok_note(False, False) == ""
    bad = uup_fallback_plan(languages=["en-us", "fr-fr"], wimlib_version="1.13.0", have_wim=False)
    assert bad["ok"] is False
    assert len(bad["problems"]) == 3
    good = uup_fallback_plan(languages=["en-us"], wimlib_version="1.14.3", have_wim=True)
    assert good["ok"] is True


if __name__ == "__main__":
    test_warnings_stay_offline()
    print("preflight care OK")
