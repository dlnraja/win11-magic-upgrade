"""Create the next semver tag after a code merge to main.

This job never pushes a branch. A shallow checkout (fetch-depth) cannot push
a new commit: GitHub rejects it with "shallow update not allowed". The rules
on main only forbid deletion and non-fast-forward history; they do not require
a pull request or a status check. The release tag points at the merge commit
that is already on main, and release.yml stamps VERSION from that tag.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))
sys.path.insert(0, str(ROOT / "python"))
# engine/__init__.py imports winreg. This job runs on Ubuntu.
if "engine" not in sys.modules:
    pkg = types.ModuleType("engine")
    pkg.__path__ = [str(ROOT / "python" / "engine")]  # type: ignore[attr-defined]
    pkg.__package__ = "engine"
    sys.modules["engine"] = pkg

from engine.self_update import bump_level, next_version, should_tag_release  # noqa: E402
from release_plan import highest_release, parse_ls_remote_tags  # noqa: E402


def git(*args: str, check: bool = True) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        if check:
            result.check_returncode()
    return result.returncode, (result.stdout or "").strip()


def dispatch_release(repo: str, token: str, tag: str) -> None:
    request = Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/release.yml/dispatches",
        data=json.dumps({"ref": tag}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "win11-magic-upgrade-release-bump",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        print(f"Dispatched release.yml on {tag}: HTTP {response.status}")


def main() -> int:
    ref = os.environ.get("GITHUB_REF") or ""
    _code, message = git("log", "-1", "--format=%B")
    _code, files_text = git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    files = [line for line in files_text.splitlines() if line]
    if not should_tag_release(ref, message, files):
        print(f"No release tag for ref={ref!r}")
        return 0
    token = os.environ.get("GH_TOKEN") or ""
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    if not token or not repo:
        print("GH_TOKEN or GITHUB_REPOSITORY missing")
        return 1
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    code, listed = git("ls-remote", "--tags", remote, check=False)
    if code != 0:
        print("Could not list release tags")
        return 1
    tags = parse_ls_remote_tags(listed)
    base = highest_release(tags) or "0.0.0"
    _code, subjects = git("log", "--format=%s", "-n", "30")
    version = next_version(base, bump_level(subjects.splitlines()))
    tag = f"v{version}"
    if tag in tags:
        print(f"{tag} already exists. It will not be moved.")
        return 0
    print(f"Tagging {tag} on {git('rev-parse', 'HEAD')[1]} (no branch push)")
    git("tag", tag, "HEAD")
    git("push", remote, f"refs/tags/{tag}")
    print(f"Pushed {tag}")
    # A tag pushed with GITHUB_TOKEN does not start another workflow.
    dispatch_release(repo, token, tag)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired:
        print("git command timed out")
        raise SystemExit(1)
