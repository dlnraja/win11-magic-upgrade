"""Create the next semver tag after a code merge to main.

Invoked only by .github/workflows/release-bump.yml. A pull request ref,
a tag ref, or a release commit does not create another tag.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.self_update import bump_level, current_version, next_version, should_tag_release  # noqa: E402


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return (result.stdout or "").strip()


def write_version(version: str) -> None:
    (ROOT / "VERSION").write_text(version + "\n", encoding="utf-8")
    info = ROOT / "build" / "version_info.txt"
    if info.is_file():
        text = info.read_text(encoding="utf-8")
        major, minor, patch = version.split(".")
        tuple_text = f"({major}, {minor}, {patch}, 0)"
        dotted = version + ".0"
        import re

        text = re.sub(r"filevers=\([^)]+\)", f"filevers={tuple_text}", text)
        text = re.sub(r"prodvers=\([^)]+\)", f"prodvers={tuple_text}", text)
        text = re.sub(r"FileVersion', '[^']+'", f"FileVersion', '{dotted}'", text)
        text = re.sub(r"ProductVersion', '[^']+'", f"ProductVersion', '{dotted}'", text)
        info.write_text(text, encoding="utf-8")
    report = ROOT / "python" / "engine" / "gh_report.py"
    if report.is_file():
        import re

        body = report.read_text(encoding="utf-8")
        body = re.sub(r'APP_VERSION = "[^"]+"', f'APP_VERSION = "{version}"', body, count=1)
        report.write_text(body, encoding="utf-8")


def main() -> int:
    ref = os.environ.get("GITHUB_REF") or ""
    message = git("log", "-1", "--format=%B")
    files = [line for line in git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines() if line]
    if not should_tag_release(ref, message, files):
        print(f"No release tag for ref={ref!r}")
        return 0
    messages = git("log", "--format=%s", "-n", "30").splitlines()
    version = next_version(current_version(), bump_level(messages))
    print(f"Tagging v{version}")
    write_version(version)
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "VERSION", "build/version_info.txt", "python/engine/gh_report.py")
    git("commit", "-m", f"chore: release v{version}")
    git("tag", f"v{version}")
    token = os.environ.get("GH_TOKEN") or ""
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    if not token or not repo:
        print("GH_TOKEN or GITHUB_REPOSITORY missing; tag was created locally only")
        return 1
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    git("push", remote, "HEAD:main")
    git("push", remote, f"v{version}")
    print(f"Pushed v{version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired:
        print("git command timed out")
        raise SystemExit(1)
