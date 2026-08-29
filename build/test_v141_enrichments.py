# Unit tests: ISO lang matching, recovery codes, version skip planner (no disk I/O).
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.iso_inspect import host_locale_matches_iso, parse_lang_ini  # noqa: E402
from engine.setup_recovery import SUBCODE_ACTIONS, TOP_CODE_ACTIONS, _CODE_RE  # noqa: E402
from engine.version_planner import should_skip_chain_step  # noqa: E402
from engine.chain import ChainStep  # noqa: E402


class _R:
    def __init__(self, **kw):
        self.is_win11 = kw.get("is_win11", False)
        self.build = kw.get("build", 19045)
        self.partition_style = kw.get("partition_style", "GPT")


def test_lang_ini_and_match():
    text = """
[Available UI Languages]
en-US = 1
fr-FR = 1
"""
    langs = parse_lang_ini(text)
    assert "en-US" in langs and "fr-FR" in langs
    assert host_locale_matches_iso("fr-FR", langs)
    assert host_locale_matches_iso("fr-CA", langs)  # same language family
    assert not host_locale_matches_iso("de-DE", langs)
    assert host_locale_matches_iso("en-US", [])  # unknown ISO langs → allow


def test_recovery_new_codes():
    assert "0x20009" in SUBCODE_ACTIONS
    assert "0x80070002" in TOP_CODE_ACTIONS
    assert "0xc190012e" in TOP_CODE_ACTIONS
    sample = "Fail 0xC190012E and 0x80070002-0x20009 and 0xC1420121"
    tops = []
    for m in _CODE_RE.finditer(sample):
        tops.append(tuple(g for g in m.groups() if g))
    flat = " ".join(str(t) for t in tops).lower()
    assert "c190012e" in flat or "0xc190012e" in flat
    assert "80070002" in flat


def test_skip_chain():
    r = _R(is_win11=True, build=26100, partition_style="GPT")
    assert should_skip_chain_step(
        ChainStep(id="mbr2gpt", label="m", kind="mbr2gpt"), r, latest_win11=26100
    )
    assert should_skip_chain_step(
        ChainStep(id="win11_latest", label="w", kind="iso_upgrade"), r, latest_win11=26100
    )
    r2 = _R(is_win11=False, build=19041, partition_style="MBR")
    assert not should_skip_chain_step(
        ChainStep(id="mbr2gpt", label="m", kind="mbr2gpt"), r2, latest_win11=26100
    )


if __name__ == "__main__":
    test_lang_ini_and_match()
    test_recovery_new_codes()
    test_skip_chain()
    print("OK test_v141_enrichments")
