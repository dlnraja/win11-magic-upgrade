# Latest Win11 selection, firmware decision matrix, OpenDuet safety (no disk I/O).
from __future__ import annotations

import ctypes
import os
import plistlib
import sys
import tempfile
import types
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="w11m-v154-")
os.environ.pop("MAGIC_KEEP_LEGACY", None)
os.environ.pop("MAGIC_FORCE_MBR2GPT", None)
os.environ.pop("MAGIC_OPENDUET_ALLOW_LARGE", None)
os.environ["SystemDrive"] = "C:"

if not hasattr(ctypes, "windll"):
    class _Dll:
        def __getattr__(self, _name):
            return self

        def __call__(self, *_a, **_k):
            return self

    ctypes.windll = _Dll()  # type: ignore[attr-defined]

if "winreg" not in sys.modules:
    _winreg = types.ModuleType("winreg")
    _winreg.HKEY_LOCAL_MACHINE = 1
    _winreg.HKEY_CURRENT_USER = 2
    _winreg.KEY_READ = 1
    _winreg.REG_SZ = 1

    def _open_key(*_a, **_k):
        raise OSError("winreg stub")

    _winreg.OpenKey = _open_key
    _winreg.CreateKey = _open_key
    _winreg.QueryValueEx = lambda *_a, **_k: ("", 1)
    _winreg.SetValueEx = lambda *_a, **_k: None
    _winreg.CloseKey = lambda *_a, **_k: None
    sys.modules["winreg"] = _winreg

from engine.chain import build_version_chain  # noqa: E402
from engine.firmware_matrix import (  # noqa: E402
    FirmwareSnapshot,
    classify_mbr_sector,
    classify_partition_text,
    decide_boot_plan,
    infer_firmware_bitness,
    mbr2gpt_wanted,
    media_boot_file,
)
from engine.openduet import (  # noqa: E402
    DiskWriteRefused,
    TargetDisk,
    apply_windows_bridge_config,
    authorize_write,
    config_problems,
    confirm_token,
    describe_target,
    diskpart_create_gpt_fat32,
    install_boot_code_files,
    ocvalidate_acceptable,
    parse_list_disk,
    splice_mbr,
    splice_pbr,
    stage_openduet_tree,
)
from engine.version_planner import mapped_version, target_label_for  # noqa: E402
from engine.win11_release import (  # noqa: E402
    FALLBACK_WIN11_BUILD,
    FALLBACK_WIN11_UBR,
    ReleaseTarget,
    floor_target,
    merge_release,
    parse_edition_ids,
    parse_excluded_versions,
    refresh_latest_win11,
    select_latest,
    strip_html,
)


HEALTH = """
<html><body>
<p>Windows 11, version 26H1 is scoped to support new devices and is not designed as a feature update
for existing devices. This version is not offered as an in-place update from 24H2.</p>
<table>
<tr><th>Version</th><th>Servicing option</th><th>Build</th></tr>
<tr><td>26H2</td><td>General Availability</td><td>26300.9711</td></tr>
<tr><td>26H1</td><td>General Availability</td><td>28000.3231</td></tr>
<tr><td>25H2</td><td>General Availability</td><td>26200.9711</td></tr>
<tr><td>24H2</td><td>LTSC</td><td>26100.9711</td></tr>
</table>
</body></html>
"""

TRUNCATED = """
<table>
<tr><th>Version</th><th>Build</th></tr>
<tr><td>25H2</td><td>26200.1000</td></tr>
</table>
"""

DOWNLOAD = """
<html><title>Download Windows 11</title>
<p>Current release: Windows 11 2026 Update | Version 26H2</p>
<select id="product-edition">
<option value="3813">Windows 11 (multi-edition ISO for x64 devices)</option>
</select>
<select id="other"><option value="1111">ignore</option></select>
</html>
"""


def _fetch_factory(health: str, page: str = DOWNLOAD):
    def fetch(url: str) -> str:
        if "release-health" in url or "windows11-release-information" in url:
            return health
        if "software-download" in url:
            return page
        return ""

    return fetch


def test_selects_26h2_not_higher_build_26h1():
    target = refresh_latest_win11(fetch_text=_fetch_factory(HEALTH), save=False, state={})
    assert target.version == "26H2"
    assert target.major == 26300
    assert target.ubr == 9711
    assert "26H1" in target.excluded
    assert target.edition_ids[0] == 3813
    assert target.page_version == "26H2"
    assert 3321 in target.edition_ids


