# November 2026 D previews are recognized. The default target stays the B build.
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="w11m-v157-")
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

from engine.chain import build_version_chain  # noqa: E402
from engine.version_planner import evaluate_host, note_for_eos_build  # noqa: E402
from engine.win11_inplace_policy import decide_noargs_startup, decide_oneclick  # noqa: E402
from engine.win11_release import (  # noqa: E402
    FALLBACK_WIN11_UBR,
    _pin_recognized_row,
    classify_update,
    floor_target,
    parse_end_of_updates,
    parse_excluded_versions,
    parse_servicing_rows,
    recognized_builds,
    refresh_latest_win11,
    select_latest,
    strip_html,
    view_host_build,
)

PAGE = (ROOT / "build" / "fixtures" / "win11_release_health_2026-11-23.html").read_text(
    encoding="utf-8"
)
FLOOR = dict(target_version="26H2", target_build=26300, target_ubr=9974)
_FORBIDDEN = ("outdated", "unknown build", "no-go", "nogo")


def _fetch_page(url: str) -> str:
    if "release-health" in url or "windows11-release-information" in url:
        return PAGE
    return ""


def _host(**kwargs):
    base = dict(
        build=26300,
        ubr=10075,
        architecture="x64",
        sse42=True,
        is_win10=False,
        is_win11=True,
        os_family="win11",
        is_legacy=False,
        is_arm64=False,
    )
    base.update(kwargs)
    return types.SimpleNamespace(**base)


def _assert_clean(text: str) -> None:
    low = (text or "").lower()
    for word in _FORBIDDEN:
        assert word not in low, word


def test_catalog_keeps_d_previews_off_the_floor():
    floor = floor_target()
    assert floor.version == "26H2"
    assert floor.major == 26300
    assert floor.ubr == 9974
    assert floor.ubr == FALLBACK_WIN11_UBR
    assert floor.source == "builtin-floor"
    by_role: dict[tuple[str, int, int], str] = {}
    for row in recognized_builds():
        by_role[(row.version, row.major, row.ubr)] = row.role
        if row.role == "recognized-only":
            assert row.update_class == "preview"
    assert by_role[("26H2", 26300, 9974)] == "default-target"
    assert by_role[("25H2", 26200, 9974)] == "default-target"
    assert by_role[("24H2", 26100, 9974)] == "default-target"
    assert by_role[("26H1", 28000, 3501)] == "default-target"
    assert by_role[("23H2", 22631, 7714)] == "default-target"
    assert by_role[("26H2", 26300, 10075)] == "recognized-only"
    assert by_role[("25H2", 26200, 10075)] == "recognized-only"
    assert by_role[("26H1", 28000, 3620)] == "recognized-only"
    d26 = next(row for row in recognized_builds() if row.major == 26300 and row.ubr == 10075)
    d25 = next(row for row in recognized_builds() if row.major == 26200 and row.ubr == 10075)
    d261 = next(row for row in recognized_builds() if row.major == 28000 and row.ubr == 3620)
    assert d26.kb == "KB5132057" and d26.released == "2026-11-19"
    assert d25.kb == "KB5132057"
    assert d261.kb == "KB5132265" and d261.released == "2026-11-19"
    assert _pin_recognized_row(
        {"version": "26H2", "build": 26300, "ubr": 10075, "role": "default-target", "kb": "KB1"}
    ) is None
    pinned = _pin_recognized_row(
        {
            "version": "26H2",
            "build": 26300,
            "ubr": 10075,
            "role": "recognized-only",
            "class": "preview",
            "kb": "KB5132057",
            "released": "2026-11-19",
        }
    )
    assert pinned is not None and pinned.role == "recognized-only" and pinned.ubr == 10075


def test_november_23_page_defaults_to_b_and_opts_into_d():
    assert classify_update("General Availability Channel", "2026-11 D") == "preview"
    rows = parse_servicing_rows(PAGE)
    by_key = {(row.version, row.update_class): (row.major, row.ubr) for row in rows}
    assert by_key[("26H2", "preview")] == (26300, 10075)
    assert by_key[("26H2", "security")] == (26300, 9974)
    assert by_key[("25H2", "preview")] == (26200, 10075)
    assert by_key[("25H2", "security")] == (26200, 9974)
    assert by_key[("26H1", "preview")] == (28000, 3620)
    assert by_key[("26H1", "security")] == (28000, 3501)
    assert by_key[("24H2", "security")] == (26100, 9974)
    assert ("24H2", "preview") not in by_key
    assert by_key[("23H2", "oob")] == (22631, 7714)
    assert by_key[("23H2", "security")] == (22631, 7711)
    assert parse_end_of_updates(PAGE) == {"23H2"}
    excluded = parse_excluded_versions(strip_html(PAGE))
    assert "26H1" in excluded
    chosen = select_latest(rows, excluded)
    assert chosen is not None and chosen.ubr == 9974 and chosen.version == "26H2"
    opted = select_latest(rows, excluded, allow_preview=True)
    assert opted is not None and opted.ubr == 10075
    host_b = select_latest(rows, excluded, host_build=28000)
    assert host_b is not None and host_b.ubr == 3501 and host_b.version == "26H1"
    host_d = select_latest(rows, excluded, host_build=28000, allow_preview=True)
    assert host_d is not None and host_d.ubr == 3620 and host_d.version == "26H1"

    target = refresh_latest_win11(fetch_text=_fetch_page, save=False, state={})
    assert target.version == "26H2"
    assert target.major == 26300
    assert target.ubr == 9974
    assert target.source == "release-health"
    preview = refresh_latest_win11(
        fetch_text=_fetch_page, save=False, state={}, allow_preview=True
    )
    assert preview.version == "26H2"
    assert preview.ubr == 10075
    assert preview.source == "release-health"
    branch = refresh_latest_win11(
        host_build=28000, fetch_text=_fetch_page, save=False, state={}
    )
    assert branch.version == "26H1" and branch.ubr == 3501 and branch.source == "host-branch"
    branch_d = refresh_latest_win11(
        host_build=28000, fetch_text=_fetch_page, save=False, state={}, allow_preview=True
    )
    assert branch_d.version == "26H1" and branch_d.ubr == 3620 and branch_d.source == "host-branch"
    offline = refresh_latest_win11(
        fetch_text=lambda _url: (_ for _ in ()).throw(OSError("offline")),
        save=False,
        state={},
    )
    assert offline.ubr == 9974 and offline.source == "builtin-floor"
    offline_d = refresh_latest_win11(
        fetch_text=lambda _url: (_ for _ in ()).throw(OSError("offline")),
        save=False,
        state={},
        allow_preview=True,
    )
    assert offline_d.version == "26H2"
    assert offline_d.ubr == 10075
    assert offline_d.source == "recognized-preview"
    os.environ["MAGIC_WIN11_PREVIEW"] = "1"
    try:
        from_env = refresh_latest_win11(
            fetch_text=lambda _url: (_ for _ in ()).throw(OSError("offline")),
            save=False,
            state={},
        )
    finally:
        os.environ.pop("MAGIC_WIN11_PREVIEW", None)
    assert from_env.ubr == 10075 and from_env.source == "recognized-preview"


