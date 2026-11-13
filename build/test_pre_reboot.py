"""Repair-before-reboot history, BCD backup selection, and the ASUS hotfix gates."""
from __future__ import annotations

import json
import sys
import types
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
# Skip engine/__init__.py (it imports winreg). Submodules load on their own.
if "engine" not in sys.modules:
    pkg = types.ModuleType("engine")
    pkg.__path__ = [str(ROOT / "python" / "engine")]  # type: ignore[attr-defined]
    pkg.__package__ = "engine"
    sys.modules["engine"] = pkg

from engine.boot_safe import (  # noqa: E402
    assess_postflight,
    bcdboot_succeeded,
    partition_style_from_text,
    style_from_list_disk,
)
from engine.pre_reboot import (  # noqa: E402
    analyze_history,
    attach_export_times,
    inventory_bcd_backups,
    parse_store_enum,
    repair_before_reboot,
    store_is_known_good,
)
from engine.sysreserved import destructive_esp_opt_in, font_cleanup_opt_in  # noqa: E402

GOOD = """
Windows Boot Manager
--------------------
identifier              {bootmgr}
device                  partition=\\Device\\HarddiskVolume2

Windows Boot Loader
-------------------
identifier              {default}
device                  partition=C:
path                    \\Windows\\system32\\winload.efi
osdevice                partition=C:
systemroot              \\Windows
"""

BROKEN = "BFSVC Error: BcdOpenStore failed with unexpected error code.\n"


def test_bcdboot_exit_32_is_failure() -> None:
    text = "BFSVC Error: BcdOpenStore failed with unexpected error code. The operation completed successfully."
    assert bcdboot_succeeded(32, text) is False
    assert bcdboot_succeeded(0, text) is False
    assert bcdboot_succeeded(0, "Boot files successfully created.") is True


def test_gpt_style_from_guid_and_star() -> None:
    assert partition_style_from_text("Type de disque : GPT") == "GPT"
    assert partition_style_from_text("PartitionStyle : GUID") == "GPT"
    listed = "Disk ###  Status  Size  Free  Dyn  Gpt\nDisk 0    Online  238 GB  0 B        *\n"
    assert style_from_list_disk(listed, 0) == "GPT"


def test_postflight_ignores_mountvol_when_storage_works() -> None:
    mounted = assess_postflight(
        firmware="UEFI",
        mounted=True,
        has_esp=True,
        boot_files_ok=True,
        bcd_ok=True,
        methods_exhausted=False,
    )
    assert "esp_unmountable" not in mounted["issues"]
    assert mounted["needs_boot_repair"] is False
    bios = assess_postflight(
        firmware="BIOS",
        mounted=False,
        has_esp=False,
        boot_files_ok=True,
        bcd_ok=True,
        methods_exhausted=True,
    )
    assert bios["issues"] == []
    exhausted = assess_postflight(
        firmware="UEFI",
        mounted=False,
        has_esp=True,
        boot_files_ok=False,
        bcd_ok=True,
        methods_exhausted=True,
    )
    assert exhausted["issues"] == ["esp_unmountable"]
    assert exhausted["needs_boot_repair"] is False


def test_one_click_does_not_expand_without_opt_in(monkeypatch: object | None = None) -> None:
    import os

    os.environ.pop("MAGIC_ESP_EXPAND", None)
    os.environ.pop("MAGIC_ESP_FONT_CLEANUP", None)
    assert destructive_esp_opt_in() is False
    assert font_cleanup_opt_in() is False
    pipeline = (ROOT / "python" / "engine" / "pipeline.py").read_text(encoding="utf-8")
    assert "force or True" not in pipeline


