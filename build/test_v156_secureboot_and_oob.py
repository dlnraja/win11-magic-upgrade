# Secure Boot CA 2023 gate and 2026-10 OOB / D-preview selection. No disk I/O.
from __future__ import annotations

import ctypes
import os
import struct
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="w11m-v156-")
os.environ.pop("MAGIC_WIN11_PREVIEW", None)

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

    def _open_key(*_a, **_k):
        raise OSError("winreg stub")

    _winreg.OpenKey = _open_key
    _winreg.QueryValueEx = lambda *_a, **_k: (0, 1)
    _winreg.CloseKey = lambda *_a, **_k: None
    sys.modules["winreg"] = _winreg

from engine.firmware_matrix import FirmwareSnapshot, decide_boot_plan  # noqa: E402
from engine.secureboot_certs import (  # noqa: E402
    CN_WINDOWS_PCA_2011,
    CN_WINDOWS_UEFI_CA_2023,
    OFFICIAL_DBX_URLS,
    SIGNER_WINDOWS_PCA_2011,
    SIGNER_WINDOWS_UEFI_CA_2023,
    BootRewriteDecision,
    LoaderIdentity,
    SecureBootCertState,
    db_contains_windows_uefi_ca_2023,
    dbx_revokes_windows_pca_2011,
    efi_write_block_reason,
    fetch_official_dbx,
    find_media_loader,
    identify_loader,
    parse_bootmgr_svn,
    pe_security_directory,
    rewrite_decision,
    setup_media_block_reason,
    snapshot_restore_allowed,
)
from engine.version_planner import evaluate_host, note_for_eos_build  # noqa: E402
from engine.win11_release import (  # noqa: E402
    FALLBACK_WIN11_UBR,
    ReleaseTarget,
    classify_update,
    floor_target,
    merge_release,
    parse_end_of_updates,
    parse_excluded_versions,
    parse_servicing_rows,
    refresh_latest_win11,
    select_latest,
    strip_html,
)

FIXTURE = (ROOT / "build" / "fixtures" / "win11_release_health_2026-11-02.html").read_text(
    encoding="utf-8"
)
NOVEMBER = (ROOT / "build" / "fixtures" / "win11_release_health_2026-11-16.html").read_text(
    encoding="utf-8"
)
_PCA2011_TBS_SHA256 = bytes.fromhex(
    "4e80be107c860de896384b3eff50504dc2d76ac7151df3102a4450637a032146"
)


def _state(**kwargs) -> SecureBootCertState:
    base = dict(
        secure_boot="on",
        db_has_windows_uefi_ca_2023=False,
        db_has_windows_pca_2011=True,
        pca2011_revoked_in_dbx=False,
        firmware_min_svn=None,
        uefica2023_status="NotStarted",
        uefica2023_error=0,
        windows_uefi_ca_2023_capable=0,
        source="test",
    )
    base.update(kwargs)
    return SecureBootCertState(**base)


def _loader(*signers: str, svn: int | None = None) -> LoaderIdentity:
    return LoaderIdentity(frozenset(signers), svn, True)


def _snap(**kwargs) -> FirmwareSnapshot:
    base = dict(
        firmware_mode="uefi",
        firmware_bitness="x64",
        uefi_capable=True,
        cpu_bits=64,
        os_arch="x64",
        partition_style="gpt",
        secure_boot="on",
    )
    base.update(kwargs)
    return FirmwareSnapshot(**base)


def _pe(cert: bytes) -> bytes:
    dos = bytearray(128)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 128)
    coff = bytearray(20)
    struct.pack_into("<H", coff, 0, 0x8664)
    struct.pack_into("<H", coff, 16, 240)
    opt = bytearray(240)
    struct.pack_into("<H", opt, 0, 0x20B)
    struct.pack_into("<I", opt, 108, 16)
    struct.pack_into("<II", opt, 112 + 4 * 8, 128 + 4 + 20 + 240, len(cert))
    return bytes(dos) + b"PE\0\0" + bytes(coff) + bytes(opt) + cert