def test_pc_already_on_a_d_preview_is_not_a_nogo():
    view = view_host_build(26300, 10075, target_major=26300, target_ubr=9974)
    assert view.status == "recognized-preview"
    assert view.role == "recognized-only"
    assert view.kb == "KB5132057"
    assert view.warning == ""
    _assert_clean(view.note)
    assert "recognized optional preview" in view.note
    assert "26300.9974" in view.note

    current = decide_oneclick(build=26300, ubr=10075, display_version="26H2", **FLOOR)
    assert current.path == "health_check_only"
    assert current.allow_bypass is False
    _assert_clean(current.reason)
    assert "recognized optional preview" in current.reason
    startup = decide_noargs_startup(
        is_win11=True, guard_go=True, issues=[], update_path=current.path
    )
    assert startup.action == "health"
    _assert_clean(startup.reason)

    device = decide_oneclick(build=28000, ubr=3620, display_version="26H1", **FLOOR)
    assert device.path == "health_check_only"
    _assert_clean(device.reason)
    assert "recognized optional preview" in device.reason
    assert "device-only" in device.reason
    device_go = decide_noargs_startup(
        is_win11=True, guard_go=True, issues=[], update_path=device.path
    )
    assert device_go.action == "health"

    ahead_of_branch = decide_oneclick(
        build=28000,
        ubr=3620,
        display_version="26H1",
        target_version="26H1",
        target_build=28000,
        target_ubr=3501,
    )
    assert ahead_of_branch.path == "health_check_only"
    _assert_clean(ahead_of_branch.reason)
    opted_behind = decide_oneclick(
        build=28000,
        ubr=3501,
        display_version="26H1",
        target_version="26H1",
        target_build=28000,
        target_ubr=3620,
    )
    assert opted_behind.path == "cumulative_update"
    opted_go = decide_noargs_startup(
        is_win11=True, guard_go=True, issues=[], update_path=opted_behind.path
    )
    assert opted_go.action == "official_update"

    feature = decide_oneclick(build=26200, ubr=10075, display_version="25H2", **FLOOR)
    assert feature.path == "inplace_least"
    _assert_clean(feature.reason)
    assert "KB5132057" in feature.reason
    feature_go = decide_noargs_startup(
        is_win11=True, guard_go=True, issues=[], update_path=feature.path
    )
    assert feature_go.action == "official_update"

    plan = evaluate_host(_host(), latest_win11=26300)
    assert plan.needs_win11_upgrade is False
    assert plan.needs_cumulative is False
    assert plan.iso_steps_needed == []
    blob = " ".join(plan.notes)
    _assert_clean(blob)
    assert "KB5132057" in blob

    older = evaluate_host(_host(build=26200, ubr=10075), latest_win11=26300)
    assert older.needs_win11_upgrade is True
    _assert_clean(" ".join(older.notes))
    assert any("recognized optional preview" in note for note in older.notes)

    chain = build_version_chain(_host())
    assert chain[0].kind == "done"
    _assert_clean(chain[0].note)
    assert "recognized optional preview" in chain[0].note
    device_chain = build_version_chain(_host(build=28000, ubr=3620))
    assert device_chain[0].kind == "done"
    _assert_clean(device_chain[0].note)
    assert "28000.3620" in device_chain[0].note

    unchanged = evaluate_host(_host(build=26100, ubr=9974), latest_win11=26300)
    assert unchanged.needs_win11_upgrade is True
    assert all("recognized optional preview" not in note for note in unchanged.notes)
    assert "22631.7714" in note_for_eos_build(22631)
    eos = decide_oneclick(build=22631, ubr=7714, display_version="23H2", **FLOOR)
    assert eos.path == "inplace_least"
    eos_go = decide_noargs_startup(is_win11=True, guard_go=True, issues=[], update_path=eos.path)
    assert eos_go.action == "official_update"


if __name__ == "__main__":
    test_catalog_keeps_d_previews_off_the_floor()
    test_november_23_page_defaults_to_b_and_opts_into_d()
    test_pc_already_on_a_d_preview_is_not_a_nogo()
    print("OK test_v157_november_d_preview")
