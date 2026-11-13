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
    analyze_runs,
    boot_guard_skipped,
    collect_diag_inventory,
    guard_blocks_action,
    apply_startup_repairs,
    decide_startup_repair,
    no_args_entrypoint,
    startup_action,
    startup_boot_guard,
    attach_export_times,
    inventory_bcd_backups,
    parse_store_enum,
    redact_issue_text,
    repair_before_reboot,
    repair_issue_url,
    store_is_known_good,
    upgrade_allowed,
    verdict_message,
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


def test_diag_collects_every_run_and_error_code(tmp: Path) -> None:
    logs = tmp / "logs"
    logs.mkdir()
    (logs / "upgrade-20261113-173000.log").write_text(
        "SESSION 20261113-173000\nesp_unmountable\nINTELLIGENT BOOT REPAIR\n"
        "bcdboot rewrite from Windows\nBFSVC Error: BcdOpenStore failed exit 32\n",
        encoding="utf-8",
    )
    (logs / "upgrade-20261113-180000.log").write_text(
        "SESSION 20261113-180000\nError: 1005\nThe volume does not contain a recognized file system.\n",
        encoding="utf-8",
    )
    (tmp / "Panther").mkdir()
    (tmp / "Panther" / "setuperr.log").write_text("Error MOUNTVOL\n", encoding="utf-8")
    manifest = tmp / "partition-backups" / "20261113T173617Z"
    manifest.mkdir(parents=True)
    (manifest / "manifest.json").write_text('{"files": 147}\n', encoding="utf-8")
    (tmp / "updates").mkdir()
    (tmp / "updates" / "history.json").write_text('{"from": "1.56.0"}\n', encoding="utf-8")
    (tmp / "crashes").mkdir()
    (tmp / "crashes" / "app.dmp").write_bytes(b"DMP")
    (tmp / "bcd-backups").mkdir()
    (tmp / "bcd-backups" / "bcd-export-20261113T183805Z").write_bytes(b"bcd")
    inventory, text = collect_diag_inventory(tmp)
    roles = {item["role"] for item in inventory["files"]}
    assert "session_log" in roles
    assert "panther" in roles
    assert "esp_backup" in roles
    assert "self_update" in roles
    assert "crash_dump" in roles
    assert "bcd_export" in roles
    analysis = analyze_runs(text, inventory)
    assert analysis["repeated_runs"] is True
    assert analysis["run_count"] >= 2
    assert analysis["cascade"] is True
    assert analysis["bcdboot_32"] is True
    assert analysis["dism_1005"] is True
    assert "32" in analysis["error_codes"]
    assert "1005" in analysis["error_codes"]
    url = repair_issue_url(
        {
            "verdict": "NO-GO",
            "summary_text": "cascade and exit 32",
            "bundle": str(tmp / "repair.zip"),
        }
    )
    assert "Full+diagnostic+zip" in url or "Full%20diagnostic%20zip" in url or "Full diagnostic zip" in url


def test_startup_guard_skips_only_when_asked_and_blocks_upgrade(tmp: Path) -> None:
    import engine.pre_reboot as guard
    import os

    os.environ.pop("MAGIC_SKIP_BOOT_GUARD", None)
    guard._LAST_GUARD = None
    assert boot_guard_skipped([]) is False
    assert boot_guard_skipped(["--skip-boot-guard"]) is True
    os.environ["MAGIC_SKIP_BOOT_GUARD"] = "1"
    assert boot_guard_skipped([]) is True
    os.environ.pop("MAGIC_SKIP_BOOT_GUARD", None)
    assert startup_action([]) == "oneclick"
    assert startup_action(["--diagnose"]) == ""
    assert startup_action(["--cli", "--mbr"]) == "mbr"
    assert guard_blocks_action("oneclick", "NO-GO") is True
    assert guard_blocks_action("oneclick", "GO") is False
    assert guard_blocks_action("oneclick", "SKIPPED") is False
    assert guard_blocks_action("", "NO-GO") is False
    skipped = startup_boot_guard(["--skip-boot-guard"])
    assert skipped["verdict"] == "SKIPPED"
    guard._LAST_GUARD = None

    def runner(cmd: list[str], timeout: int) -> tuple[int, str]:
        raise AssertionError(f"budget should stop before {cmd}")

    report = repair_before_reboot(root=tmp, runner=runner, budget_s=0, esps=[])
    assert report["verdict"] == "NO-GO"
    assert "startup_budget_exceeded" in (report.get("plan") or {}).get("go_blockers", [])
    source = (ROOT / "python" / "magic_upgrade.py").read_text(encoding="utf-8")
    assert source.index("startup_update(argv)") < source.index("startup_boot_guard(argv)")


def _path(present: bool, healthy: bool) -> dict:
    return {"present": present, "healthy": healthy, "reasons": () if healthy or not present else ("bad",)}