def test_october_2026_rows_prefer_oob_over_d_preview():
    assert classify_update("General Availability Channel", "2026-10 D") == "preview"
    assert classify_update("General Availability Channel", "2026-10 OOB") == "oob"
    assert classify_update("General Availability Channel", "2026-10 B") == "security"
    assert classify_update("LTSC • General Availability Channel", "2026-10 OOB") == "ltsc"
    rows = parse_servicing_rows(FIXTURE)
    by_key = {(row.version, row.update_class): (row.major, row.ubr) for row in rows}
    assert by_key[("26H2", "preview")] == (26300, 9818)
    assert by_key[("26H2", "oob")] == (26300, 9725)
    assert by_key[("26H2", "security")] == (26300, 9711)
    assert by_key[("26H1", "preview")] == (28000, 3365)
    assert by_key[("26H1", "oob")] == (28000, 3237)
    assert by_key[("25H2", "oob")] == (26200, 9725)
    assert by_key[("25H2", "preview")] == (26200, 9818)
    assert by_key[("24H2", "oob")] == (26100, 9725)
    assert by_key[("23H2", "oob")] == (22631, 7654)
    excluded = parse_excluded_versions(strip_html(FIXTURE))
    assert "26H1" in excluded
    assert "24H2" not in excluded
    chosen = select_latest(rows, excluded)
    assert chosen is not None and chosen.ubr == 9725
    preview_row = select_latest(rows, excluded, allow_preview=True)
    assert preview_row is not None and preview_row.ubr == 9818
    host_row = select_latest(rows, excluded, host_build=28000)
    assert host_row is not None and host_row.ubr == 3237
    assert host_row.source == "host-branch"

    def fetch(url: str) -> str:
        if "release-health" in url or "windows11-release-information" in url:
            return FIXTURE
        return ""

    # The October page is older than the shipped November floor, so refresh
    # keeps the floor. The row asserts above still prove OOB beats the D preview.
    target = refresh_latest_win11(fetch_text=fetch, save=False, state={})
    assert target.version == "26H2"
    assert target.major == 26300
    assert target.ubr == FALLBACK_WIN11_UBR
    assert target.source == "builtin-floor"
    assert "26H1" in target.excluded
    preview = refresh_latest_win11(
        fetch_text=fetch, save=False, state={}, allow_preview=True
    )
    assert preview.version == "26H2"
    assert preview.ubr == FALLBACK_WIN11_UBR
    host = refresh_latest_win11(
        host_build=28000, fetch_text=fetch, save=False, state={}
    )
    assert host.version == "26H1"
    assert host.major == 28000
    assert host.ubr == 3237
    assert host.source == "host-branch"


def test_stale_cache_ubr_does_not_hide_the_oob_floor():
    stale = ReleaseTarget(version="26H2", major=26300, ubr=9711, source="cache")
    merged = merge_release(
        live=None,
        cache=stale,
        floor=floor_target(),
        parsed_versions=set(),
    )
    assert merged.ubr == FALLBACK_WIN11_UBR
    assert merged.source == "builtin-floor"
    newer = ReleaseTarget(version="26H2", major=26300, ubr=9999, source="cache")
    kept = merge_release(
        live=None, cache=newer, floor=floor_target(), parsed_versions=set()
    )
    assert kept.ubr == 9999
    assert kept.source == "cache"


def test_db_and_dbx_markers():
    db = b"prefix " + CN_WINDOWS_UEFI_CA_2023.encode("ascii") + b" suffix"
    assert db_contains_windows_uefi_ca_2023(db) is True
    assert db_contains_windows_uefi_ca_2023("Windows UEFI CA 2023".encode("utf-16le")) is True
    assert dbx_revokes_windows_pca_2011(b"\x00" * 8 + _PCA2011_TBS_SHA256 + b"\x00" * 4) is True
    assert dbx_revokes_windows_pca_2011(b"unrelated") is False
    text = b"BOOTMGRSECURITYVERSIONNUMBER,0x70000\nBOOTMGRSECURITYVERSIONNUMBER,0xb0000"
    assert parse_bootmgr_svn(text) == 0xB0000
    utf16 = "BOOTMGRSECURITYVERSIONNUMBER,0x70000".encode("utf-16le")
    assert parse_bootmgr_svn(utf16) == 0x70000