def test_repair_uses_183805_backup_and_does_not_delete(tmp: Path) -> None:
    export = tmp / "bcd-backups" / "bcd-export-20261113T183805Z"
    export.parent.mkdir(parents=True)
    export.write_bytes(b"good-store")
    earlier = tmp / "partition-backups" / "20261113T173617Z" / "ESP-0" / "files" / "EFI" / "Microsoft" / "Boot"
    (earlier / "BCD").parent.mkdir(parents=True)
    (earlier / "BCD").write_bytes(b"esp-copy")
    fonts = earlier / "Fonts"
    fonts.mkdir()
    (fonts / "wgl4_boot.ttf").write_bytes(b"font")
    later = tmp / "bcd-backups" / "bcd-export-20261113T184500Z"
    later.write_bytes(b"after-heal")
    log = tmp / "logs"
    log.mkdir()
    (log / "upgrade-test.log").write_text(
        "\n".join(
            [
                r"2026-11-13 18:38:05, Info  MAGIC  BCD backup exported: C:\Users\Alice\AppData\Local\Win11MagicUpgrade\bcd-backups\bcd-export-20261113T183805Z",
                "2026-11-13 18:40:01, Info  MAGIC  ESP via diskpart volume 2 → X:",
                "2026-11-13 18:40:02, Info  MAGIC  ESP mounted via Storage → S:",
                "2026-11-13 18:41:00, Info  MAGIC  Deleted 16 font files under EFI\\Microsoft\\Boot\\Fonts",
                "2026-11-13 18:44:00, Info  MAGIC  Creating new 512 MB EFI System Partition",
                "2026-11-13 18:45:10, Info  MAGIC  bcdboot rewrite from Windows — boot path re-anchored",
                "2026-11-13 18:45:12, Error MAGIC  BFSVC Error: BcdOpenStore failed exit 32",
                "2026-11-13 18:45:12, Info  MAGIC  Boot Manager repaired (UEFI) OK",
            ]
        ),
        encoding="utf-8",
    )
    history = analyze_history((log / "upgrade-test.log").read_text(encoding="utf-8"))
    assert history["shrunk_c"] is True
    assert history["deleted_fonts"] is True
    assert history["letters_assigned"] == ["X", "S"]
    backups = inventory_bcd_backups(tmp)
    attach_export_times(backups, (log / "upgrade-test.log").read_text(encoding="utf-8"))
    kinds = {Path(item.path).name: item.kind for item in backups}
    assert kinds["bcd-export-20261113T183805Z"] == "export"
    assert kinds["BCD"] == "esp-file-copy"
    imported = {"done": False}
    calls: list[list[str]] = []
    esp_root = tmp / "esp-target"

    def runner(cmd: list[str], timeout: int) -> tuple[int, str]:
        assert timeout > 0
        calls.append(cmd)
        if cmd[1] == "/store":
            if "184500" in cmd[2]:
                return 32, BROKEN
            return 0, GOOD
        if cmd[1] == "/enum":
            return (0, GOOD) if imported["done"] else (32, BROKEN)
        if cmd[1] == "/export":
            Path(cmd[2]).write_bytes(b"current")
            return 0, ""
        if cmd[1] == "/import":
            imported["done"] = True
            assert "183805" in cmd[2]
            return 0, "The operation completed successfully."
        return 1, "unexpected"

    removed: list[str] = []
    report = repair_before_reboot(
        root=tmp,
        runner=runner,
        apply=True,
        esps=[
            {"disk": 0, "partition": 2, "volume": 2, "size_mb": 96, "letter": "S", "has_bootmgfw": True},
            {"disk": 0, "partition": 6, "volume": 6, "size_mb": 512, "letter": "X", "has_bootmgfw": True},
        ],
        firmware_text="device                  partition=\\Device\\HarddiskVolume2\n",
        unmount_letter=removed.append,
        esp_root_for=lambda _esp: esp_root,
    )
    assert report["plan"]["chosen_backup"].endswith("bcd-export-20261113T183805Z")
    assert report["plan"]["chosen_logged_at"] == "2026-11-13 18:38:05", report["plan"]
    assert report["plan"]["delete_partitions"] is False
    assert "never_delete_partitions" in report["plan"]["actions"]
    assert report["plan"]["target_esp"]["volume"] == 2
    assert report["verdict"] == "GO"
    export_at = next(i for i, cmd in enumerate(calls) if cmd[1] == "/export")
    import_at = next(i for i, cmd in enumerate(calls) if cmd[1] == "/import")
    assert export_at < import_at
    assert (esp_root / "EFI" / "Microsoft" / "Boot" / "Fonts" / "wgl4_boot.ttf").is_file()
    assert removed == ["X", "S"]
    assert "18:38:05" in report["summary_text"], report["summary_text"]
    assert "GO" in report["summary_text"]
    bundle = Path(report["bundle"])
    with zipfile.ZipFile(bundle) as archive:
        summary = archive.read("SUMMARY.txt").decode("utf-8")
        redacted = archive.read("logs-redacted.txt").decode("utf-8")
    assert "Alice" not in summary
    assert "Alice" not in redacted
    assert store_is_known_good(parse_store_enum(GOOD)) is True
    parsed = json.loads((bundle.parent / "summary.json").read_text(encoding="utf-8"))
    assert parsed["delete_partitions"] is False


def main() -> None:
    import tempfile

    test_bcdboot_exit_32_is_failure()
    test_gpt_style_from_guid_and_star()
    test_postflight_ignores_mountvol_when_storage_works()
    test_one_click_does_not_expand_without_opt_in()
    with tempfile.TemporaryDirectory() as name:
        test_repair_uses_183805_backup_and_does_not_delete(Path(name))
    print("test_pre_reboot ok")


if __name__ == "__main__":
    main()