def test_startup_repair_matrix() -> None:
    def decision(**kwargs):
        base = {
            "firmware": "UEFI",
            "partition_style": "GPT",
            "csm": False,
            "secure_boot": "off",
            "boot_disk_count": 1,
            "boot_disk_known": True,
            "failed_path": "",
            "esp": _path(True, True),
            "mbr": _path(False, True),
        }
        base.update(kwargs)
        return decide_startup_repair(base)

    healthy = decision()
    assert healthy["mode"] == "none"
    assert healthy["repair_uefi"] is False and healthy["repair_mbr"] is False

    uefi_only = decision(esp=_path(True, False))
    assert uefi_only["mode"] == "uefi" and uefi_only["repair_mbr"] is False

    bios_only = decision(
        firmware="BIOS",
        partition_style="MBR",
        esp=_path(False, True),
        mbr=_path(True, False),
    )
    assert bios_only["mode"] == "mbr" and bios_only["repair_uefi"] is False

    hybrid = decision(
        firmware="UEFI",
        partition_style="HYBRID",
        csm=True,
        esp=_path(True, False),
        mbr=_path(True, False),
    )
    assert hybrid["mode"] == "hybrid" and hybrid["repair_uefi"] and hybrid["repair_mbr"]

    csm_mbr_healthy = decision(csm=True, partition_style="HYBRID", esp=_path(True, False), mbr=_path(True, True))
    assert csm_mbr_healthy["mode"] == "uefi" and csm_mbr_healthy["repair_mbr"] is False

    history = decision(
        csm=True,
        partition_style="HYBRID",
        esp=_path(True, False),
        mbr=_path(True, False),
        failed_path="bios",
    )
    assert history["mode"] == "mbr" and history["repair_uefi"] is False

    disks = decision(boot_disk_count=2, boot_disk_known=False, esp=_path(True, False))
    assert disks["mode"] == "none" and disks["reason"] == "multiple_boot_disks"

    called: list[str] = []
    apply_startup_repairs(uefi_only, uefi_repair=lambda: called.append("uefi"), mbr_repair=lambda: called.append("mbr"))
    assert called == ["uefi"]
    called.clear()
    apply_startup_repairs(healthy, uefi_repair=lambda: called.append("uefi"), mbr_repair=lambda: called.append("mbr"))
    assert called == []


def test_no_args_entrypoint_calls_guard_first() -> None:
    order: list[str] = []
    no_args_entrypoint(
        guard=lambda: order.append("guard"),
        update=lambda: order.append("update"),
        continue_app=lambda: order.append("app"),
    )
    assert order == ["guard", "update", "app"]
    source = (ROOT / "python" / "magic_upgrade.py").read_text(encoding="utf-8")
    main = source.split("def main()", 1)[1].split("def _main_after_no_args", 1)[0]
    assert "if not sys.argv[1:]" in main
    assert main.index("no_args_entrypoint") < main.index("return")
    assert "startup_update" not in main


def test_oneclick_stops_unless_go_and_issue_url_is_redacted() -> None:
    import os

    assert upgrade_allowed("GO") is True
    assert upgrade_allowed("NO-GO") is False
    assert upgrade_allowed("REVIEW") is False
    os.environ["USERNAME"] = "Alice"
    os.environ["COMPUTERNAME"] = "DESKTOP-SECRET"
    raw = (
        "Owner Alice on DESKTOP-SECRET email alice@example.com "
        "MAC 00-11-22-33-44-55 serial number: ABCD1234 "
        r"C:\Users\Alice\AppData\Local\Win11MagicUpgrade\repair.zip"
    )
    cleaned = redact_issue_text(raw)
    assert "Alice" not in cleaned
    assert "DESKTOP-SECRET" not in cleaned
    assert "alice@example.com" not in cleaned
    assert "00-11-22-33-44-55" not in cleaned
    assert "ABCD1234" not in cleaned
    url = repair_issue_url(
        {
            "verdict": "NO-GO",
            "summary_text": raw,
            "bundle": r"C:\Users\Alice\AppData\Local\Win11MagicUpgrade\repair.zip",
        }
    )
    assert url.startswith("https://github.com/dlnraja/win11-magic-upgrade/issues/new?")
    assert "ghp_" not in url and "github_pat_" not in url and "Bearer" not in url
    assert "Alice" not in url
    assert "NO-GO" in verdict_message("NO-GO", r"C:\Users\Alice\zip")
    assert "GO" in verdict_message("GO", "")
    source = (ROOT / "python" / "magic_upgrade.py").read_text(encoding="utf-8")
    assert "repair_before_reboot(uefi=default_uefi())" in source
    assert "upgrade_allowed" in source
    body = source.split("def _main_after_no_args", 1)[1]
    assert "console_countdown(" in body
    # Self-update still runs before the no-args countdown and the official update.
    assert body.index("startup_update(argv)") < body.index("console_countdown(")


def main() -> None:
    import tempfile

    test_bcdboot_exit_32_is_failure()
    test_gpt_style_from_guid_and_star()
    test_postflight_ignores_mountvol_when_storage_works()
    test_one_click_does_not_expand_without_opt_in()
    test_startup_repair_matrix()
    test_no_args_entrypoint_calls_guard_first()
    test_oneclick_stops_unless_go_and_issue_url_is_redacted()
    with tempfile.TemporaryDirectory() as name:
        test_startup_guard_skips_only_when_asked_and_blocks_upgrade(Path(name))
    with tempfile.TemporaryDirectory() as name:
        test_diag_collects_every_run_and_error_code(Path(name))
    with tempfile.TemporaryDirectory() as name:
        test_repair_uses_183805_backup_and_does_not_delete(Path(name))
    print("test_pre_reboot ok")


if __name__ == "__main__":
    main()
