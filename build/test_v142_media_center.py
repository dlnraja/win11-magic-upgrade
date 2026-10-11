# Unit tests: Media Center detection + chain media_offers / win81 skip (no disk I/O).
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.legacy_os import detect_media_center, os_family_from_build  # noqa: E402
from engine.chain import ChainStep, build_version_chain  # noqa: E402
from engine.version_planner import should_skip_chain_step  # noqa: E402


def test_detect_media_center_editions():
    assert detect_media_center(
        "Windows 8.1 Pro with Media Center", "ProfessionalWMC", build=9600
    )
    assert detect_media_center("Windows 8 Pro", "ProfessionalWMC", build=9200)
    assert detect_media_center("Windows 8.1", "ProfessionalMediaCenter", build=9600)
    # On Win10+ do not treat plain Pro as Media Center (ignore leftover files/reg)
    assert not detect_media_center("Windows 10 Pro", "Professional", build=19045)
    assert not detect_media_center("Windows 8.1 Pro", "Professional", build=19045)


def test_os_family():
    assert os_family_from_build(9200) == "win8"
    assert os_family_from_build(9600) == "win81"
    assert os_family_from_build(19045) == "win10"


class _R:
    def __init__(self, **kw):
        self.product_name = kw.get("product_name", "Windows 8.1 Pro with Media Center")
        self.edition_id = kw.get("edition_id", "ProfessionalWMC")
        self.display_version = kw.get("display_version", "")
        self.build = kw.get("build", 9600)
        self.ubr = kw.get("ubr", 0)
        self.is_win11 = kw.get("is_win11", False)
        self.is_win10 = kw.get("is_win10", False)
        self.os_family = kw.get("os_family", "win81")
        self.is_legacy = kw.get("is_legacy", True)
        self.has_media_center = kw.get("has_media_center", True)
        self.architecture = kw.get("architecture", "x64")
        self.needs_intermediate = True
        self.mbr2gpt_available = False
        self.ram_gb = 8
        self.free_gb = 40
        self.locale = "en-US"
        self.disk_number = 0
        self.partition_style = kw.get("partition_style", "GPT")
        self.is_uefi = True
        self.secure_boot = False
        self.cpu_name = "Test"
        self.sse42 = True
        self.tpm_present = False
        self.cpu_64bit = True
        self.bootmgr_arch = "x64"
        self.firmware_likely_ia32 = False
        self.bootmgr_mismatch = False
        self.boot_strategy = "ok"


def test_chain_includes_media_offers_for_mc():
    with patch("engine.legacy_os.media_offers_already_claimed", return_value=False), patch(
        "engine.legacy_os.media_center_eligible_from_state", return_value=False
    ), patch("engine.legacy_os.find_optional_win81_iso", return_value=None):
        steps = build_version_chain(_R())
    kinds = [s.kind for s in steps]
    ids = [s.id for s in steps]
    assert "iso_upgrade" in kinds
    assert "media_offers" in kinds
    assert "win10_22h2" in ids
    assert "win11_latest" in ids
    # media_offers before win11
    assert ids.index("media_offers") < ids.index("win11_latest")


def test_skip_media_offers_when_claimed():
    r = _R(is_win10=True, is_legacy=False, os_family="win10", build=19045, has_media_center=False)
    step = ChainStep(id="media_offers", label="m", kind="media_offers")
    with patch("engine.legacy_os.media_offers_already_claimed", return_value=True):
        assert should_skip_chain_step(step, r, latest_win11=26100)
    with patch("engine.legacy_os.media_offers_already_claimed", return_value=False), patch(
        "engine.legacy_os.media_center_eligible_from_state", return_value=True
    ):
        assert not should_skip_chain_step(step, r, latest_win11=26100)


def test_skip_win81_when_already_81():
    r = _R(build=9600, os_family="win81")
    step = ChainStep(id="win81", label="8.1", kind="iso_upgrade", win="81")
    assert should_skip_chain_step(step, r, latest_win11=26100)


if __name__ == "__main__":
    test_detect_media_center_editions()
    test_os_family()
    test_chain_includes_media_offers_for_mc()
    test_skip_media_offers_when_claimed()
    test_skip_win81_when_already_81()
    print("OK test_v142_media_center")