def test_host_on_26h1_stays_on_that_branch():
    target = refresh_latest_win11(
        host_build=28000, fetch_text=_fetch_factory(HEALTH), save=False, state={}
    )
    assert target.version == "26H1"
    assert target.major == 28000
    assert target.source == "host-branch"


def test_truncated_page_keeps_floor_and_newer_cache():
    floor = refresh_latest_win11(fetch_text=_fetch_factory(TRUNCATED, page=""), save=False, state={})
    assert floor.version == "26H2"
    assert floor.major == FALLBACK_WIN11_BUILD
    assert floor.ubr == FALLBACK_WIN11_UBR
    cache = ReleaseTarget(version="26H2", major=26300, ubr=9999, source="cache")
    merged = merge_release(
        live=select_latest([], set(), 0),
        cache=cache,
        floor=floor_target(),
        parsed_versions=set(),
    )
    # empty rows → live is None; newer cache UBR wins over the floor
    assert merged.ubr == 9999
    assert merged.source == "cache"
    failed = refresh_latest_win11(
        fetch_text=lambda _url: (_ for _ in ()).throw(OSError("offline")),
        save=False,
        state={
            "LatestWin11Build": 26300,
            "LatestWin11Label": "26H2",
            "LatestWin11Ubr": 9999,
        },
    )
    assert failed.major == 26300
    assert failed.ubr == 9999
    assert failed.source == "cache"


def test_exclusion_ignores_a_different_version_in_the_gap():
    text = strip_html(
        "version 24H2 or 25H2. version 26H1 is not designed as a feature update."
    )
    excluded = parse_excluded_versions(text)
    assert "26H1" in excluded
    assert "24H2" not in excluded
    ids = parse_edition_ids(DOWNLOAD)
    assert ids == [3813]


def _snap(**kw) -> FirmwareSnapshot:
    base = dict(
        firmware_mode="uefi",
        firmware_bitness="x64",
        uefi_capable=True,
        cpu_bits=64,
        os_arch="x64",
        partition_style="gpt",
        secure_boot="off",
    )
    base.update(kw)
    return FirmwareSnapshot(**base)