def test_pe_signer_and_rewrite_matrix():
    cert = (
        CN_WINDOWS_UEFI_CA_2023.encode("ascii")
        + b" "
        + CN_WINDOWS_PCA_2011.encode("ascii")
        + b" BOOTMGRSECURITYVERSIONNUMBER,0xb0000"
    )
    image = _pe(cert)
    assert pe_security_directory(image) == cert
    ident = identify_loader(image)
    assert SIGNER_WINDOWS_UEFI_CA_2023 in ident.signers
    assert SIGNER_WINDOWS_PCA_2011 in ident.signers
    assert ident.svn == 0xB0000

    only_2023 = _loader(SIGNER_WINDOWS_UEFI_CA_2023, svn=0xB0000)
    only_2011 = _loader(SIGNER_WINDOWS_PCA_2011, svn=0x70000)
    dual = _loader(SIGNER_WINDOWS_UEFI_CA_2023, SIGNER_WINDOWS_PCA_2011, svn=0xB0000)
    no_db = _state(db_has_windows_uefi_ca_2023=False, pca2011_revoked_in_dbx=False)
    with_db = _state(
        db_has_windows_uefi_ca_2023=True,
        windows_uefi_ca_2023_capable=2,
        uefica2023_status="Updated",
    )
    revoked = _state(db_has_windows_uefi_ca_2023=False, pca2011_revoked_in_dbx=True)
    revoked_with_db = _state(db_has_windows_uefi_ca_2023=True, pca2011_revoked_in_dbx=True)
    svn_floor = _state(
        db_has_windows_uefi_ca_2023=True,
        firmware_min_svn=0xB0000,
    )

    assert rewrite_decision(no_db, only_2023).allow is False
    assert rewrite_decision(with_db, only_2023).allow is True
    assert rewrite_decision(no_db, dual).allow is True
    assert rewrite_decision(revoked, only_2011).allow is False
    assert rewrite_decision(revoked, dual).allow is False
    assert rewrite_decision(revoked_with_db, dual).allow is True
    assert rewrite_decision(no_db, only_2011).allow is True
    old_2023 = _loader(SIGNER_WINDOWS_UEFI_CA_2023, svn=0x70000)
    assert rewrite_decision(svn_floor, old_2023).allow is False
    assert "0x70000" in rewrite_decision(svn_floor, old_2023).reason
    assert rewrite_decision(svn_floor, only_2023).allow is True
    off = _state(secure_boot="off")
    assert rewrite_decision(off, only_2023).allow is True
    assert rewrite_decision(no_db, only_2011, kind="bios_bootmgr").allow is True
    assert efi_write_block_reason("BIOS", file_bytes=b"unsigned", state=no_db) == ""
    only_bytes = _pe(
        CN_WINDOWS_UEFI_CA_2023.encode("ascii") + b" BOOTMGRSECURITYVERSIONNUMBER,0xb0000"
    )
    blocked = efi_write_block_reason("UEFI", file_bytes=only_bytes, state=no_db)
    assert "Windows UEFI CA 2023" in blocked
    assert efi_write_block_reason("UEFI", file_bytes=only_bytes, state=with_db) == ""


def test_snapshot_restore_skips_revoked_loader_and_keeps_unparsed_one():
    revoked = _state(pca2011_revoked_in_dbx=True, db_has_windows_uefi_ca_2023=False)
    old = _pe(CN_WINDOWS_PCA_2011.encode("ascii"))
    decision = snapshot_restore_allowed(old, revoked)
    assert decision.allow is False
    assert decision.proven is True
    mystery = _pe(b"not-a-microsoft-certificate")
    kept = snapshot_restore_allowed(mystery, revoked)
    assert kept.allow is True
    assert kept.proven is False


