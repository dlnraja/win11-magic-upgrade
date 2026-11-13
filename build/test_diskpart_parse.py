"""Unit checks for disk # parsing (FR Disque / EN Disk)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.diskpart_safe import (  # noqa: E402
    assign_partition_letter,
    commands_for,
    decode_diskpart_output,
    disk_number_from_detail,
    diskpart_script_bytes,
    ensure_select_volume,
    failing_command,
    lint_diskpart_script,
    parse_list_volume,
    run_diskpart,
    script_lines,
    select_lines,
    step_scripts,
    vds_is_running,
)


def test_en() -> None:
    assert disk_number_from_detail("Disk #: 0\nType : NTFS") == 0
    assert disk_number_from_detail("Disk # : 1\n") == 1
    assert disk_number_from_detail("  Disk: 2\n") == 2
    print("EN OK")


def test_fr_disque() -> None:
    # Real FR diskpart uses "Disque" — old regex Disk(?:e)? missed this.
    assert disk_number_from_detail("Disque n° : 0\nType : NTFS") == 0
    assert disk_number_from_detail("Disque #: 0\n") == 0
    assert disk_number_from_detail("Disque : 1\n") == 1
    assert disk_number_from_detail("Disque###  0\n") == 0
    assert disk_number_from_detail("  Disque n°0\n") == 0
    print("FR Disque OK")


def test_no_match() -> None:
    assert disk_number_from_detail("Volume ### Ltr Label") is None
    print("no match OK")


FR_LIST_VOLUME = """
  Volume ###  Ltr  Etiquette     Fs     Type        Taille  Statut     Info
  ----------  ---  -----------  -----  ----------  -------  ---------  --------
  Volume 0     C   OS            NTFS   Partition    200 Go  Sain       Demarrage
  Volume 1         SYSTEM        FAT32  Partition     96 Mo  Sain       Systeme
  Volume 2     D   Donnees       NTFS   Partition    500 Go  Sain
