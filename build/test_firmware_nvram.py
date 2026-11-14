"""NVRAM boot entry after bcdboot /s, including /f ALL and firmware enum."""
from __future__ import annotations

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

from engine.firmware_nvram import (  # noqa: E402
    ensure_fallback_loader,
    ensure_firmware_entry,
    firmware_has_bootmgr,
    nvram_bcdboot_args,
)

PRESENT = """
Firmware Boot Manager
identifier              {fwbootmgr}
displayorder            {bootmgr}

Windows Boot Manager
identifier              {bootmgr}
"""
MISSING = """
Firmware Boot Manager
identifier              {fwbootmgr}
displayorder            {other}
"""


def test_detect_and_followup_commands() -> None:
    assert firmware_has_bootmgr(PRESENT) is True
    assert firmware_has_bootmgr(MISSING) is False
    args = nvram_bcdboot_args("bcdboot.exe", r"C:\Windows", mode="ALL", preserve_order=True)
    assert "/s" not in args
    assert args[-3:] == ["/f", "ALL", "/p"]
    plain = nvram_bcdboot_args("bcdboot.exe", r"C:\Windows", mode="UEFI", preserve_order=False)
    assert plain[-2:] == ["/f", "UEFI"]


def test_second_bcdboot_then_addfirst() -> None:
    calls: list[list[str]] = []
    enums = [MISSING, MISSING, PRESENT]

    def runner(cmd, timeout=30):
        calls.append(list(cmd))
        if cmd[:2] == ["bcdedit", "/enum"]:
            return 0, enums.pop(0)
        return 0, ""

    result = ensure_firmware_entry(
        exe="bcdboot.exe",
        windows=r"C:\Windows",
        mode="UEFI",
        runner=runner,
        enum_text=None,
    )
    assert result["ok"] is True
    assert result["action"] == "addfirst"
    assert calls[1][:3] == ["bcdboot.exe", r"C:\Windows", "/f"]
    assert "/s" not in calls[1]


def test_linux_preserves_order_and_does_not_addfirst() -> None:
    calls: list[list[str]] = []

    def runner(cmd, timeout=30):
        calls.append(list(cmd))
        if cmd[:2] == ["bcdedit", "/enum"]:
            return 0, MISSING
        return 0, ""

    result = ensure_firmware_entry(
        exe="bcdboot.exe",
        windows=r"C:\Windows",
        mode="UEFI",
        runner=runner,
        linux_present=True,
        enum_text=MISSING,
    )
    assert result["ok"] is False
    assert any("/p" in cmd for cmd in calls)
    assert not any("/addfirst" in cmd for cmd in calls)


def test_fallback_loader_copies_only_when_missing(tmp: Path) -> None:
    boot = tmp / "EFI" / "Microsoft" / "Boot"
    boot.mkdir(parents=True)
    (boot / "bootmgfw.efi").write_bytes(b"efi")
    assert ensure_fallback_loader(tmp) is True
    copied = tmp / "EFI" / "Boot" / "bootx64.efi"
    assert copied.read_bytes() == b"efi"
    copied.write_bytes(b"keep")
    assert ensure_fallback_loader(tmp) is True
    assert copied.read_bytes() == b"keep"


if __name__ == "__main__":
    import tempfile

    test_detect_and_followup_commands()
    test_second_bcdboot_then_addfirst()
    test_linux_preserves_order_and_does_not_addfirst()
    with tempfile.TemporaryDirectory() as name:
        test_fallback_loader_copies_only_when_missing(Path(name))
    print("firmware nvram OK")
