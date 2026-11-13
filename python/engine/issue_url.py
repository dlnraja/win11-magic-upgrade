"""Short GitHub issue links. A long issues/new URL is rejected by GitHub."""
from __future__ import annotations

import os
import re
import subprocess
from urllib.parse import urlencode

MAX_ISSUE_URL = 6000


def short_issue_body(
    *,
    fingerprint: str,
    version: str,
    error: str,
    context: str = "",
    report_path: str = "",
) -> str:
    lines = [
        f"Fingerprint: {fingerprint}",
        f"Version: {version}",
        f"Error: {(error or '').strip()[:500]}",
        f"Context: {(context or '').strip()[:400]}",
        f"Attach the saved redacted report: {report_path}",
    ]
    return "\n".join(lines)


def prefilled_issue_url(repo: str, title: str, body: str, *, labels: str = "", limit: int = MAX_ISSUE_URL) -> str:
    """Build an issues/new URL no longer than ``limit`` characters."""
    title = (title or "diagnostic").strip()[:180]
    body = body or ""
    base = f"https://github.com/{repo}/issues/new?"
    while True:
        query = urlencode({"title": title, "body": body, "labels": labels})
        url = base + query
        if len(url) <= limit:
            return url
        if len(body) <= 80 and len(title) <= 40:
            return url[:limit]
        if len(body) > 80:
            body = body[: max(80, int(len(body) * 0.6))]
        else:
            title = title[: max(40, int(len(title) * 0.6))]


def try_gh_issue(title: str, body: str, *, label: str = "auto-diag") -> str | None:
    """Create an issue with ``gh`` when that command is already logged in.

    No token is placed on the command line. ``MAGIC_SKIP_GH_ISSUE=1`` skips it.
    """
    if os.environ.get("MAGIC_SKIP_GH_ISSUE", "").strip().lower() in ("1", "true", "yes"):
        return None
    try:
        auth = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if auth.returncode != 0:
            return None
        created = subprocess.run(
            ["gh", "issue", "create", "--title", title[:180], "--body", body, "--label", label],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if created.returncode != 0:
        return None
    match = re.search(r"https://github.com/\S+/issues/\d+", (created.stdout or "") + (created.stderr or ""))
    return match.group(0) if match else None
