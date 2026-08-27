"""Unit checks for OEM family classification (no WMI mutation)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.oem_adapt import (  # noqa: E402
    apply_oem_to_partition_plan,
    build_oem_policy,
    classify_oem_family,
    OemProfile,
)


def test_classify() -> None:
    assert classify_oem_family("TOSHIBA", "SATELLITE") == "toshiba"
    assert classify_oem_family("Dynabook Inc.", "TECRA") == "toshiba"
    assert classify_oem_family("Acer", "Aspire") == "acer"
    assert classify_oem_family("ASUSTeK COMPUTER INC.", "VivoBook") == "asus"
    assert classify_oem_family("Dell Inc.", "XPS") == "dell"
    assert classify_oem_family("HP", "EliteBook") == "hp"
    assert classify_oem_family("LENOVO", "ThinkPad") == "lenovo"
    assert classify_oem_family("Micro-Star International", "Modern") == "msi"
    print("classify OK")


def test_toshiba_policy() -> None:
    pol = build_oem_policy("toshiba")
    assert pol["prefer_new_esp_over_grow"] is True
    assert pol["preserve_oem_efi_strict"] is True
    plan = {"strategy": "extend_boot", "reasons": []}
    profile = OemProfile(family="toshiba", prefer_new_esp_over_grow=True)
    out = apply_oem_to_partition_plan(plan, profile)
    assert out["strategy"] == "fallback_legacy"
    assert "oem_prefer_new_esp_over_grow" in out["reasons"]
    print("toshiba policy OK")


def test_encryption_block() -> None:
    plan = {"strategy": "shrink_c_then_create", "reasons": []}
    profile = OemProfile(family="toshiba", encryption_blocks_mutate=True)
    out = apply_oem_to_partition_plan(plan, profile)
    assert out["strategy"] == "blocked_encryption"
    print("encryption block OK")


def test_bitlocker_on_does_not_block_plan() -> None:
    """BitLocker Protection On must NOT flip strategy to blocked_encryption."""
    plan = {"strategy": "shrink_c_then_create", "reasons": []}
    profile = OemProfile(
        family="generic",
        bitlocker="on",
        device_encryption=True,
        encryption_blocks_mutate=False,
    )
    out = apply_oem_to_partition_plan(plan, profile)
    assert out["strategy"] == "shrink_c_then_create", out
    print("bitlocker on does not block plan OK")


def test_prepare_locked_blocks_on_does_not() -> None:
    from engine.oem_adapt import prepare_encryption_for_mutate

    locked = OemProfile(family="generic", bitlocker="locked", encryption_blocks_mutate=True)
    r1 = prepare_encryption_for_mutate(locked)
    assert r1["blocked"] is True
    # On: may call manage-bde (may fail in CI without BL) but must not set blocked
    on = OemProfile(family="generic", bitlocker="on", encryption_blocks_mutate=False)
    r2 = prepare_encryption_for_mutate(on)
    assert r2["blocked"] is False, r2
    print("prepare locked vs on OK")


if __name__ == "__main__":
    test_classify()
    test_toshiba_policy()
    test_encryption_block()
    test_bitlocker_on_does_not_block_plan()
    test_prepare_locked_blocks_on_does_not()
    print("ALL oem_adapt logic checks passed")
