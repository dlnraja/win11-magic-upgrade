"""Stale ESP, MSR, and recovery letters, plus mountvol GUID mounts."""
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

from engine.drive_map import GPT_ESP_GUID, MountPlan, VolumeContent, VolumeRecord, access_chain  # noqa: E402
from engine.esp_access import _apply, _rollback  # noqa: E402
from engine.stale_letters import (  # noqa: E402
    ESP,
    MSR,
    RECOVERY,
    judge_bootmgr,
    release_letter,
    stale_system_letters,
    sweep_stale_letters,
    volume_guid_path,
)


def _vol(**kwargs: object) -> VolumeRecord:
    fields: dict = dict(disk=0, partition=1, style="GPT", gpt_type=ESP, filesystem="FAT32", letter=None)
    fields.update(kwargs)
    return VolumeRecord(**fields)


class _IO:
    def __init__(self, steps: dict[str, bool]) -> None:
        self.steps = steps
        self.calls: list[str] = []

    def mountvol_delete(self, letter: str) -> bool:
        self.calls.append(f"mountvol {letter}")
        return self.steps.get("mountvol", False)

    def remove_letter(self, volume: object, letter: str) -> bool:
        self.calls.append(f"access {letter}")
        return self.steps.get("access", False)

    def diskpart_remove(self, volume: object, letter: str) -> bool:
        self.calls.append(f"diskpart {letter}")
        return self.steps.get("diskpart", False)

    def mountvol_guid(self, volume: object, letter: str) -> bool:
        self.calls.append(f"guid {letter}")
        return True

    def mountvol_delete_unused(self, letter: str) -> None:
        self.calls.append(f"unmount {letter}")


def test_only_system_partitions_lose_letters() -> None:
    data = _vol(
        partition=3,
        gpt_type="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
        filesystem="NTFS",
        letter="D",
    )
    windows = _vol(partition=4, gpt_type="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7", letter="C")
    esp = _vol(letter="Y", access_paths=("Y:\\",))
    msr = _vol(partition=2, gpt_type=MSR, letter="S")
    recovery = _vol(partition=5, gpt_type=RECOVERY, letter="R")
    mistaken = _vol(letter="C")
    found = stale_system_letters([data, windows, esp, msr, recovery, mistaken], windows_letter="C")
    letters = {item.letter for item in found}
    assert letters == {"Y", "S", "R"}
    assert all(item.gpt_type.lower() in {ESP, MSR, RECOVERY} for item in found)


def test_removal_falls_through_and_bootmgr_is_logged() -> None:
    esp = _vol(letter="Y", disk=0, partition=2)
    io = _IO({"mountvol": False, "access": False, "diskpart": True})
    notes: list[str] = []
    result = sweep_stale_letters(
        when="end",
        volumes=[esp, _vol(partition=3, gpt_type="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7", letter="D")],
        io=io,
        enum_bootmgr=lambda: "Windows Boot Manager\r\ndevice                  partition=\\Device\\HarddiskVolume2\r\n",
        logger=lambda message, level="INFO": notes.append(f"{level}:{message}"),
    )
    assert io.calls == ["mountvol Y", "access Y", "diskpart Y"]
    assert result["removed"][0]["method"] == "diskpart"
    assert result["bootmgr_ok"] is True
    assert any("bootmgr resolves" in line for line in notes)
    assert judge_bootmgr("The system cannot find the file specified.")[0] is False
    refused = release_letter("C", esp, io)
    assert refused == "refused"
    assert "mountvol C" not in io.calls


def test_mountvol_guid_is_in_the_chain_and_unmounted() -> None:
    guid = "b95f9bc1-965f-43ae-81ff-28b0e4909488"
    assert volume_guid_path(guid) == "\\\\?\\Volume{" + guid + "}\\"
    volume = _vol(letter=None, volume_guid=guid, gpt_type=GPT_ESP_GUID)
    chain = [step.action for step in access_chain(volume, {"C"}, folder_fallback="esp-mount", firmware="UEFI")]
    assert "mountvol_guid" in chain
    assert chain[-1] == "mountvol"
    bios = [step.action for step in access_chain(volume, {"C"}, folder_fallback="esp-mount", firmware="BIOS")]
    assert "mountvol_guid" in bios
    assert "mountvol" not in bios
    plain = _vol(letter=None, volume_guid="esp-1")
    assert "mountvol_guid" not in [
        step.action for step in access_chain(plain, {"C"}, folder_fallback="esp-mount", firmware="UEFI")
    ]

    class IO(_IO):
        def __init__(self) -> None:
            super().__init__({})
            self.events: list[tuple] = []

        def mountvol_guid(self, volume: object, letter: str) -> bool:  # noqa: ARG002
            self.events.append(("guid", letter))
            return True

        def probe(self, path: str) -> VolumeContent:
            return VolumeContent()

    io = IO()
    plan = MountPlan("mountvol_guid", "E:", "E", True, "esp-mount")
    applied = _apply(volume, plan, io, attempts=(plan,))
    assert applied.action == "mountvol_guid"
    _rollback(applied, volume, io)
    assert io.calls[0] == "mountvol E"


if __name__ == "__main__":
    test_only_system_partitions_lose_letters()
    test_removal_falls_through_and_bootmgr_is_logged()
    test_mountvol_guid_is_in_the_chain_and_unmounted()
    print("stale letter tests OK")