def test_firmware_matrix():
    uefi_gpt = decide_boot_plan(_snap())
    assert uefi_gpt.path_id == "uefi_gpt_x64"
    assert uefi_gpt.allow_mbr2gpt is False
    assert media_boot_file(_snap()) == "bootx64.efi"

    uefi_mbr = decide_boot_plan(_snap(partition_style="mbr"))
    assert uefi_mbr.path_id == "uefi_mbr_convert"
    assert uefi_mbr.allow_mbr2gpt is True

    hybrid = decide_boot_plan(_snap(partition_style="hybrid"))
    assert hybrid.path_id == "uefi_hybrid_keep"
    assert hybrid.allow_mbr2gpt is False

    unknown = decide_boot_plan(_snap(partition_style="unknown"))
    assert unknown.path_id == "uefi_style_unknown"
    assert unknown.allow_mbr2gpt is False

    mismatch = decide_boot_plan(_snap(os_arch="x86", partition_style="mbr"))
    assert mismatch.path_id == "clean_x64_on_x64_firmware"
    assert mismatch.boot_files == ("bootx64.efi",)
    assert mismatch.allow_mbr2gpt is False
    assert mismatch.win11_inplace is False

    ia32 = decide_boot_plan(
        _snap(firmware_bitness="ia32", partition_style="mbr", firmware_likely_ia32=True)
    )
    assert ia32.path_id == "hybrid_ia32_csmwrap"
    assert ia32.boot_files == ("bootia32.efi", "bootmgr")
    assert ia32.allow_mbr2gpt is False
    assert ia32.allow_hybrid_csmwrap is True
    assert ia32.suggest_openduet is False

    ia32_sb = decide_boot_plan(_snap(firmware_bitness="ia32", secure_boot="on"))
    assert ia32_sb.secure_boot_must_off is True

    ia32_os = decide_boot_plan(_snap(firmware_bitness="ia32", os_arch="x86"))
    assert ia32_os.path_id == "hybrid_ia32_then_clean"
    assert ia32_os.win11_inplace is False

    legacy = decide_boot_plan(
        _snap(
            firmware_mode="bios",
            firmware_bitness="legacy",
            uefi_capable=False,
            partition_style="mbr",
            secure_boot="unsupported",
        )
    )
    assert legacy.path_id == "legacy_mbr_keep"
    assert legacy.allow_mbr2gpt is False
    assert legacy.suggest_openduet is True
    assert legacy.bcd_mode == "BIOS"

    csm = decide_boot_plan(
        _snap(
            firmware_mode="bios",
            firmware_bitness="legacy",
            uefi_capable=True,
            partition_style="mbr",
        )
    )
    assert csm.path_id == "csm_convert_mbr2gpt"
    assert csm.allow_mbr2gpt is True

    csm_gpt = decide_boot_plan(
        _snap(firmware_mode="bios", firmware_bitness="legacy", uefi_capable=True, partition_style="gpt")
    )
    assert csm_gpt.path_id == "csm_switch_firmware_uefi"
    assert csm_gpt.allow_mbr2gpt is False

    csm_unknown = decide_boot_plan(
        _snap(
            firmware_mode="bios",
            firmware_bitness="legacy",
            uefi_capable=True,
            partition_style="unknown",
        )
    )
    assert csm_unknown.path_id == "csm_style_unknown"
    assert csm_unknown.allow_mbr2gpt is False

    legacy_gpt = decide_boot_plan(
        _snap(
            firmware_mode="bios",
            firmware_bitness="legacy",
            uefi_capable=False,
            partition_style="gpt",
        )
    )
    assert legacy_gpt.path_id == "legacy_gpt_openduet"
    assert legacy_gpt.suggest_openduet is True

    bios_hybrid = decide_boot_plan(
        _snap(
            firmware_mode="bios",
            firmware_bitness="legacy",
            uefi_capable=False,
            partition_style="hybrid",
        )
    )
    assert bios_hybrid.path_id == "bios_hybrid_keep"
    assert bios_hybrid.suggest_openduet is True
    assert bios_hybrid.allow_mbr2gpt is False

    cpu32 = decide_boot_plan(_snap(cpu_bits=32, os_arch="x86"))
    assert cpu32.path_id == "cpu32_max_win10"
    assert cpu32.suggest_openduet is False

    arm = decide_boot_plan(_snap(os_arch="arm64"))
    assert arm.path_id == "arm64_unsupported"

    forced = decide_boot_plan(
        _snap(
            firmware_mode="bios",
            firmware_bitness="legacy",
            uefi_capable=False,
            partition_style="mbr",
            force_mbr2gpt=True,
        )
    )
    assert forced.allow_mbr2gpt is True
    kept = decide_boot_plan(_snap(partition_style="mbr", keep_legacy=True, force_mbr2gpt=True))
    assert kept.allow_mbr2gpt is False


def test_sector_and_bitness_classification():
    def sector(types: list[int]) -> bytes:
        buf = bytearray(512)
        buf[510:512] = b"\x55\xaa"
        for i, ptype in enumerate(types):
            buf[446 + i * 16 + 4] = ptype
        return bytes(buf)

    assert classify_mbr_sector(sector([0xEE])) == "gpt"
    assert classify_mbr_sector(sector([0xEE, 0x07])) == "hybrid"
    assert classify_mbr_sector(sector([0x07])) == "mbr"
    assert classify_mbr_sector(b"\x00" * 512) == "unknown"
    assert classify_partition_text("Partition style : GPT") == "gpt"
    assert "MBR" in "style MBR" and classify_partition_text("style MBR and GPT") == "hybrid"
    assert (
        infer_firmware_bitness(
            is_uefi=True, os_arch="x86", esp_has_bootx64=True, esp_has_bootia32=False
        )
        == "x64"
    )
    assert (
        infer_firmware_bitness(
            is_uefi=True, os_arch="x86", esp_has_bootia32=True, esp_has_bootx64=False
        )
        == "ia32"
    )
    assert infer_firmware_bitness(is_uefi=False, os_arch="x64") == "legacy"
    assert media_boot_file(_snap(firmware_bitness="ia32")) == "bootia32.efi"
    assert media_boot_file(_snap(firmware_bitness="legacy", firmware_mode="bios")) == "bootmgr"


