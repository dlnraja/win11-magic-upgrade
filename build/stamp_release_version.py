"""Write VERSION, the exe version resource, and APP_VERSION from the git tag.

Used by release.yml on the build runner only. It does not commit or push.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

from release_plan import version_from_github_ref  # noqa: E402


def stamp_tree(root: Path, version: str) -> None:
    major, minor, patch = version.split(".")
    tuple_text = f"({major}, {minor}, {patch}, 0)"
    dotted = version + ".0"
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    info = root / "build" / "version_info.txt"
    if info.is_file():
        text = info.read_text(encoding="utf-8")
        text = re.sub(r"filevers=\([^)]+\)", f"filevers={tuple_text}", text)
        text = re.sub(r"prodvers=\([^)]+\)", f"prodvers={tuple_text}", text)
        text = re.sub(r"FileVersion', '[^']+'", f"FileVersion', '{dotted}'", text)
        text = re.sub(r"ProductVersion', '[^']+'", f"ProductVersion', '{dotted}'", text)
        info.write_text(text, encoding="utf-8")
    report = root / "python" / "engine" / "gh_report.py"
    if report.is_file():
        body = report.read_text(encoding="utf-8")
        body = re.sub(r'APP_VERSION = "[^"]+"', f'APP_VERSION = "{version}"', body, count=1)
        report.write_text(body, encoding="utf-8")


def main() -> int:
    version = version_from_github_ref(
        os.environ.get("GITHUB_REF") or "",
        os.environ.get("GITHUB_REF_NAME") or "",
    )
    if not version:
        print("Not a vX.Y.Z tag. Leaving VERSION, version_info, and APP_VERSION unchanged.")
        return 0
    stamp_tree(ROOT, version)
    print(f"Stamped {version} from the release tag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
