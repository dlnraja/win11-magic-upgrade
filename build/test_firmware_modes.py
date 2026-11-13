"""Boot-plan choices for UEFI 64, UEFI 32, BIOS, hybrid, and unknown firmware."""
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

from engine.firmware_matrix import FirmwareSnapshot, decide_boot_plan  # noqa: E402


def _snap(**kwargs: object) -> FirmwareSnapshot:
    base = dict(
        firmware_mode="uefi",
        firmware_bitness="x64",
        uefi_capable=True,
        cpu_bits=64,
        os_arch="x64",
        partition_style="gpt",
        secure_boot="off",
    )
    base.update(kwargs)
    return FirmwareSnapshot(**base)  # type: ignore[arg-type]


def main() -> None:
    uefi64 = decide_boot_plan(_snap())
    assert uefi64.bcd_mode == "UEFI"
    assert "bootx64.efi" in uefi64.boot_files
    assert uefi64.allow_mbr2gpt is False

    uefi32 = decide_boot_plan(
        _snap(firmware_bitness="ia32", firmware_likely_ia32=True, esp_has_bootia32=True)
    )
    assert uefi32.bcd_mode == "BIOS"
    assert "bootia32.efi" in uefi32.boot_files
    assert uefi32.allow_mbr2gpt is False
    assert uefi32.allow_hybrid_csmwrap is True

    bios = decide_boot_plan(
        _snap(firmware_mode="bios", firmware_bitness="legacy", uefi_capable=False, partition_style="mbr")
    )
    assert bios.bcd_mode == "BIOS"
    assert bios.boot_files == ("bootmgr",)
    assert bios.allow_mbr2gpt is False
    assert bios.suggest_openduet is True

    hybrid = decide_boot_plan(_snap(partition_style="hybrid"))
    assert hybrid.allow_mbr2gpt is False
    assert hybrid.bcd_mode == "UEFI"

    unknown = decide_boot_plan(
        _snap(firmware_mode="unknown", firmware_bitness="unknown", uefi_capable=False, partition_style="unknown")
    )
    assert unknown.path_id == "unknown_conservative"
    assert unknown.bcd_mode == "NONE"
    assert unknown.allow_mbr2gpt is False
    print("test_firmware_modes ok")


if __name__ == "__main__":
    main()
