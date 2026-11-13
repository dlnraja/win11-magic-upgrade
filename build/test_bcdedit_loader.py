"""Resolve the Windows Boot Loader instead of {current}."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
if "engine" not in sys.modules:
    pkg = types.ModuleType("engine")
    pkg.__path__ = [str(ROOT / "python" / "engine")]  # type: ignore[attr-defined]
    sys.modules["engine"] = pkg

from engine.boot_safe import (  # noqa: E402
    bcdedit_argv,
    bcdedit_entry_error,
    loader_devices_match,
    resolve_loader_identifier,
)

GUID = "{11111111-2222-3333-4444-555555555555}"
ENUM = f"""
Windows Boot Loader
-------------------
identifier              {{current}}
device                  partition=C:
osdevice                partition=C:
path                    \\Windows\\system32\\winload.efi

Windows Boot Loader
-------------------
identifier              {GUID}
device                  partition=C:
osdevice                partition=C:
path                    \\Windows\\system32\\winload.efi

Windows Boot Loader
-------------------
identifier              {{99999999-9999-9999-9999-999999999999}}
device                  partition=D:
osdevice                partition=D:
path                    \\Windows\\system32\\winload.efi
"""


def test_loader_matches_system_partition_not_current() -> None:
    assert resolve_loader_identifier(ENUM, system_drive="C:") == GUID
    assert resolve_loader_identifier(ENUM, system_drive="C:", store=r"S:\EFI\Microsoft\Boot\BCD") == GUID
    only_default = """
identifier              {default}
device                  partition=C:
osdevice                partition=C:
path                    \\Windows\\system32\\winload.efi
"""
    assert resolve_loader_identifier(only_default, system_drive="C:") == "{default}"
    assert loader_devices_match(ENUM, "C:") is True
    assert loader_devices_match("device                  partition=D:\nosdevice                partition=D:", "C:") is False


def test_store_never_targets_current_and_args_are_a_list() -> None:
    argv = bcdedit_argv(r"S:\EFI\Microsoft\Boot\BCD", "/enum", "{default}", "/v")
    assert argv == ["bcdedit", "/store", r"S:\EFI\Microsoft\Boot\BCD", "/enum", "{default}", "/v"]
    assert all(isinstance(part, str) and '"' not in part for part in argv)
    try:
        bcdedit_argv(r"S:\EFI\Microsoft\Boot\BCD", "/set", "{current}", "device", "partition=C:")
        raise AssertionError("{current} was allowed with /store")
    except ValueError:
        pass
    assert bcdedit_entry_error(
        "An error occurred while attempting to reference the specified entry. The file cannot be found."
    )


def test_catalog_maps_the_entry_error() -> None:
    catalog = json.loads((ROOT / "data" / "error_catalog.json").read_text(encoding="utf-8"))
    entry = next(item for item in catalog["entries"] if item["id"] == "bcdedit-current-entry")
    assert any("specified entry" in pattern for pattern in entry["patterns"])
    assert any("{default}" in step for step in entry["repair_order"])
    assert any("/store" in step for step in entry["repair_order"])


if __name__ == "__main__":
    test_loader_matches_system_partition_not_current()
    test_store_never_targets_current_and_args_are_a_list()
    test_catalog_maps_the_entry_error()
    print("bcdedit loader tests OK")
