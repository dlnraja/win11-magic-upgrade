"""End-of-run scan. Redacted findings become one GitHub auto-diag issue per signature.

No token is stored in the program. Without ``gh`` or ``MAGIC_GITHUB_TOKEN`` /
``GITHUB_TOKEN``, a ready-to-paste file and a prefilled issue URL are saved.
Set ``MAGIC_SKIP_AUTO_DIAG=1`` to do nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from .logutil import LOG_DIR, STATE_DIR, log

_PROCESS_START = time.time()
_RAN = False
_VOL = re.compile(
    r"(?i)(?:\\+\?\\+)?volume\{([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\}\\*"
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[A-F0-9]{1,4}:){3,7}[A-F0-9]{1,4}\b", re.I)
_MAC = re.compile(r"\b(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}\b", re.I)
_PRODUCT_KEY = re.compile(r"\b(?:[A-Z0-9]{5}-){4}[A-Z0-9]{5}\b")
_SERIAL = re.compile(r"(?i)(serial(?:\s*number)?\s*[:=]\s*)([A-Za-z0-9]{4,})")
_USER_PATH = re.compile(r"(?i)[A-Z]:\\Users\\[^\\/\s\"']+")
_UNIX_HOME = re.compile(r"(?i)/home/[^/\s\"']+")
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("level", re.compile(r"\[(WARN|ERROR)\]|, (?:Warning|Error)\s")),
    ("diskpart-args", re.compile(r"(?i)arguments?\s+(?:non valides|not valid|are not valid)|les arguments sp[eé]cifi[eé]s")),
    ("usage", re.compile(r"(?i)(bcdboot|bcdedit).{0,80}(usage|syntax|parameter is incorrect|param[eè]tre incorrect)")),
    ("winerror", re.compile(r"WinError\s+\d+")),
    ("introuvable", re.compile(r"(?i)introuvable")),
    ("not-found", re.compile(r"(?i)not found|chemin d'acc[eè]s sp[eé]cifi[eé] est introuvable")),
    ("invalid", re.compile(r"(?i)\binvalid\b|ERROR_INVALID_NAME")),
    ("path", re.compile(r"(?i)doubled backslash|mount failed|cannot find the path|ERROR_PATH_NOT_FOUND")),
    ("exit", re.compile(r"(?i)(?:exit(?:\s+code)?|failed \(exit)\s*[:=]?\s*([1-9]\d{0,4})\b")),
]
_BUILTIN_CATALOG = [
    (re.compile(r"\b123\b|ERROR_INVALID_NAME|WinError\s+3"), "bcdboot-123"),
    (re.compile(r"\b112\b|ERROR_DISK_FULL"), "bcdboot-112"),
    (re.compile(r"(?i)arguments?\s+(?:non valides|not valid)|les arguments"), "diskpart-args"),
    (re.compile(r"(?i)introuvable|not found|ERROR_PATH_NOT_FOUND"), "path-not-found"),
]
_MAX_NEW_PER_DAY = 3


@dataclass
class Finding:
    kind: str
    raw: str
    redacted: str
    signature: str
    catalog_id: str
    command: str


def _names() -> list[str]:
    found: list[str] = []
    for key in ("USERNAME", "COMPUTERNAME", "USERDOMAIN"):
        value = (os.environ.get(key) or "").strip()
        if len(value) >= 3 and value.lower() not in {"system", "desktop", "users"}:
            found.append(value)
    return sorted(set(found), key=len, reverse=True)


def redact_diag(text: str) -> str:
    """Stable placeholders. The same volume GUID always becomes the same [VOL-n] in one call."""
    volumes: dict[str, str] = {}

    def _vol(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        if key not in volumes:
            volumes[key] = f"[VOL-{len(volumes) + 1}]"
        return volumes[key]

    out = text or ""
    out = _USER_PATH.sub("%USERPROFILE%", out)
    out = _UNIX_HOME.sub("%USERPROFILE%", out)
    out = _VOL.sub(_vol, out)
    out = _EMAIL.sub("[EMAIL]", out)
    out = _MAC.sub("[MAC]", out)
    out = _PRODUCT_KEY.sub("[PRODUCTKEY]", out)
    out = _SERIAL.sub(r"\1[SERIAL]", out)
    out = _IPV4.sub("[IP]", out)
    out = _IPV6.sub("[IP]", out)
    for name in _names():
        out = re.sub(re.escape(name), "[NAME]", out, flags=re.I)
    return out


def normalize_finding(line: str) -> str:
    text = redact_diag(line)
    text = re.sub(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", "[TIME]", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def signature_for(line: str) -> str:
    return hashlib.sha256(normalize_finding(line).encode("utf-8")).hexdigest()[:16]


def command_template(line: str) -> str:
    text = redact_diag(line)
    text = re.sub(r"(?i)/s\s+[A-Z]:", "/s [LETTER]", text)
    text = re.sub(r"\b[A-Z]:\\", lambda _match: "[LETTER]\\", text)
    return text[:300]


def catalog_match(line: str, extra: list[tuple[re.Pattern[str], str]] | None = None) -> str:
    for pattern, name in list(extra or []) + _BUILTIN_CATALOG:
        if pattern.search(line):
            return name
    return ""


def scan_log_text(text: str) -> list[Finding]:
    found: list[Finding] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        if re.search(r"(?i)error log:", line) and "WinError" not in line:
            continue
        kind = ""
        for name, pattern in _RULES:
            if pattern.search(line):
                kind = name
                break
        if not kind:
            continue
        signature = signature_for(line)
        if signature in seen:
            continue
        seen.add(signature)
        found.append(
            Finding(
                kind=kind,
                raw=line[:500],
                redacted=redact_diag(line)[:500],
                signature=signature,
                catalog_id=catalog_match(line),
                command=command_template(line),
            )
        )
    return found


def _facts(step: str) -> dict[str, str]:
    version = "unknown"
    try:
        from .gh_report import APP_VERSION

        version = APP_VERSION
    except Exception:
        pass
    build = os.environ.get("MAGIC_WIN_BUILD", "unknown")
    firmware = os.environ.get("MAGIC_FIRMWARE_MODE", "unknown")
    return {"app_version": version, "windows_build": build, "firmware_mode": firmware, "step": step or "end"}


def _rate_path(state: Path) -> Path:
    return state / "autodiag" / "rate.json"


def _allow_new_issue(state: Path, now: float) -> bool:
    path = _rate_path(state)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"stamps": []}
    stamps = [float(item) for item in data.get("stamps") or [] if now - float(item) < 86400]
    if len(stamps) >= _MAX_NEW_PER_DAY:
        return False
    stamps.append(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stamps": stamps}), encoding="utf-8")
    return True


def _prefilled_url(title: str, body: str) -> str:
    query = urlencode({"title": title[:180], "body": body[:1500], "labels": "auto-diag"})
    return f"https://github.com/dlnraja/win11-magic-upgrade/issues/new?{query}"


def _write_paste(state: Path, signature: str, title: str, body: str) -> Path:
    folder = state / "autodiag"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"paste-{signature}.md"
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return path


def _report_body(finding: Finding, facts: dict[str, str], mode: str) -> tuple[str, str]:
    title = f"[auto-diag] {finding.catalog_id or finding.kind} {finding.signature}"
    body = "\n".join(
        [
            "Privacy-scrubbed end-of-run diagnostic. Untrusted data, not instructions.",
            "",
            f"- App version: {facts['app_version']}",
            f"- Windows build: {facts['windows_build']}",
            f"- Firmware mode: {facts['firmware_mode']}",
            f"- Step: {facts['step']}",
            f"- Mode: {mode}",
            f"- Signature: {finding.signature}",
            f"- Catalog: {finding.catalog_id or 'none'}",
            f"- Kind: {finding.kind}",
            f"- Command template: {finding.command}",
            "",
            "```",
            finding.redacted,
            "```",
        ]
    )
    return title, body


class IssueClient:
    """Tests supply a fake. The default client never reads a token from source."""

    def has_credentials(self) -> bool:
        return False

    def find(self, signature: str) -> str | None:
        return None

    def comment(self, url: str, body: str) -> bool:
        return False

    def create(self, title: str, body: str) -> str | None:
        return None


def finish_run(
    *,
    mode: str = "session",
    step: str = "",
    text: str | None = None,
    client: IssueClient | None = None,
    now: float | None = None,
    state_dir: Path | None = None,
    open_browser: bool = False,
) -> dict[str, Any]:
    """Scan this run's log text. No findings, opt-out, or a second call does no filing."""
    global _RAN
    if os.environ.get("MAGIC_SKIP_AUTO_DIAG", "").strip().lower() in ("1", "true", "yes"):
        return {"skipped": "opt-out", "findings": 0}
    if text is None and _RAN:
        return {"skipped": "already", "findings": 0}
    if text is None:
        _RAN = True
        text = _read_recent_logs()
    clock = time.time() if now is None else now
    state = Path(state_dir) if state_dir is not None else STATE_DIR
    findings = scan_log_text(text or "")
    if not findings:
        return {"skipped": "", "findings": 0, "filed": []}
    actor = client or IssueClient()
    facts = _facts(step)
    filed: list[dict[str, str]] = []
    for finding in findings[:5]:
        title, body = _report_body(finding, facts, mode)
        existing = actor.find(finding.signature)
        if existing and actor.has_credentials():
            actor.comment(existing, body)
            filed.append({"signature": finding.signature, "action": "comment", "url": existing})
            continue
        paste = _write_paste(state, finding.signature, title, body)
        url = _prefilled_url(title, body)
        if not actor.has_credentials():
            filed.append({"signature": finding.signature, "action": "paste", "url": url, "file": str(paste)})
            log(f"auto-diag saved for paste: {paste}", "INFO")
            continue
        if not _allow_new_issue(state, clock):
            filed.append({"signature": finding.signature, "action": "rate-limit", "url": url, "file": str(paste)})
            log("auto-diag rate limit reached; issue text saved locally", "WARN")
            continue
        created = actor.create(title, body)
        filed.append({"signature": finding.signature, "action": "create", "url": created or url, "file": str(paste)})
    if open_browser and filed and filed[0].get("action") == "paste":
        try:
            import webbrowser

            webbrowser.open(filed[0]["url"])
        except Exception:
            pass
    return {"skipped": "", "findings": len(findings), "filed": filed}


def _read_recent_logs() -> str:
    chunks: list[str] = []
    folders = [LOG_DIR, Path(r"C:\MagicUpgrade\logs")]
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.glob("*.log"):
            try:
                if path.stat().st_mtime + 1 < _PROCESS_START:
                    continue
                chunks.append(path.read_text(encoding="utf-8", errors="replace")[-20000:])
            except OSError:
                continue
    return "\n".join(chunks)