def test_openduet_config_and_sectors():
    cfg = {
        "Misc": {
            "Security": {"ScanPolicy": 17760515, "SecureBootModel": "Default", "Vault": "Secure"},
            "BlessOverride": [],
        },
        "UEFI": {
            "Input": {"KeySupport": True},
            "Drivers": [
                {"Path": "HfsPlus.efi", "Enabled": True, "Arguments": "", "Comment": "", "LoadEarly": False},
                {"Path": "OpenUsbKbDxe.efi", "Enabled": False, "Arguments": "", "Comment": "", "LoadEarly": False},
            ],
        },
    }
    apply_windows_bridge_config(cfg, bless_path=r"\EFI\Microsoft\Boot\bootmgfw.efi")
    assert cfg["Misc"]["BlessOverride"] == []
    assert config_problems(cfg) == []
    apply_windows_bridge_config(cfg, bless_path=r"EFI/fedora/grubx64.efi")
    assert r"\EFI\fedora\grubx64.efi" in cfg["Misc"]["BlessOverride"]
    assert ocvalidate_acceptable(0, "No issues found")[0] is True
    redundant = (
        r"Misc->BlessOverride: \EFI\Microsoft\Boot\bootmgfw.efi is redundant!"
        "\nFound 1 issue requiring attention."
    )
    assert ocvalidate_acceptable(1, redundant) == (True, "windows_bless_redundant")
    assert ocvalidate_acceptable(1, "Found 2 issues requiring attention.")[0] is False

    existing = bytearray(b"\x33" * 512)
    existing[510:512] = b"\x55\xaa"
    existing[446:510] = b"\xAB" * 64
    boot0 = bytes([0x11]) * 512
    spliced = splice_mbr(bytes(existing), boot0)
    assert spliced[:446] == boot0[:446]
    assert spliced[446:510] == b"\xAB" * 64
    assert spliced[510:512] == b"\x55\xaa"

    fat = bytearray(b"\x22" * 512)
    fat[3:90] = b"\x44" * 87
    fat[510:512] = b"\x55\xaa"
    boot1 = bytes([0x66]) * 512
    pbr = splice_pbr(bytes(fat), boot1, rng=lambda n: b"\x99" * n)
    assert pbr[3:90] == b"\x44" * 87
    assert pbr[496:510] == b"\x99" * 14
    assert pbr[510:512] == b"\x55\xaa"
    assert pbr[0] == 0x66

    with tempfile.TemporaryDirectory() as td:
        mbr = Path(td) / "mbr"
        pbr_path = Path(td) / "pbr"
        mbr.write_bytes(bytes(existing))
        pbr_path.write_bytes(bytes(fat))
        install_boot_code_files(str(mbr), str(pbr_path), boot0, boot1, rng=lambda n: b"\x77" * n)
        assert mbr.read_bytes()[:446] == boot0[:446]
        assert mbr.read_bytes()[446:510] == b"\xAB" * 64
        assert pbr_path.read_bytes()[3:90] == b"\x44" * 87


def test_authorize_write_refusals():
    usb = TargetDisk(number=2, size_bytes=8 * 1024**3, bus="usb", boot_disk=False)
    token = confirm_token(usb, partition=False)
    assert token == "2|8589934592|usb|boot=no|part=no"
    assert authorize_write(
        usb, confirm="", dry_run=True, allow_fixed=False, partition=False, system_disk=0
    ) == "dry-run"
    assert authorize_write(
        usb, confirm=token, dry_run=False, allow_fixed=False, partition=False, system_disk=0
    ) == "commit"
    try:
        authorize_write(
            usb, confirm="nope", dry_run=False, allow_fixed=False, partition=False, system_disk=0
        )
        raise AssertionError("token mismatch should refuse")
    except DiskWriteRefused:
        pass
    try:
        authorize_write(
            TargetDisk(0, 100, "usb", True),
            confirm="0|100|usb|boot=yes|part=no",
            dry_run=False,
            allow_fixed=False,
            partition=False,
            system_disk=1,
        )
        raise AssertionError("boot disk should refuse")
    except DiskWriteRefused:
        pass
    try:
        authorize_write(
            TargetDisk(1, 100, "usb", False),
            confirm="ignored",
            dry_run=False,
            allow_fixed=False,
            partition=False,
            system_disk=1,
        )
        raise AssertionError("system disk should refuse")
    except DiskWriteRefused:
        pass
    try:
        authorize_write(
            TargetDisk(3, 100, "sata", False),
            confirm="",
            dry_run=False,
            allow_fixed=False,
            partition=False,
            system_disk=0,
        )
        raise AssertionError("fixed disk should refuse")
    except DiskWriteRefused:
        pass
    huge = TargetDisk(4, 200 * 1024**3, "usb", False)
    try:
        authorize_write(
            huge, confirm="", dry_run=False, allow_fixed=False, partition=True, system_disk=0
        )
        raise AssertionError("large partition should refuse")
    except DiskWriteRefused:
        pass
    try:
        authorize_write(
            usb,
            confirm="",
            dry_run=True,
            allow_fixed=False,
            partition=False,
            system_disk=0,
            volume_letter="C",
        )
        raise AssertionError("system drive letter should refuse")
    except DiskWriteRefused:
        pass
    try:
        authorize_write(
            usb, confirm="", dry_run=True, allow_fixed=False, partition=False, system_disk=None
        )
        raise AssertionError("unknown system disk should refuse")
    except DiskWriteRefused:
        pass

    listed = parse_list_disk("Disk 1    Online     28 GB      0 B\nDisque 2    En ligne   16 Go   *\n")
    assert listed[0].number == 1 and listed[0].size_bytes == 28 * 1024**3
    assert listed[1].style == "gpt"
    described = describe_target(
        "Disk 1    Online     28 GB      0 B\n",
        "SanDisk\nType : USB\nBoot Disk : No\n",
        1,
    )
    assert described.bus == "usb" and described.boot_disk is False
    script = diskpart_create_gpt_fat32(1, "E")
    assert "clean" in script and "convert gpt" in script and "format fs=fat32" in script