def test_boot_plan_holds_uefi_rewrite_and_leaves_legacy_paths():
    unsafe = _loader(SIGNER_WINDOWS_UEFI_CA_2023, svn=0xB0000)
    state = _state(db_has_windows_uefi_ca_2023=False)
    held = decide_boot_plan(_snap(), cert_state=state, target_loader=unsafe)
    assert held.path_id == "uefi_gpt_x64_cert_hold"
    assert held.allow_mbr2gpt is False
    assert held.win11_inplace is False
    assert "keep_current_bootmgr" in held.actions
    assert "bcdboot_uefi" not in held.actions
    assert held.blockers

    csm = decide_boot_plan(
        _snap(firmware_mode="bios", firmware_bitness="legacy", partition_style="mbr", secure_boot="off"),
        cert_state=state,
        target_loader=unsafe,
    )
    assert csm.path_id == "csm_convert_mbr2gpt_cert_hold"
    assert csm.allow_mbr2gpt is False

    legacy = decide_boot_plan(
        _snap(
            firmware_mode="bios",
            firmware_bitness="legacy",
            uefi_capable=False,
            partition_style="mbr",
            secure_boot="unsupported",
        ),
        cert_state=state,
        target_loader=unsafe,
    )
    assert legacy.path_id == "legacy_mbr_keep"
    assert "suggest_openduet" in legacy.actions or legacy.suggest_openduet is True

    ia32 = decide_boot_plan(
        _snap(firmware_bitness="ia32", secure_boot="on"),
        cert_state=state,
        target_loader=unsafe,
    )
    assert ia32.path_id == "hybrid_ia32_csmwrap"
    assert ia32.secure_boot_must_off is True

    cpu32 = decide_boot_plan(
        _snap(cpu_bits=32, os_arch="x86", secure_boot="on"),
        cert_state=state,
        target_loader=unsafe,
    )
    assert cpu32.path_id == "cpu32_max_win10"

    trusted = decide_boot_plan(
        _snap(secure_boot="on"),
        cert_state=_state(db_has_windows_uefi_ca_2023=True),
        target_loader=unsafe,
    )
    assert trusted.path_id == "uefi_gpt_x64"
    assert trusted.win11_inplace is True


def test_setup_media_refuses_a_2023_only_loader_when_db_lacks_it():
    state = _state(db_has_windows_uefi_ca_2023=False)
    payload = _pe(CN_WINDOWS_UEFI_CA_2023.encode("ascii"))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        media = root / "EFI" / "Boot"
        media.mkdir(parents=True)
        (media / "bootx64.efi").write_bytes(payload)
        assert find_media_loader(root) == media / "bootx64.efi"
        reason = setup_media_block_reason(root, state)
        assert "Windows UEFI CA 2023" in reason
        assert setup_media_block_reason(root, _state(secure_boot="off")) == ""
        assert isinstance(reason, str)


def test_november_2026_b_is_the_floor():
    assert classify_update("General Availability Channel", "2026-11 B") == "security"
    rows = parse_servicing_rows(NOVEMBER)
    by_key = {(row.version, row.update_class): (row.major, row.ubr) for row in rows}
    assert by_key[("26H2", "security")] == (26300, 9974)
    assert by_key[("26H2", "preview")] == (26300, 9818)
    assert by_key[("26H2", "oob")] == (26300, 9725)
    assert by_key[("26H1", "security")] == (28000, 3501)
    assert by_key[("25H2", "security")] == (26200, 9974)
    assert by_key[("24H2", "security")] == (26100, 9974)
    assert by_key[("23H2", "oob")] == (22631, 7714)
    assert by_key[("23H2", "security")] == (22631, 7711)
    ended = parse_end_of_updates(NOVEMBER)
    assert ended == {"23H2"}
    excluded = parse_excluded_versions(strip_html(NOVEMBER))
    assert "26H1" in excluded

    def fetch(url: str) -> str:
        if "release-health" in url or "windows11-release-information" in url:
            return NOVEMBER
        return ""

    target = refresh_latest_win11(fetch_text=fetch, save=False, state={})
    assert target.version == "26H2"
    assert target.major == 26300
    assert target.ubr == 9974
    assert target.ubr == FALLBACK_WIN11_UBR
    assert target.source == "release-health"
    preview = refresh_latest_win11(
        fetch_text=fetch, save=False, state={}, allow_preview=True
    )
    assert preview.ubr == 9974
    host = refresh_latest_win11(host_build=28000, fetch_text=fetch, save=False, state={})
    assert host.version == "26H1"
    assert host.ubr == 3501
    assert host.source == "host-branch"
    offline = refresh_latest_win11(
        fetch_text=lambda _url: (_ for _ in ()).throw(OSError("offline")),
        save=False,
        state={},
    )
    assert offline.version == "26H2"
    assert offline.ubr == FALLBACK_WIN11_UBR
    assert offline.source == "builtin-floor"


