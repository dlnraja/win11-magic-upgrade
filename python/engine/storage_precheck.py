"""RAID/VMD and dynamic-disk checks before an in-place upgrade.

Switching RAID to AHCI is never advised unless the user opts in and safe mode
is staged. Converting a dynamic disk to basic is never run automatically.
"""
from __future__ import annotations

import os

VMD_MARKERS = ("iastorvd", "iastorac")


def vmd_present(pnp_ids: list[str] | None, has_rst: bool = False) -> bool:
    if has_rst:
        return True
    blob = " ".join(pnp_ids or []).lower()
    return any(marker in blob for marker in VMD_MARKERS)


def raid_plan(*, pnp_ids: list[str] | None = None, has_rst: bool = False, rescue_has_driver: bool = False) -> dict:
    present = vmd_present(pnp_ids, has_rst)
    ahci = os.environ.get("MAGIC_RAID_TO_AHCI", "").strip().lower() in ("1", "true", "yes")
    actions = []
    if present:
        actions.append("export-iastor-driver")
        if not rescue_has_driver:
            actions.append("inject-rescue-driver")
    if ahci and present:
        actions.append("stage-safeboot-minimal")
        actions.append("opt-in-raid-to-ahci")
    return {
        "vmd": present,
        "advise_ahci": bool(ahci and present),
        "actions": actions,
        "message": (
            "Intel RST/VMD is present. Rescue media needs the PC's iaStorVD/iaStorAC driver. "
            "Do not switch RAID to AHCI unless MAGIC_RAID_TO_AHCI=1, and then only after "
            "bcdedit /set {current} safeboot minimal is staged and removed after boot."
            if present
            else ""
        ),
    }


def dynamic_disk_block(dynamic: bool) -> dict:
    if not dynamic:
        return {"nogo": False, "code": "", "message": ""}
    return {
        "nogo": True,
        "code": "0x80070001",
        "convert_basic": False,
        "message": (
            "Windows 11 Setup cannot upgrade a dynamic OS or boot disk (0x80070001). "
            "Back up first. A free converter or a reinstall is required. "
            "diskpart convert basic is never run automatically."
        ),
    }
