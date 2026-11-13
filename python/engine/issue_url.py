"""Short GitHub issue links. A long issues/new URL is rejected by GitHub."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MAX_ISSUE_URL = 6000
MAX_ISSUE_BODY = 60_000


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
        f"Attach the saved redacted report. Full diagnostic zip: {report_path}",
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


def cap_report(text: str, limit: int = MAX_ISSUE_BODY) -> str:
    body = text or ""
    if len(body) <= limit:
        return body
    note = "\n\n[truncated]\n"
    return body[: max(0, limit - len(note))] + note


def github_token() -> str:
    for key in ("MAGIC_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _gh_skip() -> bool:
    return os.environ.get("MAGIC_SKIP_GH_ISSUE", "").strip().lower() in ("1", "true", "yes")


def _default_http(url: str, payload: dict, token: str, timeout: int = 20) -> tuple[int, str]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Win11MagicUpgrade",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return int(response.status), response.read().decode("utf-8", errors="replace")


def _default_gh(args: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def bundle_text_files(report: str, bundle: str | Path | None = None) -> dict[str, str]:
    """Text files for a secret gist. Zip members are decoded as text."""
    files = {"report.md": cap_report(report, 400_000)}
    path = Path(bundle) if bundle else None
    if path is None or not path.is_file() or path.suffix.lower() != ".zip":
        return files
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.file_size > 300_000 or len(files) >= 8:
                    continue
                name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(info.filename).name)[:80] or "log.txt"
                files[name] = cap_report(archive.read(info).decode("utf-8", errors="replace"), 200_000)
    except (OSError, zipfile.BadZipFile):
        pass
    return files


def file_report(
    *,
    title: str,
    body: str,
    files: dict[str, str] | None = None,
    label: str = "auto-diag",
    token: str | None = None,
    http_post=None,
    gh_run=None,
) -> dict[str, str] | None:
    """Create an issue when gh is logged in or a token is set. Otherwise return None."""
    if _gh_skip():
        return None
    secret = github_token() if token is None else token
    run = gh_run or _default_gh
    post = http_post or _default_http
    authed = bool(secret) or run(["auth", "status"], 15)[0] == 0
    if not authed:
        return None
    gist_url = ""
    payload_files = files or {"report.md": cap_report(body, 400_000)}
    if secret:
        try:
            status, raw = post(
                "https://api.github.com/gists",
                {
                    "public": False,
                    "description": "Redacted Win11 Magic Upgrade report",
                    "files": {name: {"content": text or " "} for name, text in payload_files.items()},
                },
                secret,
                20,
            )
            if status in (200, 201):
                gist_url = str(json.loads(raw).get("html_url") or "")
        except Exception:
            gist_url = ""
    else:
        import tempfile

        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths: list[str] = []
                for name, text in payload_files.items():
                    dest = Path(tmp) / name
                    dest.write_text(text or " ", encoding="utf-8")
                    paths.append(str(dest))
                code, out = run(["gist", "create", "--secret", *paths], 25)
            if code == 0:
                match = re.search(r"https://gist\.github\.com/\S+", out)
                gist_url = match.group(0) if match else ""
        except Exception:
            gist_url = ""
    issue_body = cap_report(body)
    if gist_url:
        link = f"\n\nRedacted bundle: {gist_url}\n"
        issue_body = cap_report(issue_body[: MAX_ISSUE_BODY - len(link)] + link)
    issue_url = ""
    if secret:
        try:
            status, raw = post(
                "https://api.github.com/repos/dlnraja/win11-magic-upgrade/issues",
                {"title": title[:180], "body": issue_body, "labels": [label]},
                secret,
                20,
            )
            if status in (200, 201):
                issue_url = str(json.loads(raw).get("html_url") or "")
        except Exception:
            issue_url = ""
    if not issue_url:
        code, out = run(
            ["issue", "create", "--title", title[:180], "--body", issue_body, "--label", label],
            25,
        )
        if code == 0:
            match = re.search(r"https://github.com/\S+/issues/\d+", out)
            issue_url = match.group(0) if match else ""
    if not issue_url:
        return None
    return {"url": issue_url, "gist": gist_url}


def run_in_background(fn, *, name: str = "auto-diag") -> None:
    """Daemon thread. Failures are logged and never raised to the caller."""

    def work() -> None:
        try:
            fn()
        except Exception as exc:
            try:
                from .logutil import log

                log(f"{name} failed: {exc}", "WARN")
            except Exception:
                pass

    threading.Thread(target=work, name=name, daemon=True).start()


def try_gh_issue(title: str, body: str, *, label: str = "auto-diag", files: dict[str, str] | None = None) -> str | None:
    """File an issue when gh is logged in or MAGIC_GITHUB_TOKEN is set."""
    filed = file_report(title=title, body=cap_report(body), files=files, label=label)
    return (filed or {}).get("url") or None