def test_23h2_end_of_updates_plans_an_inplace_upgrade():
    assert "22631.7714" in note_for_eos_build(22631)
    assert note_for_eos_build(26300) == ""
    report = types.SimpleNamespace(
        build=22631,
        architecture="x64",
        sse42=True,
        is_win10=False,
        is_win11=True,
        os_family="win11",
        is_legacy=False,
        ubr=7711,
    )
    plan = evaluate_host(report, latest_win11=26300)
    assert "win11_latest" in plan.iso_steps_needed
    assert plan.needs_cumulative is False
    assert any("end of updates" in note for note in plan.notes)


def test_ca2023_replacement_file_is_part_of_media_check():
    state = _state(db_has_windows_uefi_ca_2023=True, pca2011_revoked_in_dbx=True)
    stock = _pe(CN_WINDOWS_PCA_2011.encode("ascii"))
    updated = _pe(CN_WINDOWS_UEFI_CA_2023.encode("ascii"))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        boot = root / "EFI" / "Microsoft" / "Boot"
        boot.mkdir(parents=True)
        (boot / "bootmgfw.efi").write_bytes(stock)
        (boot / "bootmgfw_EX.efi").write_bytes(updated)
        (boot / "boot.stl").write_bytes(b"stl")
        assert setup_media_block_reason(root, state) == ""
        no_stl = root / "EFI" / "Boot"
        no_stl.mkdir()
        (no_stl / "bootx64.efi").write_bytes(updated)
        (boot / "bootmgfw.efi").unlink()
        (boot / "bootmgfw_EX.efi").unlink()
        (boot / "boot.stl").unlink()
        assert find_media_loader(root).name.lower() == "bootx64.efi"
        assert setup_media_block_reason(root, state) == ""
        only_revoked = _state(db_has_windows_uefi_ca_2023=False, pca2011_revoked_in_dbx=True)
        (no_stl / "bootx64.efi").write_bytes(stock)
        reason = setup_media_block_reason(root, only_revoked)
        assert "PCA 2011" in reason or "2011" in reason


def test_dbx_url_fallback_does_not_fail_offline():
    assert OFFICIAL_DBX_URLS[0].endswith("SignedByKEK2023/dbx_x64.efiauth2")
    assert any(url.endswith("DBX/amd64/DBXUpdate.bin") for url in OFFICIAL_DBX_URLS)
    seen: list[str] = []

    def fetch(url: str) -> bytes:
        seen.append(url)
        if url == OFFICIAL_DBX_URLS[0]:
            raise OSError("offline")
        if url == OFFICIAL_DBX_URLS[1]:
            return b'{"message":"not found"}'
        if url == OFFICIAL_DBX_URLS[2]:
            return b"\x00" * 32
        raise OSError("unreachable")

    assert fetch_official_dbx(fetch) == b"\x00" * 32
    assert seen[:3] == list(OFFICIAL_DBX_URLS[:3])
    assert fetch_official_dbx(lambda _url: (_ for _ in ()).throw(OSError("offline"))) == b""


def test_decision_type_stays_explicit():
    decision = rewrite_decision(_state(secure_boot="off"), LoaderIdentity.missing())
    assert isinstance(decision, BootRewriteDecision)
    assert decision.allow is True


if __name__ == "__main__":
    test_october_2026_rows_prefer_oob_over_d_preview()
    test_stale_cache_ubr_does_not_hide_the_oob_floor()
    test_db_and_dbx_markers()
    test_pe_signer_and_rewrite_matrix()
    test_snapshot_restore_skips_revoked_loader_and_keeps_unparsed_one()
    test_boot_plan_holds_uefi_rewrite_and_leaves_legacy_paths()
    test_setup_media_refuses_a_2023_only_loader_when_db_lacks_it()
    test_november_2026_b_is_the_floor()
    test_23h2_end_of_updates_plans_an_inplace_upgrade()
    test_ca2023_replacement_file_is_part_of_media_check()
    test_dbx_url_fallback_does_not_fail_offline()
    test_decision_type_stays_explicit()
    print("OK test_v156_secureboot_and_oob")
