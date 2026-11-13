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
    regeneration_plan,
    sha256_hex,
    strategy_command,
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


def _broken(kind: str) -> tuple[dict, tuple[str, ...]]:
    if kind == "bootsect-signature":
        evidence = _mbr()
        evidence["boot_sector"] = b"\x00" * 512
        return evidence, ("mbr",)
    if kind == "file:bootmgfw.efi":
        evidence = _uefi()
        evidence["files"]["bootmgfw.efi"] = {"present": False, "sha256": ""}
        return evidence, ("uefi",)
    if kind == "file:bootmgr":
        evidence = _mbr()
        evidence["files"]["bootmgr"] = {"present": False, "sha256": ""}
        return evidence, ("mbr",)
    if kind == "file:BCD":
        evidence = _uefi()
        evidence["files"]["BCD"] = {"present": False, "sha256": ""}
        return evidence, ("uefi",)
    if kind == "bcd-enum":
        evidence = _uefi()
        evidence["bcd_text"] = "The boot configuration data store could not be opened.\n"
        return evidence, ("uefi",)
    if kind == "firmware-bootmgr":
        evidence = _uefi()
        evidence["firmware_text"] = "Firmware Boot Manager\n"
        return evidence, ("uefi",)
    if kind == "esp-type":
        evidence = _uefi()
        evidence["partition"] = {"present": True, "gpt_type": "basic-data", "is_system": False}
        return evidence, ("uefi",)
    if kind == "mbr-active":
        evidence = _mbr()
        evidence["partition"]["is_active"] = False
        return evidence, ("mbr",)
    evidence = _uefi()
    evidence["map_ok"] = False
    return evidence, ("uefi",)


_FIRST = {
    "bootsect-signature": "bootsect-nt60-mbr",
    "file:bootmgfw.efi": "copy-bootmgfw",
    "file:bootmgr": "copy-bootmgr",
    "file:BCD": "bcdedit-import",
    "bcd-enum": "fix-device-refs",
    "firmware-bootmgr": "bcdboot-uefi",
    "esp-type": "bcdboot-uefi",
    "mbr-active": "mark-active",
    "drive-map": "rescan-map",
}


def test_each_failure_class_picks_its_repair() -> None:
    for kind, first in _FIRST.items():
        evidence, scopes = _broken(kind)
        plan = regeneration_plan(evaluate_post_op(evidence, scopes=scopes))
        assert plan, kind
        assert plan[0] == first, (kind, plan)
        assert len(plan) <= 4
        seen: list[str] = []

        def attempt(strategy: str, expected: str = first) -> bool:
            seen.append(strategy)
            return strategy == expected

        def reread() -> dict:
            return _uefi() if "uefi" in scopes else _mbr()

        report = mandatory_post_op_verify(
            evidence=evidence,
            scopes=scopes,
            attempt=attempt,
            reread=reread,
            rollback=lambda _backup: (_ for _ in ()).throw(AssertionError(kind)),
        )
        assert report["verdict"] == "GO", kind
        assert seen[0] == first
        assert report["decision"][-1]["verified"] is True
    cmd = strategy_command("bcdboot-uefi-clean", target="S:", locale="fr-FR")
    assert cmd[cmd.index("/f") + 1] == "UEFI"
    assert cmd[cmd.index("/s") + 1] == "S:"
    assert "/l" in cmd and "fr-FR" in cmd and cmd[-1] == "/c"
    assert strategy_command("bootsect-nt60-mbr", target="S")[-2:] == ["S:", "/mbr"]
    assert "/import" in strategy_command("bcdedit-import", backup="good-bcd")
    assert "/createstore" in strategy_command("bcdedit-createstore")
    assert strategy_command("fix-device-refs", device="partition=C:")[-1] == "partition=C:"
    print("classes OK")


def test_exhausted_repairs_roll_back() -> None:
    evidence, scopes = _broken("bootsect-signature")
    tried: list[str] = []

    def attempt(strategy: str) -> bool:
        tried.append(strategy)
        return True

    def still_bad() -> dict:
        return evidence

    calls: list[str] = []
    report = mandatory_post_op_verify(
        evidence=evidence,
        scopes=scopes,
        attempt=attempt,
        reread=still_bad,
        rollback=lambda backup: calls.append(backup) or True,
        backup="last-known-good",
    )
    assert report["verdict"] == "NO-GO"
    assert calls == ["last-known-good"]
    assert tried == regeneration_plan(evaluate_post_op(evidence, scopes=scopes))
    assert all(item["verified"] is False for item in report["decision"])
    print("exhausted OK")


if __name__ == "__main__":
    test_uefi_and_mbr_pass()
    test_failed_check_rolls_back_and_is_no_go()
    test_each_failure_class_picks_its_repair()
    test_exhausted_repairs_roll_back()
    print("ALL post-op verify tests passed")
