# Smoke tests for v1.45 modules (no Setup / minimal I/O).
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.confidence import evaluate_confidence  # noqa: E402
from engine.compat_scan import CompatScanResult, soften_block_migration_in_file  # noqa: E402
from engine.self_update import _parse_ver  # noqa: E402


def test_confidence_levels():
    r = MagicMock()
    r.free_gb = 40
    r.sse42 = True
    r.architecture = "x64"
    r.is_legacy = False
    r.partition_style = "GPT"
    r.firmware_likely_ia32 = False
    r.edition_id = "Professional"
    r.ram_gb = 8
    r.is_arm64 = False
    with patch("engine.confidence.save_state"), patch("engine.confidence.log"):
        cs = evaluate_confidence(r, chain_label="test")
    assert cs.level == "green"
    assert cs.score >= 75


def test_parse_ver():
    assert _parse_ver("1.45.0") > _parse_ver("1.44.0")
    assert _parse_ver("v1.2") < _parse_ver("1.10.0")


def test_soften_blockmigration(tmp_path: Path | None = None):
    p = Path(__file__).resolve().parent / "_tmp_compat.xml"
    p.write_text('<x BlockMigration="True" Name="Foo"/>', encoding="utf-8")
    try:
        n = soften_block_migration_in_file(p)
        assert n >= 1
        assert 'BlockMigration="False"' in p.read_text(encoding="utf-8")
    finally:
        p.unlink(missing_ok=True)


if __name__ == "__main__":
    test_confidence_levels()
    test_parse_ver()
    test_soften_blockmigration()
    print("OK test_v145_suite")
