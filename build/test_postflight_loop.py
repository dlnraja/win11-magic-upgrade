"""Postflight must mount the ESP once and always return GO or NO-GO."""
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

from engine.boot_safe import postflight_boot_edit, resolve_loader_guid  # noqa: E402

MAX_MOUNTS = 1


def test_resolve_loader_guid_prefers_real_winload_entry() -> None:
    enum = """
Windows Boot Manager
identifier              {bootmgr}
default                 {11111111-2222-3333-4444-555555555555}

Windows Boot Loader
identifier              {current}
path                    \\Windows\\system32\\winload.efi

Windows Boot Loader
identifier              {11111111-2222-3333-4444-555555555555}
path                    \\Windows\\system32\\winload.efi
"""
    assert resolve_loader_guid(enum) == "{11111111-2222-3333-4444-555555555555}"


def test_resolve_loader_guid_keeps_current_only_when_it_is_the_loader() -> None:
    enum = """
Windows Boot Loader
identifier              {current}
path                    \\Windows\\system32\\winload.exe
"""
    assert resolve_loader_guid(enum) == "{current}"


def test_postflight_mounts_at_most_once_for_the_same_state() -> None:
    mounts = {"n": 0}
    ledger = Path(__file__).resolve().parent / "_postflight_loop_test.json"
    if ledger.exists():
        ledger.unlink()

    def mount() -> str:
        mounts["n"] += 1
        return "Z:"

    def unmount(_path: str) -> None:
        return None

    kwargs = {
        "expect_uefi": True,
        "system_disk": -1,
        "mount": mount,
        "unmount": unmount,
        "ledger_path": ledger,
        "now": 1_000.0,
        "run_deep": False,
    }
    first = postflight_boot_edit(**kwargs)
    second = postflight_boot_edit(**kwargs)
    third = postflight_boot_edit(**kwargs)
    try:
        assert mounts["n"] <= MAX_MOUNTS, mounts["n"]
        assert first["verdict"] in ("GO", "NO-GO")
        assert second["verdict"] in ("GO", "NO-GO")
        assert third["mounts"] == 0
        assert third["stopped"] == "repeated-state"
        assert "verdict" in first and "verdict" in second and "verdict" in third
    finally:
        if ledger.exists():
            ledger.unlink()


if __name__ == "__main__":
    test_resolve_loader_guid_prefers_real_winload_entry()
    test_resolve_loader_guid_keeps_current_only_when_it_is_the_loader()
    test_postflight_mounts_at_most_once_for_the_same_state()
    print("postflight loop tests passed")
