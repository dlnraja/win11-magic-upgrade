"""Local ISO scan: read-only, bounded, offline, and preferred over a download."""
from __future__ import annotations

import os
import socket
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

from engine.local_iso import (  # noqa: E402
    apply_cli_iso,
    choose_local_iso,
    hash_verdict,
    load_cache,
    parse_cversion,
    parse_lang_ini,
    parse_wim_info,
    pick_best,
    save_cache,
    scan_tree,
)


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_scan_skips_system_dirs_reparse_and_small_files(tmp: Path) -> None:
    _write(tmp / "Windows" / "Win11.iso", 50)
    _write(tmp / "ProgramData" / "Win11.iso", 50)
    _write(tmp / "$Recycle.Bin" / "Win11.iso", 50)
    _write(tmp / "Users" / "Downloads" / "Win11_Pro_en-us_x64.iso", 50)
    _write(tmp / "Users" / "Downloads" / "notes.iso", 10)
    media = tmp / "extracted"
    _write(media / "sources" / "install.wim", 50)
    (media / "setup.exe").write_text("setup", encoding="utf-8")
    (media / "sources" / "cversion.ini").write_text("MinClient=10.0.26100.1\n", encoding="utf-8")
    (media / "sources" / "lang.ini").write_text("[Available UI Languages]\nen-US = 3\n", encoding="utf-8")
    try:
        (tmp / "again").symlink_to(tmp, target_is_directory=True)
    except OSError:
        pass
    found = scan_tree([tmp], budget_s=5, min_bytes=50)
    names = {Path(item["path"]).name.lower() for item in found}
    assert "win11_pro_en-us_x64.iso" in names
    assert "install.wim" in names
    assert "notes.iso" not in names
    assert all("windows" not in Path(item["path"]).parts[-2].lower() for item in found)
    assert all("programdata" not in str(item["path"]).lower() for item in found)
    assert all("recycle" not in str(item["path"]).lower() for item in found)


def test_budget_stops_the_walk(tmp: Path) -> None:
    folder = tmp
    for index in range(30):
        folder = folder / f"d{index}"
    _write(folder / "deep.iso", 80)
    calls = {"n": 0}

    def clock() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] < 4 else 50.0

    found = scan_tree([tmp], budget_s=1, min_bytes=1, clock=clock)
    assert found == []
    assert calls["n"] >= 4


def test_pick_newest_matching_language_edition_and_arch() -> None:
    rows = [
        {"path": "old.iso", "kind": "iso", "build": 22621, "language": "en-US", "edition": "Pro", "arch": "x64", "win": "11", "size": 10, "hash": "unknown"},
        {"path": "new.iso", "kind": "iso", "build": 26100, "language": "en-GB", "edition": "Windows 11 Pro", "arch": "amd64", "win": "11", "size": 9, "hash": "official"},
        {"path": "fr.iso", "kind": "iso", "build": 27000, "language": "fr-FR", "edition": "Pro", "arch": "x64", "win": "11", "size": 11, "hash": "unknown"},
        {"path": "home.iso", "kind": "iso", "build": 26100, "language": "en-US", "edition": "Home", "arch": "x64", "win": "11", "size": 12, "hash": "unknown"},
        {"path": "bad.iso", "kind": "iso", "build": 26100, "language": "en-US", "edition": "Pro", "arch": "x64", "win": "11", "size": 20, "hash": "mismatch"},
        {"path": "same.wim", "kind": "wim", "build": 26100, "language": "en-US", "edition": "Pro", "arch": "x64", "win": "11", "size": 30, "hash": "unknown"},
    ]
    best = pick_best(rows, win="11", arch="x64", language="en-US", edition="Pro", min_build=22000)
    assert best is not None
    assert best["path"] == "new.iso"
    outdated = pick_best(rows, win="11", arch="x64", language="en-US", edition="Pro", min_build=30000)
    assert outdated is None