def test_stage_tree_leaves_windows_bless_empty():
    sample = {
        "Misc": {
            "Security": {"ScanPolicy": 1, "SecureBootModel": "Default", "Vault": "Secure"},
            "BlessOverride": [],
        },
        "UEFI": {
            "Input": {"KeySupport": True},
            "Drivers": [
                {"Path": "HfsPlus.efi", "Enabled": True},
                {"Path": "OpenUsbKbDxe.efi", "Enabled": False},
            ],
        },
    }
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        zpath = base / "oc.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("Docs/Sample.plist", plistlib.dumps(sample))
            zf.writestr("Utilities/LegacyBoot/boot0", b"\x01" * 512)
            zf.writestr("Utilities/LegacyBoot/boot1f32", b"\x02" * 512)
            zf.writestr("Utilities/LegacyBoot/bootX64", b"DUET")
            zf.writestr("Utilities/LegacyBoot/bootX64-blockio", b"BLOCK")
            zf.writestr("X64/EFI/OC/OpenCore.efi", b"OC")
            zf.writestr("Utilities/ocvalidate/ocvalidate.linux", b"tiny")
        dest = stage_openduet_tree(
            zpath, base / "stick", bless_path=r"\EFI\Microsoft\Boot\bootmgfw.efi"
        )
        cfg = plistlib.loads((dest / "EFI" / "OC" / "config.plist").read_bytes())
        assert cfg["Misc"]["BlessOverride"] == []
        assert cfg["Misc"]["Security"]["ScanPolicy"] == 0
        assert (dest / "boot").read_bytes() == b"DUET"
        assert (dest / "LegacyBoot" / "bootX64").read_bytes() == b"DUET"
        readme = (dest / "OPENDUET-README.txt").read_text(encoding="utf-8")
        assert "chainesteve.be" in readme
        assert "ajouter-uefi-pc-bios-legacy" in readme


class _R:
    def __init__(self, **kw):
        self.product_name = "Windows 10"
        self.edition_id = "Professional"
        self.display_version = "22H2"
        self.build = kw.get("build", 19045)
        self.ubr = kw.get("ubr", 0)
        self.is_win11 = kw.get("is_win11", False)
        self.is_win10 = kw.get("is_win10", True)
        self.os_family = kw.get("os_family", "win10")
        self.is_legacy = False
        self.has_media_center = False
        self.architecture = kw.get("architecture", "x64")
        self.needs_intermediate = False
        self.mbr2gpt_available = kw.get("mbr2gpt_available", True)
        self.ram_gb = 8
        self.free_gb = 40
        self.locale = "en-US"
        self.disk_number = 0
        self.partition_style = kw.get("partition_style", "GPT")
        self.is_uefi = kw.get("is_uefi", True)
        self.secure_boot = False
        self.cpu_name = "Test"
        self.sse42 = kw.get("sse42", True)
        self.tpm_present = False
        self.cpu_64bit = kw.get("cpu_64bit", True)
        self.bootmgr_arch = "x64"
        self.firmware_likely_ia32 = kw.get("firmware_likely_ia32", False)
        self.bootmgr_mismatch = False
        self.boot_strategy = "ok"
        self.uefi_capable = kw.get("uefi_capable", True)
        self.firmware_bitness = kw.get("firmware_bitness", "x64")
        self.partition_hybrid = kw.get("partition_hybrid", False)
        self.is_arm64 = False


