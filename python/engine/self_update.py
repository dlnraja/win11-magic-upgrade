"""Check GitHub Releases for a newer portable build (opt-in download)."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from .gh_report import APP_VERSION, DEFAULT_REPO
from .logutil import log, save_state


def _parse_ver(s: str) -> tuple[int, ...]:
    s = (s or "").lstrip("vV")
    parts = []
    for p in s.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts[:4] or (0,))


def check_for_update() -> dict[str, Any]:
    """
    Query latest GitHub release. No auto-install unless MAGIC_SELF_UPDATE=1
    (then only reports URL — user/CI downloads).
    """
    result: dict[str, Any] = {
        "current": APP_VERSION,
        "latest": "",
        "newer": False,
        "url": "",
        "notes": "",
    }
    if os.environ.get("MAGIC_SKIP_UPDATE_CHECK", "").strip().lower() in ("1", "true", "yes"):
        return result
    api = f"https://api.github.com/repos/{DEFAULT_REPO}/releases/latest"
    try:
        req = urllib.request.Request(
            api,
            headers={"User-Agent": f"Win11MagicUpgrade/{APP_VERSION}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        log(f"Update check skipped: {e}", "INFO")
        return result
    tag = str(data.get("tag_name") or "")
    result["latest"] = tag.lstrip("v")
    result["url"] = str(data.get("html_url") or "")
    result["notes"] = str(data.get("name") or tag)[:200]
    try:
        result["newer"] = _parse_ver(result["latest"]) > _parse_ver(APP_VERSION)
    except Exception:
        result["newer"] = False
    if result["newer"]:
        log(
            f"Newer release available: {result['latest']} (you have {APP_VERSION}) → {result['url']}",
            "WARN",
        )
    else:
        log(f"Up to date ({APP_VERSION})", "OK")
    save_state({"UpdateCheck": result})
    return result
