"""The error catalog is sourced, and the classifier uses it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.error_catalog import catalog_path, load_catalog, match_catalog, repairs_for_ids  # noqa: E402
from engine.post_op_verify import regeneration_plan  # noqa: E402
from engine.win11_inplace_policy import classify_setup_failure  # noqa: E402

REQUIRED = ("id", "patterns", "cause", "detection", "syntax", "repair", "rollback", "sources")


def test_catalog_is_sourced_and_complete() -> None:
    payload = json.loads(catalog_path().read_text(encoding="utf-8"))
    assert payload["entries"]
    ids = set()
    for entry in payload["entries"]:
        for field in REQUIRED:
            assert entry.get(field), entry.get("id")
        assert entry["id"] not in ids
        ids.add(entry["id"])
        assert entry["patterns"]
        assert entry["repair"]
        for url in entry["sources"]:
            assert url.startswith("https://learn.microsoft.com/"), url
    doc = (ROOT / "docs" / "ERROR_REFERENCE.md").read_text(encoding="utf-8")
    assert "learn.microsoft.com" in doc
    print("catalog shape OK")


def test_classifier_uses_the_catalog() -> None:
    french = match_catalog("Les arguments spécifiés pour cette commande ne sont pas valides.")
    assert french[0]["id"] == "diskpart-arguments"
    specific = match_catalog("Setup failed 0xC1900101-0x20017")
    assert specific[0]["id"] == "setup-0xC1900101-0x20017"
    assert specific[0]["cause"] == "boot"
    assert classify_setup_failure(1, "0xC1900101-0x20017 boot configuration") == "boot"
    assert classify_setup_failure(1, "0xC1900101 driver rollback") == "driver"
    assert classify_setup_failure(1, "Setup failed 0xC1900200") == "compat"
    assert classify_setup_failure(1, r"C:\$WINDOWS.~BT\Sources\Rollback\setuperr.log") == "unknown"
    stop = match_catalog("Status: 0xc000000e Info: The boot selection failed")
    assert stop[0]["id"] == "stop-0xc000000e"
    assert "fix-device-refs" in stop[0]["repair"]
    plan = regeneration_plan([{"name": "file:BCD", "ok": False}, {"name": "drive-map", "ok": True}])
    assert plan[0] == "bcdedit-import"
    assert plan == repairs_for_ids(["file:BCD"])[:4]
    assert len(load_catalog()) >= 20
    print("classifier OK")


if __name__ == "__main__":
    test_catalog_is_sourced_and_complete()
    test_classifier_uses_the_catalog()
    print("ALL error catalog tests passed")
