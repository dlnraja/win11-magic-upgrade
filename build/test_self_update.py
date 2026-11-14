"""Self-update decisions: newer, older, same, offline, bad hash, rollback."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.self_update import (  # noqa: E402
    apply_staged,
    bump_level,
    check_for_update,
    compare_semver,
    decide_update,
    version_compared_with_release,
    next_version,
    parse_sha256sums,
    pin_opencore,
    pin_win11_floor,
    rollback_installed,
    sha256_bytes,
    should_tag_release,
    signature_acceptable,
    stage_bytes,
)


def test_semver_newer_older_same() -> None:
    assert compare_semver("1.2.3", "1.2.4") < 0
    assert compare_semver("1.3.0", "1.2.9") > 0
    assert compare_semver("v1.56.0", "1.56.0") == 0
    assert decide_update(current="1.0.0", latest="1.1.0").status == "newer"
    assert decide_update(current="2.0.0", latest="1.9.0").status == "older"
    assert decide_update(current="1.56.0", latest="1.56.0").status == "same"
    offline = decide_update(current="1.56.0", latest=None, offline=True)
    assert offline.status == "offline"


def test_offline_check_does_not_raise() -> None:
    def opener(*_args, **_kwargs):
        raise OSError("offline")

    result = check_for_update(opener=opener, timeout=1)
    assert result["status"] == "offline"
    assert result["newer"] is False


def test_bad_hash_is_rejected() -> None:
    root = ROOT / "build" / "_update_bad_hash"
    if root.exists():
        import shutil

        shutil.rmtree(root)
    decision = stage_bytes(root, "Win11MagicUpgrade.exe", b"not-the-build", "0" * 64)
    assert decision.status == "bad_hash"
    assert not (root / "pending" / "Win11MagicUpgrade.exe").exists()


def test_stage_apply_and_rollback() -> None:
    import shutil

    root = ROOT / "build" / "_update_rollback"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    old = b"old-build"
    new = b"new-build-ok"
    (root / "Win11MagicUpgrade.exe").write_bytes(old)
    staged = stage_bytes(root, "Win11MagicUpgrade.exe", new, sha256_bytes(new))
    assert staged.status == "staged"
    assert apply_staged(root) is True
    assert (root / "Win11MagicUpgrade.exe").read_bytes() == new
    assert (root / "previous" / "Win11MagicUpgrade.exe").read_bytes() == old
    assert rollback_installed(root) is True
    assert (root / "Win11MagicUpgrade.exe").read_bytes() == old
    assert (root / "previous" / "Win11MagicUpgrade.exe").read_bytes() == new
    shutil.rmtree(root)


def test_sha256sums_and_signature() -> None:
    text = "abc\n" + ("ab" * 32) + " *Win11MagicUpgrade.exe\n"
    assert parse_sha256sums(text, "Win11MagicUpgrade.exe") == "ab" * 32
    assert signature_acceptable(None)[0] is True
    assert signature_acceptable("NotSigned")[0] is True
    assert signature_acceptable("Valid")[0] is True
    assert signature_acceptable("HashMismatch")[0] is False


def test_update_compares_the_stamped_tag() -> None:
    assert version_compared_with_release("1.56.1", "1.56.0") == "1.56.1"
    assert version_compared_with_release("v1.56.1", "9.9.9") == "1.56.1"
    assert version_compared_with_release("", "1.56.0") == "1.56.0"
    assert decide_update(current="1.56.1", latest="1.56.1").status == "same"
    assert decide_update(current="1.56.1", latest="1.56.2").status == "newer"


def test_release_tag_only_on_main_code() -> None:
    assert should_tag_release("refs/heads/main", "fix: boot map", ["python/engine/mbrgpt.py"]) is True
    assert should_tag_release("refs/pull/12/merge", "fix: boot map", ["python/engine/mbrgpt.py"]) is False
    assert should_tag_release("refs/tags/v1.56.0", "fix: boot map", ["python/engine/mbrgpt.py"]) is False
    assert should_tag_release("refs/heads/main", "chore: release v1.56.1", ["python/engine/mbrgpt.py"]) is False
    assert should_tag_release("refs/heads/main", "docs: readme", ["docs/BOOT_FIRMWARE.md"]) is False
    assert bump_level(["fix: letter"]) == "patch"
    assert bump_level(["feat: self update"]) == "minor"
    assert bump_level(["feat!: new pipeline"]) == "major"
    assert next_version("1.56.0", "patch") == "1.56.1"
    assert next_version("1.56.0", "minor") == "1.57.0"
    assert next_version("1.56.0", "major") == "2.0.0"


def test_boot_critical_defers_update() -> None:
    result = check_for_update(argv=["--boot-fix"])
    assert result["status"] == "deferred"


def test_pins_match_shipped_floor() -> None:
    version, sha = pin_opencore()
    assert version == "1.0.8"
    assert len(sha) == 64
    floor = pin_win11_floor()
    assert floor == ("26H2", 26300, 9725)


if __name__ == "__main__":
    test_semver_newer_older_same()
    test_offline_check_does_not_raise()
    test_bad_hash_is_rejected()
    test_stage_apply_and_rollback()
    test_sha256sums_and_signature()
    test_update_compares_the_stamped_tag()
    test_release_tag_only_on_main_code()
    test_boot_critical_defers_update()
    test_pins_match_shipped_floor()
    print("self-update OK")