def _ids(report) -> list[str]:
    return [s.id for s in build_version_chain(report)]


def test_chain_firmware_and_latest_label():
    uefi_mbr = _ids(_R(partition_style="MBR", is_uefi=True, uefi_capable=True, firmware_bitness="x64"))
    assert "mbr2gpt" in uefi_mbr
    assert "openduet_prepare" not in uefi_mbr
    assert "win11_latest" in uefi_mbr
    assert uefi_mbr.index("win11_cumulative") > uefi_mbr.index("win11_latest")

    gpt = _ids(_R(partition_style="GPT"))
    assert "mbr2gpt" not in gpt
    assert "openduet_prepare" not in gpt

    legacy = _ids(
        _R(
            partition_style="MBR",
            is_uefi=False,
            uefi_capable=False,
            firmware_bitness="legacy",
        )
    )
    assert "mbr2gpt" not in legacy
    assert "openduet_prepare" in legacy

    os.environ["MAGIC_KEEP_LEGACY"] = "1"
    try:
        kept = _ids(_R(partition_style="MBR", is_uefi=True, uefi_capable=True))
    finally:
        os.environ.pop("MAGIC_KEEP_LEGACY", None)
    assert "mbr2gpt" not in kept

    os.environ["MAGIC_FORCE_MBR2GPT"] = "1"
    try:
        forced = _ids(
            _R(
                partition_style="MBR",
                is_uefi=False,
                uefi_capable=False,
                firmware_bitness="legacy",
            )
        )
    finally:
        os.environ.pop("MAGIC_FORCE_MBR2GPT", None)
    assert "mbr2gpt" in forced

    os.environ["MAGIC_KEEP_LEGACY"] = "1"
    os.environ["MAGIC_FORCE_MBR2GPT"] = "1"
    try:
        both = mbr2gpt_wanted(_R(partition_style="MBR", is_uefi=True))
    finally:
        os.environ.pop("MAGIC_KEEP_LEGACY", None)
        os.environ.pop("MAGIC_FORCE_MBR2GPT", None)
    assert both is False

    ia32 = _ids(
        _R(
            partition_style="MBR",
            is_uefi=True,
            firmware_likely_ia32=True,
            firmware_bitness="ia32",
        )
    )
    assert "hybrid_ia32" in ia32
    assert "mbr2gpt" not in ia32

    no_sse = _ids(_R(sse42=False, partition_style="MBR", is_uefi=False, uefi_capable=False))
    assert "openduet_prepare" not in no_sse
    assert "win11_latest" not in no_sse

    cpu32 = _ids(
        _R(
            architecture="x86",
            cpu_64bit=False,
            is_uefi=False,
            uefi_capable=False,
            firmware_bitness="legacy",
            partition_style="MBR",
        )
    )
    assert "openduet_prepare" not in cpu32
    assert "win11_latest" not in cpu32

    ahead = _ids(_R(is_win11=True, is_win10=False, build=28000, os_family="win11"))
    assert ahead == ["done"]

    current = _ids(_R(is_win11=True, is_win10=False, build=26300, ubr=9711, os_family="win11"))
    assert current == ["done"]

    behind_cu = _ids(_R(is_win11=True, is_win10=False, build=26300, ubr=100, os_family="win11"))
    assert behind_cu == ["win11_cumulative"]

    older = build_version_chain(_R(is_win11=True, is_win10=False, build=26200, os_family="win11"))
    latest = next(s for s in older if s.id == "win11_latest")
    assert "latest (26H2+)" in latest.label
    assert "25H2" in latest.label  # source build, not the target
    assert mapped_version(26300) == "26H2"
    assert target_label_for(26300) == "26H2"


if __name__ == "__main__":
    test_selects_26h2_not_higher_build_26h1()
    test_host_on_26h1_stays_on_that_branch()
    test_truncated_page_keeps_floor_and_newer_cache()
    test_exclusion_ignores_a_different_version_in_the_gap()
    test_firmware_matrix()
    test_sector_and_bitness_classification()
    test_openduet_config_and_sectors()
    test_authorize_write_refusals()
    test_stage_tree_leaves_windows_bless_empty()
    test_chain_firmware_and_latest_label()
    print("OK test_v154_latest_and_firmware")
