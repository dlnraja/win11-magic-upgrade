"""Tag selection and build-time version stamp. No branch is updated."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

from release_plan import (  # noqa: E402
    highest_release,
    parse_ls_remote_tags,
    version_from_github_ref,
)
from stamp_release_version import stamp_tree  # noqa: E402


def test_next_tag_comes_from_the_latest_tag() -> None:
    listed = "\n".join(
        [
            "abc\trefs/tags/v1.56.0",
            "def\trefs/tags/v1.55.0",
            "be0\trefs/tags/v1.56.1",
            "be0\trefs/tags/v1.56.1^{}",
            "zzz\trefs/tags/not-a-release",
        ]
    )
    names = parse_ls_remote_tags(listed)
    assert highest_release(names) == "1.56.1"
    assert "v1.56.1" in names
    assert version_from_github_ref("refs/tags/v1.56.2", "v1.56.2") == "1.56.2"
    assert version_from_github_ref("refs/heads/main", "main") is None


def test_stamp_writes_tag_version_without_a_commit(tmp: Path | None = None) -> None:
    import tempfile

    base = Path(tempfile.mkdtemp()) if tmp is None else tmp
    (base / "build").mkdir(parents=True)
    (base / "python" / "engine").mkdir(parents=True)
    (base / "VERSION").write_text("1.56.0\n", encoding="utf-8")
    (base / "build" / "version_info.txt").write_text(
        "filevers=(1, 56, 0, 0)\nprodvers=(1, 56, 0, 0)\n"
        "StringStruct('FileVersion', '1.56.0.0')\n"
        "StringStruct('ProductVersion', '1.56.0.0')\n",
        encoding="utf-8",
    )
    report = base / "python" / "engine" / "gh_report.py"
    report.write_text('APP_VERSION = "1.56.0"\nAPP_VERSION = "keep-second"\n', encoding="utf-8")
    stamp_tree(base, "1.56.2")
    assert (base / "VERSION").read_text(encoding="utf-8") == "1.56.2\n"
    info = (base / "build" / "version_info.txt").read_text(encoding="utf-8")
    assert "filevers=(1, 56, 2, 0)" in info
    assert "FileVersion', '1.56.2.0'" in info
    body = report.read_text(encoding="utf-8")
    assert 'APP_VERSION = "1.56.2"' in body
    assert 'APP_VERSION = "keep-second"' in body
    source = (ROOT / "build" / "ci_release_bump.py").read_text(encoding="utf-8")
    assert "HEAD:main" not in source
    assert "refs/tags/" in source


def main() -> None:
    test_next_tag_comes_from_the_latest_tag()
    test_stamp_writes_tag_version_without_a_commit()
    print("test_release_plan ok")


if __name__ == "__main__":
    main()
