"""Drive map, ESP mount plan, content checks, and mid-run map changes."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.drive_map import (  # noqa: E402
    GPT_ESP_GUID,
    MOUNTVOL_UNSUPPORTED,
    BootDecision,
    DriveMap,
    MapPoller,
    MountPlan,
    VolumeContent,
    VolumeRecord,
    access_chain,
    access_root,
    bcdboot_args,
    bcdboot_letter_plan,
    esp_bcdboot_space_plan,
    minimal_bcdboot_args,
    normalize_volume_guid_path,
    partition_backup_verdict,
    classify_firmware,
    decide,
    drive_map_from_payload,
    format_drive_map,
    letters_in_use,
    make_volume,
    pick_free_letter,
    redact_serial,
    uefi_supported,
)
from engine.esp_access import UnsafeTarget, _apply, apply_and_verify  # noqa: E402


def _win() -> VolumeContent:
    return VolumeContent(
        has_windows=True,
        bitness="x64",
        product_build="26100",
        edition="Pro",
        loader_version=(10, 0, 22621, 3000),
    )


def _efi(**kwargs: object) -> VolumeContent:
    base = dict(
        has_efi_dir=True,
        has_microsoft_boot=True,
        has_bootmgfw=True,
        has_bootx64=True,
        bitness="x64",
        has_bcd=True,
        loader_version=(10, 0, 22621, 1000),
    )
    base.update(kwargs)
    return VolumeContent(**base)  # type: ignore[arg-type]


def _esp(**kwargs: object) -> VolumeRecord:
    fields: dict = dict(
        disk=0,
        partition=1,
        style="GPT",
        gpt_type=GPT_ESP_GUID,
        filesystem="FAT32",
        label="ESP",
        size_bytes=400 * 1024 * 1024,
        letter="Z",
        is_system=True,
        volume_guid="esp-1",
        bus="nvme",
        content=_efi(),
        serial="WD-WCAY1234567890",
    )
    fields.update(kwargs)
    return make_volume(**fields)


def _windows(**kwargs: object) -> VolumeRecord:
    fields: dict = dict(
        disk=0,
        partition=3,
        style="GPT",
        filesystem="NTFS",
        label="Windows",
        size_bytes=120 * 1024 ** 3,
        letter="C",
        is_boot=True,
        volume_guid="win-1",
        bus="nvme",
        content=_win(),
    )
    fields.update(kwargs)
    return make_volume(**fields)


def _gpt(esp: VolumeRecord | None = None, *extra: VolumeRecord, firmware: str = "UEFI", boot_disk: int = 0) -> DriveMap:
    rows = [esp or _esp(), _windows()]
    rows.extend(extra)
    return DriveMap(
        volumes=tuple(rows),
        firmware=firmware,
        boot_disk=boot_disk,
        windows_letter="C",
        windows_volume_guid="win-1",
        source="test",
    )


class _FakeIO:
    def __init__(self, content: VolumeContent) -> None:
        self.content = content
        self.events: list[tuple] = []

    def assign(self, volume: object, letter: str) -> bool:
        self.events.append(("assign", letter))
        return True

    def mountvol(self, volume: object, letter: str) -> bool:
        self.events.append(("mountvol", letter))
        return True

    def folder_mount(self, volume: object, folder: str) -> bool:
        self.events.append(("folder", folder))
        return True

    def remove_letter(self, volume: object, letter: str) -> None:
        self.events.append(("remove", letter))

    def mountvol_delete(self, letter: str) -> None:
        self.events.append(("mountdel", letter))

    def remove_folder(self, volume: object, folder: str) -> None:
        self.events.append(("rmdir", folder))

    def probe(self, path: str) -> VolumeContent:
        self.events.append(("probe", path))
        return self.content


def test_esp_on_letter_other_than_y() -> None:
    decision = decide(_gpt(_esp(letter="Z")))
    assert decision.abort is False
    assert decision.mount is not None
    assert decision.mount.action == "reuse_letter"
    assert decision.mount.letter == "Z"
    assert decision.mount.mounted_by_us is False
    assert decision.strategy == "uefi64"
    assert decision.bcd_mode == "UEFI"
    assert decision.boot_files == ("bootx64.efi",)


def test_esp_without_letter_picks_free_letter() -> None:
    drive_map = _gpt(_esp(letter=None))
    decision = decide(drive_map)
    assert decision.mount is not None
    assert decision.mount.mounted_by_us is True
    assert decision.mount.letter == pick_free_letter(letters_in_use(drive_map))
    assert decision.mount.letter not in (None, "Y", "C")
    assert decision.mount.action in ("mountvol", "assign_letter")


def test_y_already_used() -> None:
    data = make_volume(
        disk=0,
        partition=4,
        style="GPT",
        filesystem="NTFS",
        label="DATA",
        letter="Y",
        size_bytes=10 * 1024 ** 3,
        volume_guid="data-y",
    )
    drive_map = _gpt(_esp(letter=None), data)
    decision = decide(drive_map)
    assert decision.mount is not None
    assert decision.mount.letter != "Y"
    assert "Y" in letters_in_use(drive_map)


def test_mbr_active_partition() -> None:
    srp = make_volume(
        disk=2,
        partition=1,
        style="MBR",
        mbr_type=0xEF,
        filesystem="FAT32",
        label="SYSTEM",
        letter=None,
        is_active=True,
        is_system=True,
        size_bytes=500 * 1024 * 1024,
        volume_guid="mbr-ef",
        content=VolumeContent(has_efi_dir=True, has_bios_bootmgr=True, has_boot_bcd=True),
    )
    reserved = make_volume(
        disk=2,
        partition=2,
        style="MBR",
        filesystem="NTFS",
        label="System Reserved",
        letter="R",
        is_active=False,
        is_system=True,
        size_bytes=350 * 1024 * 1024,
        volume_guid="mbr-srp",
        content=VolumeContent(has_bios_bootmgr=True, has_boot_bcd=True),
    )
    # The 0xEF volume is the system partition. A large FAT32 data volume is not.
    data = make_volume(
        disk=2,
        partition=4,
        style="MBR",
        filesystem="FAT32",
        label="DATA",
        letter="D",
        size_bytes=40 * 1024 ** 3,
        volume_guid="mbr-data",
    )
    windows = _windows(disk=2, style="MBR")
    drive_map = DriveMap(
        volumes=(srp, reserved, windows, data),
        firmware="BIOS",
        boot_disk=2,
        windows_letter="C",
        windows_volume_guid="win-1",
    )
    decision = decide(drive_map)
    assert decision.abort is False
    assert decision.strategy == "bios_mbr"
    assert decision.bcd_mode == "BIOS"
    chosen = decision.srp or decision.esp
    assert chosen is not None
    assert chosen.volume_guid == "mbr-ef"
    assert decision.mount is not None
    assert decision.mount.letter != "D"


def test_cleanup_only_what_we_mounted() -> None:
    volume = _esp(letter=None, filesystem="NTFS", content=VolumeContent(readable=True, has_windows=True))
    plan = MountPlan("assign_letter", "Z:", "Z", True, "esp-mount")
    fake = _FakeIO(VolumeContent(readable=True))
    try:
        apply_and_verify(volume, plan, fake, allow_empty=False)
        raise AssertionError("inconsistent volume was accepted")
    except UnsafeTarget as exc:
        assert exc.reason
    assert ("assign", "Z") in fake.events
    assert ("remove", "Z") in fake.events

    reused = MountPlan("reuse_letter", "S:", "S", False, "esp-mount")
    fake2 = _FakeIO(VolumeContent(readable=True))
    try:
        apply_and_verify(volume, reused, fake2, allow_empty=False)
        raise AssertionError("reused letter should still be verified")
    except UnsafeTarget:
        pass
    assert not any(event[0] == "remove" for event in fake2.events)


def test_letter_reassigned_between_steps() -> None:
    first = _gpt(_esp(letter="S"))
    second = _gpt(_esp(letter="E"))
    decision = decide(second, previous=first)
    assert decision.abort is False
    assert decision.esp is not None and decision.esp.letter == "E"
    assert decision.mount is not None and decision.mount.letter == "E"
    assert "letter reassigned" in decision.explanation


def test_esp_letter_removed() -> None:
    first = _gpt(_esp(letter="S"))
    data = make_volume(
        disk=0, partition=4, style="GPT", filesystem="NTFS", label="DATA",
        letter="Y", size_bytes=8 * 1024 ** 3, volume_guid="data-y",
    )
    second = _gpt(_esp(letter=None, access_paths=()), data)
    decision = decide(second, previous=first)
    assert decision.abort is False
    assert decision.mount is not None
    assert decision.mount.mounted_by_us is True
    assert decision.mount.letter != "Y"
    assert "remount" in decision.actions or "letter reassigned" in decision.explanation


def test_usb_inserted() -> None:
    first = _gpt(_esp(letter="S"))
    usb = make_volume(
        disk=3,
        partition=1,
        style="GPT",
        filesystem="FAT32",
        label="OC",
        letter="F",
        bus="usb",
        volume_guid="usb-oc",
        content=VolumeContent(has_efi_dir=True, has_opencore=True),
    )
    second = _gpt(_esp(letter="S"), usb)
    decision = decide(second, previous=first)
    assert decision.abort is False
    assert decision.esp is not None and decision.esp.volume_guid == "esp-1"
    assert decision.opencore is not None and decision.opencore.volume_guid == "usb-oc"
    assert decision.strategy == "uefi64"
    assert "usb inserted" in decision.explanation


def test_disk_renumbered() -> None:
    first = _gpt(_esp(letter="S", disk=0), firmware="UEFI", boot_disk=0)
    second = _gpt(
        _esp(letter="R", disk=5),
        _windows(disk=5),
        firmware="UEFI",
        boot_disk=5,
    )
    decision = decide(second, previous=first)
    assert decision.abort is False
    assert decision.esp is not None
    assert decision.esp.disk == 5
    assert decision.mount is not None and decision.mount.letter == "R"
    assert "disk renumbered" in decision.explanation


def test_ambiguous_esp_aborts() -> None:
    one = _esp(letter="S", volume_guid="esp-a", partition=1, is_system=False)
    two = _esp(letter="T", volume_guid="esp-b", partition=2, is_system=False)
    decision = decide(_gpt(one, two))
    assert decision.abort is True
    assert decision.bcd_mode == "NONE"
    assert "equally" in decision.explanation or "ambiguous" in decision.explanation.lower()


def test_alternate_esp_when_previous_disappears() -> None:
    first = _gpt(_esp(volume_guid="esp-old", letter="S"))
    replacement = _esp(volume_guid="esp-new", letter="T", partition=2)
    second = _gpt(replacement)
    decision = decide(second, previous=first)
    assert decision.abort is False
    assert decision.esp is not None and decision.esp.volume_guid == "esp-new"
    assert "alternate_esp" in decision.actions


def test_content_picks_microsoft_and_preserves_linux() -> None:
    linux = _esp(
        letter="L",
        volume_guid="esp-linux",
        partition=2,
        is_system=False,
        content=VolumeContent(has_efi_dir=True, linux_loaders=("grub", "shim")),
    )
    microsoft = _esp(letter="Z", volume_guid="esp-ms", is_system=True, content=_efi(linux_loaders=("grub",)))
    decision = decide(_gpt(microsoft, linux))
    assert decision.abort is False
    assert decision.esp is not None and decision.esp.volume_guid == "esp-ms"
    assert "grub" in decision.preserve_loaders


def test_skip_newer_loader() -> None:
    esp = _esp(content=_efi(loader_version=(10, 0, 26100, 9)))
    decision = decide(_gpt(esp))
    assert decision.skip_loader_overwrite is True
    assert "skip_newer_loader" in decision.actions


def test_ia32_and_cross_bitness() -> None:
    ia32 = _esp(
        content=VolumeContent(
            has_efi_dir=True,
            has_bootia32=True,
            has_microsoft_boot=True,
            bitness="x86",
        )
    )
    decision = decide(_gpt(ia32), os_arch="x64", cpu_64=True, secure_boot="on")
    assert decision.strategy == "ia32_hybrid"
    assert decision.bcd_mode == "BIOS"
    assert "bootia32.efi" in decision.boot_files
    assert any("Secure Boot" in warning for warning in decision.warnings)

    cross = decide(_gpt(_esp()), os_arch="x86", cpu_64=True)
    assert cross.strategy == "cross_bitness"
    assert cross.boot_files == ("bootx64.efi",)


def test_install_media_winre_and_bitlocker() -> None:
    media = make_volume(
        disk=4,
        partition=1,
        style="GPT",
        filesystem="FAT32",
        label="WIN11",
        letter="G",
        bus="usb",
        volume_guid="media",
        content=VolumeContent(
            has_efi_dir=True,
            has_bootx64=True,
            has_setup_exe=True,
            has_boot_wim=True,
            has_install_wim=True,
            media_version="26300.9725",
            bitness="x64",
        ),
    )
    winre = make_volume(
        disk=0,
        partition=4,
        style="GPT",
        filesystem="NTFS",
        label="Recovery",
        volume_guid="winre",
        content=VolumeContent(has_winre=True),
    )
    decision = decide(_gpt(_esp(), media, winre))
    assert decision.esp is not None and decision.esp.volume_guid == "esp-1"
    assert decision.install_media is not None and decision.install_media.volume_guid == "media"
    assert decision.winre is not None and decision.winre.volume_guid == "winre"

    locked = _esp(volume_guid="locked", letter=None, bitlocker="locked", content=VolumeContent(readable=False))
    other = _esp(volume_guid="open", letter="Q", partition=2, is_system=False)
    picked = decide(_gpt(other, locked))
    assert picked.abort is False
    assert picked.esp is not None and picked.esp.volume_guid == "open"

    only = decide(_gpt(locked))
    assert only.abort is True
    assert "BitLocker" in only.explanation


def test_no_free_letter_uses_folder() -> None:
    extras = []
    for index, letter in enumerate("DEFGHIJKLMNOPQRSTUVWXYZ"):
        extras.append(
            make_volume(
                disk=0,
                partition=10 + index,
                style="GPT",
                filesystem="NTFS",
                label="DATA",
                letter=letter,
                size_bytes=1024 ** 3,
                volume_guid=f"fill-{letter}",
            )
        )
    drive_map = _gpt(_esp(letter=None), *extras)
    decision = decide(drive_map, folder_fallback=r"C:\mount\esp")
    assert decision.mount is not None
    assert decision.mount.action == "folder"
    assert decision.mount.path == r"C:\mount\esp"
    assert decision.mount.mounted_by_us is True


def test_log_redacts_serial_and_lists_content() -> None:
    payload = {
        "firmware": "UEFI",
        "boot_disk": 0,
        "windows_letter": "C",
        "windows_volume_guid": "win-1",
        "source": "get-partition",
        "volumes": [
            {
                "disk": 0,
                "partition": 1,
                "style": "GPT",
                "gpt_type": "{C12A7328-F81F-11D2-BA4B-00A0C93EC93B}",
                "filesystem": "FAT32",
                "label": "ESP",
                "size_bytes": 400000000,
                "letter": "Z",
                "is_system": True,
                "volume_guid": "esp-1",
                "serial": "WD-WCAY1234567890",
                "content": {
                    "has_bootmgfw": True,
                    "has_bootx64": True,
                    "bitness": "x64",
                    "linux_loaders": ["grub"],
                    "product_build": "26100",
                },
            }
        ],
    }
    drive_map = drive_map_from_payload(payload)
    text = format_drive_map(drive_map)
    assert "WD-WCAY1234567890" not in text
    assert redact_serial("WD-WCAY1234567890") in text
    assert "bootmgfw" in text
    assert "grub" in text
    assert GPT_ESP_GUID in text


def test_poller_sees_letter_change() -> None:
    maps = [
        _gpt(_esp(letter="S")),
        _gpt(_esp(letter="E")),
    ]

    def scanner() -> DriveMap:
        return maps.pop(0)

    now = {"t": 0.0}
    poller = MapPoller(scanner, interval_s=10, clock=lambda: now["t"])
    first = poller.before_step("bcdboot")
    assert first.mount is not None and first.mount.letter == "S"
    now["t"] = 9
    assert poller.poll().mount.letter == "S"  # type: ignore[union-attr]
    assert poller.refreshes == 1
    now["t"] = 20
    second = poller.poll()
    assert second is not None and second.mount is not None
    assert second.mount.letter == "E"
    assert poller.refreshes == 2


def test_volume_guid_is_unescaped_and_bcdboot_uses_a_letter() -> None:
    guid = "11111111-2222-3333-4444-555555555555"
    doubled = "\\\\\\\\?\\\\Volume{" + guid + "}\\\\"
    fixed = "\\\\?\\Volume{" + guid + "}\\"
    assert normalize_volume_guid_path(doubled) == fixed
    assert access_root(doubled) == fixed
    assert normalize_volume_guid_path("\\\\?\\Volume{" + guid + "}") == fixed
    plan = bcdboot_letter_plan(doubled, {"C"})
    assert plan["temporary"] is True
    assert plan["volume"] == fixed
    assert plan["letter"] == "Z:"
    try:
        bcdboot_args("bcdboot.exe", r"C:\Windows", doubled, "UEFI")
        raise AssertionError("volume GUID was passed to bcdboot /s")
    except ValueError:
        pass
    cmd = minimal_bcdboot_args("bcdboot.exe", r"C:\Windows", "Z:", "UEFI", "fr-FR")
    assert cmd[cmd.index("/s") + 1] == "Z:"
    assert "/l" in cmd and "fr-FR" in cmd
    roomy = esp_bcdboot_space_plan(64 * 1024 * 1024, 8 * 1024 * 1024)
    assert roomy["trim_fonts"] is False and roomy["minimal"] is False
    tight = esp_bcdboot_space_plan(1 * 1024 * 1024, 40 * 1024 * 1024)
    assert tight["trim_fonts"] is True and tight["minimal"] is True and tight["backup_required"] is True
    assert partition_backup_verdict({"ok": False, "actions": ["ESP_mount_fail"]}) == "NO-GO"
    assert partition_backup_verdict({"ok": True, "actions": []}) == "GO"
    assert partition_backup_verdict({"ok": False, "actions": ["partition_backup_disabled"]}) == "GO"


def test_bcdboot_uses_resolved_target() -> None:
    command = bcdboot_args(r"C:\Windows\System32\bcdboot.exe", r"C:\Windows", "E:", "ALL")
    assert command[1:] == [r"C:\Windows", "/s", "E:", "/f", "ALL"]
    bios = bcdboot_args("bcdboot.exe", r"D:\Windows", r"E:\esp-mount", "BIOS")
    assert "/s" in bios and r"E:\esp-mount" in bios and "/f" in bios and "BIOS" in bios
    try:
        bcdboot_args("bcdboot.exe", r"C:\Windows", "E:", "MAYBE")
        raise AssertionError("bad mode accepted")
    except ValueError:
        pass


def test_hybrid_mbr2gpt_and_openduet() -> None:
    esp = _esp(style="GPT")
    protective = make_volume(
        disk=0,
        partition=4,
        style="MBR",
        mbr_type=0xEE,
        filesystem="",
        label="GPT",
        volume_guid="protect",
    )
    hybrid = decide(_gpt(esp, protective))
    assert hybrid.strategy == "hybrid_mbr_gpt"
    assert hybrid.allow_mbr2gpt is False
    assert hybrid.bcd_mode == "UEFI"

    srp = make_volume(
        disk=0,
        partition=1,
        style="MBR",
        filesystem="NTFS",
        label="System Reserved",
        letter="R",
        is_system=True,
        is_active=True,
        size_bytes=350 * 1024 * 1024,
        volume_guid="srp",
        content=VolumeContent(has_bios_bootmgr=True, has_boot_bcd=True),
    )
    converted = decide(
        DriveMap(
            volumes=(srp, _windows(style="MBR")),
            firmware="UEFI",
            boot_disk=0,
            windows_letter="C",
            windows_volume_guid="win-1",
        )
    )
    assert converted.strategy == "uefi_mbr_convert"
    assert converted.allow_mbr2gpt is True
    assert converted.bcd_mode == "UEFI"
    assert converted.mount is not None and converted.mount.letter == "R"

    stick = make_volume(
        disk=8,
        partition=1,
        style="MBR",
        filesystem="FAT32",
        letter="K",
        bus="usb",
        volume_guid="duet",
        content=VolumeContent(has_openduet=True, has_efi_dir=True, has_opencore=True),
    )
    legacy = decide(
        DriveMap(volumes=(stick,), firmware="BIOS", boot_disk=0, source="test")
    )
    assert legacy.abort is False
    assert legacy.strategy in ("opencore", "openduet")


def test_inconsistent_esp_aborts() -> None:
    bad = _esp(
        filesystem="NTFS",
        content=VolumeContent(readable=True, has_windows=False),
    )
    decision = decide(_gpt(bad))
    assert decision.abort is True
    assert "inconsistent" in decision.explanation


def test_no_hardcoded_esp_letter_in_sources() -> None:
    needles = (
        "assign letter=S",
        "/s S:",
        "letter or \"Y\"",
        "bcdboot C:\\\\Windows /s S:",
        "bcdboot %SystemRoot% /s S:",
    )
    roots = [ROOT / "python" / "engine", ROOT / "src"]
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".py", ".ps1", ".cmd", ".bat"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in needles:
                if needle in text:
                    hits.append(f"{path.relative_to(ROOT)}: {needle}")
    assert hits == [], hits


def test_firmware_detects_bios_before_mountvol() -> None:
    assert classify_firmware(firmware_type="BIOS", env_var_present=False, bcd_loader="exe") == "BIOS"
    assert classify_firmware(firmware_type="UEFI", env_var_present=False, bcd_loader=r"\Windows\system32\winload.exe") == "BIOS"
    assert classify_firmware(firmware_type="BIOS", env_var_present=True, bcd_loader="efi") == "UEFI"
    assert classify_firmware(firmware_type=None, env_var_present=None, bcd_loader="") == "unknown"
    assert uefi_supported(firmware_type="BIOS", env_var_present=False, bcd_loader="exe") is False
    assert uefi_supported(firmware_type="UEFI", bcd_loader="exe") is True
    assert "legacy BIOS" in MOUNTVOL_UNSUPPORTED


def test_bios_only_has_no_esp() -> None:
    windows = _windows(style="MBR", disk=1, is_active=True, content=_win())
    drive_map = DriveMap(
        volumes=(windows,),
        firmware="BIOS",
        boot_disk=1,
        windows_letter="C",
        windows_volume_guid="win-1",
        uefi_capable=False,
    )
    decision = decide(drive_map)
    assert decision.abort is False
    assert decision.strategy == "bios_mbr"
    assert decision.bcd_mode == "BIOS"
    assert decision.allow_mbr2gpt is False
    assert decision.esp is None
    assert "no_esp_on_bios" in decision.actions
    assert "bootsect_nt60" in decision.actions
    assert "windows_is_boot_target" in decision.actions
    assert decision.mount is not None
    assert decision.mount.action == "reuse_letter"
    assert decision.mount.letter == "C"
    assert "mountvol" not in decision.actions


def test_mountvol_failure_uses_next_method() -> None:
    volume = _esp(letter=None)
    mountvol = MountPlan("mountvol", "E:", "E", True, "esp-mount")
    assign = MountPlan("assign_letter", "E:", "E", True, "esp-mount")

    class IO(_FakeIO):
        def mountvol(self, volume, letter):  # noqa: ARG002
            self.events.append(("mountvol-fail", letter))
            return False

    io = IO(VolumeContent(has_efi_dir=True, has_microsoft_boot=True, has_bootmgfw=True))
    applied = _apply(volume, mountvol, io, attempts=(mountvol, assign))
    assert applied.action == "assign_letter"
    assert ("mountvol-fail", "E") in io.events
    assert ("assign", "E") in io.events


def test_letter_x_taken_and_esp_already_lettered() -> None:
    taken = make_volume(
        disk=0, partition=4, style="GPT", filesystem="NTFS", label="DATA",
        letter="X", size_bytes=1024 ** 3, volume_guid="data-x",
    )
    bare = _esp(letter=None)
    drive_map = _gpt(bare, taken)
    chain = access_chain(bare, letters_in_use(drive_map), folder_fallback="esp-mount", firmware="UEFI")
    assert [step.action for step in chain[:3]] == ["assign_letter", "diskpart_assign", "folder"]
    assert chain[-1].action == "mountvol"
    assert chain[0].letter != "X"
    assert chain[-1].letter == chain[0].letter
    bios = access_chain(bare, letters_in_use(drive_map), folder_fallback="esp-mount", firmware="BIOS")
    assert "mountvol" not in [step.action for step in bios]
    decision = decide(drive_map)
    assert decision.mount is not None and decision.mount.action == "assign_letter"
    assert decision.mount.letter != "X"

    lettered = access_chain(_esp(letter="E"), {"C"}, folder_fallback="esp-mount", firmware="UEFI")
    assert len(lettered) == 1
    assert lettered[0].action == "reuse_letter"
    assert lettered[0].letter == "E"
    assert lettered[0].mounted_by_us is False


def test_gpt_on_bios_dynamic_and_secondary_disk() -> None:
    windows = _windows(style="GPT", disk=2, is_active=False)
    protective = make_volume(
        disk=2, partition=1, style="GPT", mbr_type=0xEE, label="GPT", volume_guid="protect",
    )
    bios_gpt = decide(
        DriveMap(
            volumes=(protective, windows),
            firmware="BIOS",
            boot_disk=2,
            windows_letter="C",
            windows_volume_guid="win-1",
            uefi_capable=False,
        )
    )
    assert bios_gpt.strategy == "bios_gpt"
    assert bios_gpt.allow_mbr2gpt is False
    assert bios_gpt.bcd_mode == "BIOS"
    assert "bootsect_volume_only" in bios_gpt.actions
    assert "no_esp_on_bios" in bios_gpt.actions

    dynamic = _windows(dynamic=True, is_active=False)
    dyn = decide(
        DriveMap(
            volumes=(dynamic,),
            firmware="BIOS",
            boot_disk=0,
            windows_letter="C",
            windows_volume_guid="win-1",
        )
    )
    assert "dynamic_disk" in dyn.actions
    assert dyn.mount is not None and dyn.mount.action == "reuse_letter"
    unlettered = make_volume(
        disk=0, partition=1, style="MBR", filesystem="NTFS", label="System Reserved",
        is_system=True, is_active=False, dynamic=True, volume_guid="dyn-srp",
        content=VolumeContent(has_bios_bootmgr=True, has_boot_bcd=True),
    )
    chain = access_chain(unlettered, {"C"}, folder_fallback="esp-mount", firmware="BIOS")
    assert [step.action for step in chain] == ["folder"]

    decoy = make_volume(
        disk=0, partition=1, style="MBR", filesystem="NTFS", label="System Reserved",
        is_system=True, is_active=True, volume_guid="decoy", letter="R",
        content=VolumeContent(has_bios_bootmgr=True),
    )
    real = make_volume(
        disk=3, partition=1, style="MBR", filesystem="NTFS", label="System Reserved",
        is_system=True, is_active=False, volume_guid="real-srp", letter=None,
        content=VolumeContent(has_bios_bootmgr=True, has_boot_bcd=True),
    )
    secondary = decide(
        DriveMap(
            volumes=(decoy, real, _windows(disk=3, style="MBR")),
            firmware="BIOS",
            boot_disk=0,
            windows_letter="C",
            windows_volume_guid="win-1",
        )
    )
    assert secondary.abort is False
    assert secondary.srp is not None and secondary.srp.volume_guid == "real-srp"
    assert "windows_secondary_disk" in secondary.actions
    assert "mark_active" in secondary.actions


def test_decision_type_exports() -> None:
    assert BootDecision is not None


if __name__ == "__main__":
    test_esp_on_letter_other_than_y()
    test_esp_without_letter_picks_free_letter()
    test_y_already_used()
    test_mbr_active_partition()
    test_cleanup_only_what_we_mounted()
    test_letter_reassigned_between_steps()
    test_esp_letter_removed()
    test_usb_inserted()
    test_disk_renumbered()
    test_ambiguous_esp_aborts()
    test_alternate_esp_when_previous_disappears()
    test_content_picks_microsoft_and_preserves_linux()
    test_skip_newer_loader()
    test_ia32_and_cross_bitness()
    test_install_media_winre_and_bitlocker()
    test_no_free_letter_uses_folder()
    test_log_redacts_serial_and_lists_content()
    test_poller_sees_letter_change()
    test_volume_guid_is_unescaped_and_bcdboot_uses_a_letter()
    test_bcdboot_uses_resolved_target()
    test_hybrid_mbr2gpt_and_openduet()
    test_inconsistent_esp_aborts()
    test_firmware_detects_bios_before_mountvol()
    test_bios_only_has_no_esp()
    test_mountvol_failure_uses_next_method()
    test_letter_x_taken_and_esp_already_lettered()
    test_gpt_on_bios_dynamic_and_secondary_disk()
    test_decision_type_exports()
    test_no_hardcoded_esp_letter_in_sources()
    print("drive map logic OK")