""".lstrip("\n")

FR_DETAIL = "Disque n° : 0\nType : FAT32\nInfo : Système\n"


def test_french_list_and_oem_decode() -> None:
    raw = FR_LIST_VOLUME.encode("cp850")
    text = decode_diskpart_output(raw, encoding="cp850")
    vols = parse_list_volume(text)
    assert [v.index for v in vols] == [0, 1, 2]
    assert vols[0].letter == "C" and vols[0].fs == "NTFS" and vols[0].size_mb == 200 * 1024
    assert vols[1].letter is None and vols[1].fs == "FAT32" and vols[1].size_mb == 96
    assert disk_number_from_detail(FR_DETAIL) == 0
    accented = "Volume 1   SYSTEM   FAT32   Système   96 Mo\n".encode("cp850")
    parsed = parse_list_volume(decode_diskpart_output(accented, encoding="cp850"))
    assert parsed[0].index == 1 and parsed[0].fs == "FAT32"
    print("FR list OEM OK")


def test_script_is_oem_crlf_without_bom() -> None:
    data = diskpart_script_bytes("rem étiquette\nselect disk 0\nselect partition 6\nexit\n", encoding="cp850")
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert "étiquette".encode("cp850") in data
    assert "étiquette".encode("utf-8") not in data
    assert data.startswith(b"rem ")
    print("OEM script OK")


def test_select_uses_numbers_and_file_runner() -> None:
    try:
        select_lines(volume="C")  # type: ignore[arg-type]
        raise AssertionError("letter select was accepted")
    except ValueError:
        pass
    assert ensure_select_volume("C") == (False, "select-by-letter-refused")
    lines = select_lines(disk=0, partition=6)
    assert lines == ["select disk 0", "select partition 6"]
    seen: list[bytes] = []

    def runner(argv: list[str], data: bytes, timeout: int) -> tuple[int, bytes]:
        assert argv[0] == "diskpart" and argv[1] == "/s" and timeout == 180
        seen.append(data)
        if len(seen) == 1:
            return 1, "Le service Disque virtuel a rencontré une erreur".encode("cp850")
        return 0, FR_LIST_VOLUME.encode("cp850")

    ok, out = run_diskpart("list volume\nexit\n", runner=runner, encoding="cp850", wait_vds=lambda: None)
    assert ok
    assert seen[0].startswith(b"list volume\r\n")
    assert seen[1].startswith(b"rescan\r\n")
    assert "Volume 0" in out
    assert vds_is_running("STATE              : 4  RUNNING")
    assert vds_is_running("ETAT : 4  RUNNING")
    assert vds_is_running("ETAT : 1  STOPPED") is False
    print("runner OK")


def test_fallback_order_stops_at_first_verified_method() -> None:
    cmds = commands_for(
        "assign",
        disk=0,
        partition=6,
        volume=3,
        letter="Z:",
        volume_guid=r"\\?\Volume{11111111-2222-3333-4444-555555555555}\\",
        folder=r"C:\mount\esp",
    )
    assert cmds["diskpart"] == "select volume 3\nassign letter=Z\nexit"
    assert lint_diskpart_script(cmds["diskpart"]) == []
    assert "Add-PartitionAccessPath" in cmds["powershell"]
    assert "MSFT_Partition" in cmds["cim"] and "AddAccessPath" in cmds["cim"]
    assert cmds["mountvol"].startswith("mountvol Z:")
    assert "select partition" not in cmds["diskpart"]
    calls: list[str] = []

    def runner(argv: list[str], data: bytes, timeout: int) -> tuple[int, bytes]:
        del argv, data, timeout
        calls.append("diskpart")
        return 1, b"erreur"

    def exec_command(argv: list[str], timeout: int) -> tuple[int, str]:
        del timeout
        calls.append(argv[0] if argv[0] != "powershell" else argv[-1][:28])
        if "Add-PartitionAccessPath" in argv[-1] and "MSFT" not in argv[-1] and "mount" not in argv[-1]:
            return 0, "ok"
        return 1, "no"

    method = assign_partition_letter(
        0,
        6,
        "Z",
        volume=3,
        volume_guid=r"\\?\Volume{11111111-2222-3333-4444-555555555555}\\",
        folder=r"C:\mount\esp",
        verify=lambda: "Add-PartitionAccessPath" in "".join(calls),
        exec_command=exec_command,
        runner=runner,
    )
    assert method == "powershell"
    assert calls[0] == "diskpart"
    assert "cim" not in "".join(calls).lower() or "MSFT" not in "".join(calls)
    assert not any(item.startswith("mountvol") for item in calls)
    active = commands_for("active", disk=0, partition=2, style="MBR")
    assert active["diskpart"] == "select disk 0\nselect partition 2\nactive\nexit"
    assert lint_diskpart_script(active["diskpart"], style="MBR") == []
    assert commands_for("active", disk=0, partition=2, style="GPT")["diskpart"] == ""
    assert "Set-Partition" in active["powershell"]
    assert "select volume" not in active["diskpart"]
    print("fallback OK")


GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"


def test_diskpart_grammar_rejects_bad_arguments() -> None:
    assert lint_diskpart_script("select partition 6\nassign letter=S:\nexit")
    assert lint_diskpart_script("select partition 6\nassign letter=S\nexit")
    assert lint_diskpart_script("select volume 3\nassign letter=S\nexit") == []
    assert lint_diskpart_script("select partition 6\nremove letter=S\nexit")
    assert lint_diskpart_script("select volume 3\nremove letter=S\nexit") == []
    assert lint_diskpart_script("select disk 0\nselect partition 1\nactive\nexit", style="GPT")
    assert lint_diskpart_script("select disk 0\nselect partition 1\nactive\nexit", style="MBR") == []
    assert lint_diskpart_script("select volume 1\nactive\nexit", style="MBR")
    assert lint_diskpart_script(f"select disk 0\nselect partition 1\nset id={GUID}\nexit", style="GPT")
    gpt = commands_for("set-id", disk=0, partition=1, gpt_type=GUID, style="GPT")["diskpart"]
    assert gpt.endswith(f"set id={GUID} override\nexit")
    assert lint_diskpart_script(gpt, style="GPT") == []
    assert lint_diskpart_script(gpt, style="MBR")
    mbr = commands_for("set-id", disk=0, partition=1, gpt_type="07", style="MBR")["diskpart"]
    assert "set id=07\n" in mbr and "override" not in mbr
    assert lint_diskpart_script(mbr, style="MBR") == []
    assert commands_for("set-id", disk=0, partition=1, gpt_type=GUID, style="MBR")["diskpart"] == ""
    attrs = commands_for("gpt-attributes", disk=0, partition=1, attributes="0x8000000000000001", style="GPT")["diskpart"]
    assert "gpt attributes=0x8000000000000001" in attrs
    assert lint_diskpart_script(attrs, style="GPT") == []
    assert lint_diskpart_script("select disk 0\nselect partition 1\ngpt attributes=8000\nexit", style="GPT")
    assert lint_diskpart_script(attrs, style="MBR")
    assert lint_diskpart_script("select disk 0\nassign letter=S noerr\nexit")
    create = "select disk 0\ncreate partition efi size=512\nformat fs=fat32 quick label=ESP\nassign letter=S\nexit"
    assert lint_diskpart_script(create, style="GPT") == []
    assert lint_diskpart_script(create, style="MBR")
    primary = "select disk 0\ncreate partition primary size=512\nformat fs=ntfs quick label=System\nassign letter=S\nexit"
    assert lint_diskpart_script(primary, style="MBR") == []
    steps = step_scripts("select disk 0\nselect partition 2\nactive\nexit")
    assert [item[0] for item in steps] == ["select disk 0", "select partition 2", "active"]
    for command, script in steps:
        assert script_lines(script)[-1] == command
        extras = [line for line in script_lines(script) if not line.lower().startswith("select ")]
        assert extras == [] or extras == [command]
    called: list[bytes] = []

    def runner(argv: list[str], data: bytes, timeout: int) -> tuple[int, bytes]:
        del argv, timeout
        called.append(data)
        text = "DISKPART> assign letter=S:\r\nLes arguments spécifiés pour cette commande ne sont pas valides.\r\n"
        return 0, text.encode("cp850")

    ok, out = run_diskpart(
        "select volume 1\nassign letter=S\nexit",
        runner=runner,
        encoding="cp850",
        wait_vds=lambda: None,
    )
    assert ok is False
    assert len(called) == 1
    assert failing_command(out) == "assign letter=S:"
    print("grammar OK")


if __name__ == "__main__":
    test_en()
    test_fr_disque()
    test_no_match()
    test_french_list_and_oem_decode()
    test_script_is_oem_crlf_without_bom()
    test_select_uses_numbers_and_file_runner()
    test_fallback_order_stops_at_first_verified_method()
    test_diskpart_grammar_rejects_bad_arguments()
    print("ALL diskpart parse tests passed")
