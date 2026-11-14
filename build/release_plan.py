"""Choose the next release tag without writing to a branch.

The version is the highest existing ``vX.Y.Z`` tag plus one bump. The tag
points at the merge commit that is already on main.
"""
from __future__ import annotations

import re

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_REF = re.compile(r"^refs/tags/v(\d+)\.(\d+)\.(\d+)$")


def parse_semver(value: str) -> tuple[int, int, int]:
    text = (value or "").strip().lstrip("vV")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def parse_ls_remote_tags(text: str) -> list[str]:
    names: list[str] = []
    for line in (text or "").splitlines():
        if "\t" not in line:
            continue
        ref = line.split("\t", 1)[1].strip()
        if ref.endswith("^{}"):
            ref = ref[:-3]
        if ref.startswith("refs/tags/"):
            names.append(ref[len("refs/tags/") :])
    return names


def highest_release(tag_names: list[str]) -> str | None:
    best: tuple[int, int, int] | None = None
    for name in tag_names:
        if not _TAG.fullmatch(name or ""):
            continue
        parsed = parse_semver(name)
        if best is None or parsed > best:
            best = parsed
    if best is None:
        return None
    return f"{best[0]}.{best[1]}.{best[2]}"


def version_from_github_ref(ref: str, ref_name: str = "") -> str | None:
    """Return X.Y.Z when this workflow run is the tag refs/tags/vX.Y.Z."""
    for text in (ref_name or "", ref or ""):
        candidate = text.strip()
        if _TAG.fullmatch(candidate):
            major, minor, patch = parse_semver(candidate)
            return f"{major}.{minor}.{patch}"
        match = _REF.fullmatch(candidate)
        if match:
            return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    return None