def test_hash_and_parsers(tmp: Path) -> None:
    sample = tmp / "a.iso"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_bytes(b"iso")
    os.environ.pop("MAGIC_ISO_SHA256", None)
    assert hash_verdict(sample) == "unknown"
    os.environ["MAGIC_ISO_SHA256"] = "ab" * 32
    assert hash_verdict(sample, digest="ab" * 32) == "official"
    assert hash_verdict(sample, digest="cd" * 32) == "mismatch"
    os.environ.pop("MAGIC_ISO_SHA256", None)
    assert parse_cversion("MinClient=10.0.26100.1742\n") == 26100
    assert parse_lang_ini("[Available UI Languages]\nen-US = 3\n").lower() == "en-us"
    info = parse_wim_info("Name : Windows 11 Pro\nArchitecture : x64\nLanguage : en-US\nVersion : 10.0.26100.1\n")
    assert info["build"] == 26100
    assert info["arch"] == "x64"
    assert "Pro" in str(info["edition"])


def test_choose_uses_local_file_offline_and_caches(tmp: Path) -> None:
    iso = tmp / "Win11_Pro_en-us_x64.iso"
    _write(iso, 40)
    os.environ["MAGIC_ISO_SCAN_CACHE"] = str(tmp / "scan.json")
    os.environ.pop("MAGIC_ISO", None)
    os.environ.pop("MAGIC_ISO_SHA256", None)
    saved = socket.create_connection

    def down(*_args, **_kwargs):
        raise TimeoutError("timed out")

    socket.create_connection = down  # type: ignore[assignment]
    try:
        def reader(path: Path) -> dict:
            return {
                "path": str(path),
                "kind": "iso",
                "build": 26100,
                "language": "en-US",
                "edition": "Pro",
                "arch": "x64",
                "win": "11",
                "size": 40,
                "hash": "unknown",
            }

        chosen = choose_local_iso(
            win="11",
            arch="x64",
            language="en-US",
            edition="Pro",
            min_build=22000,
            roots=[tmp],
            reader=reader,
            min_bytes=40,
        )
        assert chosen == iso
        cached = load_cache()
        assert any(Path(item["path"]) == iso for item in cached)
        save_cache([{"path": str(iso), "size": 40, "kind": "iso"}], complete=True)
        again = choose_local_iso(
            win="11",
            arch="x64",
            language="en-US",
            edition="Pro",
            roots=None,
            reader=reader,
            min_bytes=40,
        )
        assert again == iso
    finally:
        socket.create_connection = saved  # type: ignore[assignment]
        os.environ.pop("MAGIC_ISO_SCAN_CACHE", None)


def test_override_and_cli(tmp: Path) -> None:
    iso = tmp / "manual.iso"
    _write(iso, 10)
    argv = apply_cli_iso(["--cli", "--iso", str(iso), "--oneclick"])
    assert argv == ["--cli", "--oneclick"]
    assert os.environ["MAGIC_ISO"] == str(iso)
    os.environ["MAGIC_ISO_SCAN_CACHE"] = str(tmp / "cache.json")

    def reader(path: Path) -> dict:
        return {"path": str(path), "kind": "iso", "build": 26100, "language": "en-US", "edition": "Pro", "arch": "x64", "win": "11", "hash": "unknown", "size": 10}

    assert choose_local_iso(win="11", language="en-US", edition="Pro", reader=reader, roots=[]) == iso
    os.environ.pop("MAGIC_ISO", None)
    os.environ.pop("MAGIC_ISO_SCAN_CACHE", None)
    source = (ROOT / "python" / "magic_upgrade.py").read_text(encoding="utf-8")
    assert "Browse" in source
    assert "--iso" in source


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        test_scan_skips_system_dirs_reparse_and_small_files(folder / "scan")
        test_budget_stops_the_walk(folder / "budget")
        test_pick_newest_matching_language_edition_and_arch()
        test_hash_and_parsers(folder / "hash")
        test_choose_uses_local_file_offline_and_caches(folder / "choose")
        test_override_and_cli(folder / "override")
    print("local iso tests OK")
