"""Post-operation checks for MBR and EFI changes."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.post_op_verify import (  # noqa: E402
    boot_sector_signature_ok,
    evaluate_post_op,
    mandatory_post_op_verify,
    sha256_hex,
)

BCD_UEFI = """
Windows Boot Manager
--------------------
identifier              {bootmgr}
device                  partition=C:
description             Windows Boot Manager

Windows Boot Loader
-------------------
identifier              {default}
device                  partition=C:
path                    \\Windows\\system32\\winload.efi
osdevice                partition=C:
systemroot              \\Windows
"""

BCD_BIOS = BCD_UEFI.replace("winload.efi", "winload.exe")


def _files() -> dict:
    blobs = {"bootmgfw.efi": b"efi-loader", "bootmgr": b"bios-loader", "BCD": b"bcd-store"}
    return {name: {"present": True, "sha256": sha256_hex(data)} for name, data in blobs.items()}


def _uefi() -> dict:
    return {
        "map_ok": True,
        "style": "UEFI",
        "partition": {
            "present": True,
            "gpt_type": "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}",
            "is_system": True,
            "is_active": False,
            "mbr_type": "",
        },
        "files": _files(),
        "bcd_text": BCD_UEFI,
        "firmware_text": "Windows Boot Manager\nidentifier {bootmgr}\n",
        "known_devices": ["partition=C:"],
        "boot_sector": b"",
    }


def _mbr() -> dict:
    sector = b"\x00" * 510 + b"\x55\xaa"
    evidence = _uefi()
    evidence["style"] = "BIOS"
    evidence["partition"] = {"present": True, "gpt_type": "", "is_system": True, "is_active": True, "mbr_type": "07"}
    evidence["bcd_text"] = BCD_BIOS
    evidence["boot_sector"] = sector
    return evidence


def test_uefi_and_mbr_pass() -> None:
    uefi = evaluate_post_op(_uefi(), scopes=("uefi",))
    assert all(item["ok"] for item in uefi)
    names = [item["name"] for item in uefi]
    assert "drive-map" in names and "esp-type" in names and "file:bootmgfw.efi" in names
    assert "file:BCD" in names and "bcd-enum" in names and "firmware-bootmgr" in names
    assert boot_sector_signature_ok(_mbr()["boot_sector"])
    mbr = evaluate_post_op(_mbr(), scopes=("mbr",))
    assert all(item["ok"] for item in mbr)
    assert any(item["name"] == "bootsect-signature" and item["ok"] for item in mbr)
    report = mandatory_post_op_verify(evidence=_uefi(), scopes=("uefi",), rollback=lambda _backup: (_ for _ in ()).throw(AssertionError("rollback")))
    assert report["verdict"] == "GO" and report["rollback"] is False
    print("pass OK")


def test_failed_check_rolls_back_and_is_no_go() -> None:
    bad = _mbr()
    bad["boot_sector"] = b"\x00" * 512
    bad["expected_hashes"] = {"BCD": "0" * 64}
    calls: list[str] = []

    def rollback(backup: str) -> bool:
        calls.append(backup)
        return True

    report = mandatory_post_op_verify(
        evidence=bad,
        scopes=("mbr",),
        rollback=rollback,
        reread=_mbr,
        backup="pre-op-snapshot",
    )
    assert report["verdict"] == "NO-GO"
    assert calls == ["pre-op-snapshot"]
    assert report["restored"] is True
    failed = [item["name"] for item in report["checks"] if not item["ok"]]
    assert "bootsect-signature" in failed and "file:BCD" in failed
    assert all(item["ok"] for item in report["after_rollback"])
    print("rollback OK")


if __name__ == "__main__":
    test_uefi_and_mbr_pass()
    test_failed_check_rolls_back_and_is_no_go()
    print("ALL post-op verify tests passed")
