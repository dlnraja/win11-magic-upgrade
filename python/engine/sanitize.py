"""
PII / personal-data scrubbing for support diagnostics.

Never ship: usernames, computer names, emails, SIDs, MACs, IPs, full user paths,
serial numbers, product keys, or Absolute local paths under Users\\.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[A-F0-9]{1,4}:){2,7}[A-F0-9]{1,4}\b", re.I)
_MAC = re.compile(r"\b(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}\b", re.I)
_SID = re.compile(r"\bS-1-5-21(?:-\d+){3,}\b")
_GUID = re.compile(
    r"\b[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\b",
    re.I,
)
_PRODUCT_KEY = re.compile(r"\b(?:[A-Z0-9]{5}-){4}[A-Z0-9]{5}\b")
_USERS_PATH = re.compile(r"(?i)([A-Z]:\\Users\\)([^\\\/\s\"']+)")
_HOME_UNIX = re.compile(r"(?i)(/home/)([^/\s\"']+)")
_UNC_USER = re.compile(r"(?i)(\\\\[^\\\s]+\\Users\\)([^\\\/\s\"']+)")


def _tokens_to_redact() -> list[str]:
    keys = (
        "USERNAME",
        "USERPROFILE",
        "COMPUTERNAME",
        "USERDOMAIN",
        "LOGONSERVER",
        "HOMEPATH",
        "HOME",
    )
    out: list[str] = []
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if not v or len(v) < 2:
            continue
        out.append(v)
        # Also basename of profile
        try:
            if "\\" in v or "/" in v:
                out.append(Path(v).name)
        except Exception:
            pass
    # Deduplicate longest-first so we replace full paths before short names
    uniq = sorted(set(out), key=len, reverse=True)
    return [u for u in uniq if u.lower() not in {"users", "windows", "system32", "c:", "c"}]


def sanitize_text(text: str) -> str:
    if not text:
        return text
    s = text
    for tok in _tokens_to_redact():
        if not tok:
            continue
        s = re.sub(re.escape(tok), "<REDACTED>", s, flags=re.I)

    s = _USERS_PATH.sub(r"\1<USER>", s)
    s = _UNC_USER.sub(r"\1<USER>", s)
    s = _HOME_UNIX.sub(r"\1<USER>", s)
    s = _EMAIL.sub("<EMAIL>", s)
    s = _SID.sub("<SID>", s)
    s = _MAC.sub("<MAC>", s)
    s = _IPV4.sub("<IP>", s)
    s = _IPV6.sub("<IP6>", s)
    s = _PRODUCT_KEY.sub("<PRODUCTKEY>", s)
    # Keep structure of GUIDs but scrub (may identify machine in some OEM logs)
    s = _GUID.sub("<GUID>", s)

    # LocalAppData / absolute state dir
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        s = re.sub(re.escape(local), r"%LOCALAPPDATA%", s, flags=re.I)
    return s


def sanitize_path_str(path: str | Path) -> str:
    return sanitize_text(str(path))


def sanitize_obj(obj: Any, *, depth: int = 0) -> Any:
    """Recursively sanitize dict/list/str for JSON diagnostics."""
    if depth > 8:
        return "<TRUNCATED>"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, Path):
        return sanitize_path_str(obj)
    if isinstance(obj, str):
        return sanitize_text(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k)
            # Drop obviously sensitive keys entirely
            if re.search(r"user|password|token|secret|email|serial|license|key|sid|mac", key, re.I):
                if key.lower() in {"cpu_name", "disk_number", "partition_style", "build", "ubr"}:
                    out[key] = sanitize_obj(v, depth=depth + 1)
                elif key.lower() in {"username", "computername", "password", "token", "secret", "email"}:
                    out[key] = "<REDACTED>"
                else:
                    out[key] = sanitize_obj(v, depth=depth + 1)
            else:
                out[key] = sanitize_obj(v, depth=depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize_obj(x, depth=depth + 1) for x in obj[:200]]
    return sanitize_text(str(obj))


def safe_report_fields(report: dict[str, Any] | None) -> dict[str, Any]:
    """Allow-list system facts useful for debugging (no identity)."""
    if not report:
        return {}
    allow = {
        "product_name",
        "edition_id",
        "display_version",
        "build",
        "ubr",
        "is_win11",
        "is_win10",
        "architecture",
        "needs_intermediate",
        "mbr2gpt_available",
        "ram_gb",
        "free_gb",
        "locale",
        "disk_number",
        "partition_style",
        "is_uefi",
        "secure_boot",
        "cpu_name",
        "sse42",
        "tpm_present",
        "cpu_64bit",
        "bootmgr_arch",
        "firmware_likely_ia32",
        "bootmgr_mismatch",
        "boot_strategy",
    }
    out = {k: report.get(k) for k in allow if k in report}
    # Soften CPU name brand strings only (keep model family)
    if "cpu_name" in out and isinstance(out["cpu_name"], str):
        out["cpu_name"] = sanitize_text(out["cpu_name"])
    return sanitize_obj(out)
