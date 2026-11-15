"""Compat scan codes, holds, language, phase, and wimmount."""
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

from engine.upgrade_scan import (  # noqa: E402
    decode_scan_code,
    keep_apps_allowed,
    phase_from_subcode,
    safeguard_hold,
    safeguards_bypass_allowed,
    scanonly_argv,
    wimmount_status,
)


def test_upgrade_scan() -> None:
    os.environ.pop("MAGIC_DISABLE_SAFEGUARDS", None)
    argv = scanonly_argv(r"D:\setup.exe", r"C:\logs")
    assert "/compat" in argv and "scanonly" in argv and "/noreboot" in argv
    clean = decode_scan_code(0xC1900210)
    assert clean["go"] is True
    assert decode_scan_code(0xC1900208)["kind"] == "actionable"
    assert decode_scan_code(0xC190020E)["kind"] == "disk"
    hold = safeguard_hold("IntcAudioBus", "10.30.0.5152")
    assert hold is not None and "Intel" in hold["fix"]
    assert safeguard_hold("disk", "1.0") is None
    assert safeguards_bypass_allowed() is False
    os.environ["MAGIC_DISABLE_SAFEGUARDS"] = "1"
    assert safeguards_bypass_allowed() is True
    os.environ.pop("MAGIC_DISABLE_SAFEGUARDS", None)
    ok, reason = keep_apps_allowed(
        host_language="fr-FR",
        iso_language="en-US",
        host_edition="Professional",
        iso_edition="Professional",
        keep_apps=True,
    )
    assert ok is False and "fr-FR" in reason
    ok, _ = keep_apps_allowed(
        host_language="en-US",
        iso_language="en-US",
        host_edition="Pro",
        iso_edition="Windows 11 Pro",
        keep_apps=True,
    )
    assert ok is True
    assert phase_from_subcode(0x40017)["phase"] == "second-boot"
    inbox = ROOT / "build" / "_wimmount_inbox.sys"
    other = ROOT / "build" / "_wimmount_other.sys"
    inbox.write_bytes(b"a")
    other.write_bytes(b"b")
    try:
        assert wimmount_status(path=str(other), inbox_path=str(inbox))["ok"] is False
        assert wimmount_status(path=str(inbox), inbox_path=str(inbox))["ok"] is True
    finally:
        inbox.unlink(missing_ok=True)
        other.unlink(missing_ok=True)


if __name__ == "__main__":
    test_upgrade_scan()
    print("upgrade scan OK")
