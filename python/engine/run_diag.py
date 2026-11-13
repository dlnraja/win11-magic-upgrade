"""End-of-run scan. Redacted findings become one GitHub auto-diag issue per signature.

No token is stored in the program. Without ``gh`` or ``MAGIC_GITHUB_TOKEN`` /
``GITHUB_TOKEN``, a ready-to-paste file and a prefilled issue URL are saved.
Set ``MAGIC_SKIP_AUTO_DIAG=1`` to do nothing.

Each run also packs previous logs that have not been sent yet: app logs,
``C:\\MagicUpgrade\\logs``, state counters, BCD export metadata, Panther and
``$WINDOWS.~BT`` excerpts, and SrtTrail. The issue gets a short summary.
The full redacted zip is local unless ``MAGIC_AUTO_DIAG_GIST=1`` and ``gh``
can create a gist. A ledger remembers what was already packed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .logutil import LOG_DIR, SETUPACT, SETUPERR, STATE_DIR, log

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
_USER_PATH = re.compile(r"(?i)[A-Z]:\\+Users\\+[^\\/\s\"']+")
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
_SUMMARY_CAP = 4000
_LOG_CAP = 48_000
_EXCERPT_CAP = 8_000
_BUNDLE_CAP = 200_000
_MAX_NEW_FILES = 40
_COUNTER_NAMES = {
    "postflight-loop.json",
    "boot-postflight.json",
    "boot-preflight.json",
    "boot-emergency.json",
    "bootable-status.json",
}


@dataclass
class HistoryItem:
    kind: str
    name: str
    text: str
    identity: str
    mtime: float = 0


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


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80]
    return cleaned or "log"


def _identity(kind: str, name: str, text: str) -> str:
    return hashlib.sha256(f"{kind}\n{name}\n{text}".encode("utf-8")).hexdigest()[:20]


def _read_capped(path: Path, cap: int, *, tail: bool) -> str:
    raw = path.read_bytes()
    if len(raw) > cap:
        raw = raw[-cap:] if tail else raw[:cap]
    return redact_diag(raw.decode("utf-8", errors="replace"))


def _item(kind: str, name: str, text: str, mtime: float) -> HistoryItem | None:
    body = (text or "").strip()
    if not body:
        return None
    safe = _safe_name(name)
    return HistoryItem(kind=kind, name=safe, text=body, identity=_identity(kind, safe, body), mtime=mtime)


def _log_items(folder: Path, kind: str) -> list[HistoryItem]:
    if not folder.is_dir():
        return []
    found: list[HistoryItem] = []
    for path in sorted(folder.glob("*")):
        if not path.is_file() or path.suffix.lower() not in {".log", ".txt"}:
            continue
        if "autodiag" in {part.lower() for part in path.parts}:
            continue
        try:
            text = _read_capped(path, _LOG_CAP, tail=True)
            item = _item(kind, path.name, text, path.stat().st_mtime)
        except OSError:
            continue
        if item is not None:
            found.append(item)
    return found


def _counter_items(state: Path) -> list[HistoryItem]:
    if not state.is_dir():
        return []
    found: list[HistoryItem] = []
    for path in sorted(state.iterdir()):
        if not path.is_file():
            continue
        if path.name not in _COUNTER_NAMES and "count" not in path.name.lower():
            continue
        try:
            text = _read_capped(path, _EXCERPT_CAP, tail=False)
            item = _item("state-counter", path.name, text, path.stat().st_mtime)
        except OSError:
            continue
        if item is not None:
            found.append(item)
    return found


def _bcd_meta_items(folders: list[Path]) -> list[HistoryItem]:
    """Names and sizes only. The hive bytes are never read."""
    found: list[HistoryItem] = []
    seen: set[Path] = set()
    for folder in folders:
        if not folder.is_dir():
            continue
        candidates: list[Path] = []
        if folder.name.lower() == "bcd-backups":
            candidates.extend(path for path in folder.iterdir() if path.is_file())
        else:
            for child in folder.iterdir():
                if child.is_file() and "bcd" in child.name.lower():
                    candidates.append(child)
                elif child.is_dir():
                    candidates.extend(
                        path for path in child.iterdir() if path.is_file() and "bcd" in path.name.lower()
                    )
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            try:
                text = f"{path.name} bytes={path.stat().st_size}"
                item = _item("bcd-meta", path.name, text, path.stat().st_mtime)
            except OSError:
                continue
            if item is not None:
                found.append(item)
    return found


def default_excerpt_paths() -> list[tuple[str, Path]]:
    system = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    windows_bt = Path(r"C:\$WINDOWS.~BT")
    return [
        ("panther", system / "Panther" / "setupact.log"),
        ("panther", system / "Panther" / "setuperr.log"),
        ("panther", SETUPACT),
        ("panther", SETUPERR),
        ("windows-bt", windows_bt / "Sources" / "Panther" / "setupact.log"),
        ("windows-bt", windows_bt / "Sources" / "Panther" / "setuperr.log"),
        ("windows-bt", windows_bt / "Sources" / "Rollback" / "setupact.log"),
        ("windows-bt", windows_bt / "Sources" / "Rollback" / "setuperr.log"),
        ("srt", system / "System32" / "LogFiles" / "Srt" / "SrtTrail.txt"),
    ]


def collect_history(
    *,
    log_dirs: list[Path] | None = None,
    state_dir: Path | None = None,
    excerpts: list[tuple[str, Path]] | None = None,
    bcd_dirs: list[Path] | None = None,
) -> list[HistoryItem]:
    """Redacted history. Missing folders are skipped. BCD exports contribute metadata only."""
    if log_dirs is None and state_dir is None and excerpts is None and bcd_dirs is None:
        log_dirs = [LOG_DIR, Path(r"C:\MagicUpgrade\logs")]
        state_dir = STATE_DIR
        excerpts = default_excerpt_paths()
        bcd_dirs = [STATE_DIR / "bcd-backups", STATE_DIR / "partition-backups"]
    items: list[HistoryItem] = []
    for folder in log_dirs or []:
        normalized = str(folder).replace("/", "\\").lower()
        kind = "magic-log" if normalized.endswith("\\magicupgrade\\logs") and "win11magicupgrade" not in normalized else "session-log"
        items.extend(_log_items(folder, kind))
    if state_dir is not None:
        items.extend(_counter_items(state_dir))
    for kind, path in excerpts or []:
        if not path.is_file():
            continue
        try:
            text = _read_capped(path, _EXCERPT_CAP, tail=True)
            item = _item(kind, path.name, text, path.stat().st_mtime)
        except OSError:
            continue
        if item is not None:
            items.append(item)
    items.extend(_bcd_meta_items(bcd_dirs or []))
    return items


def _ledger_path(state: Path) -> Path:
    return state / "autodiag" / "uploaded.json"


def load_uploaded(state: Path) -> set[str]:
    try:
        data = json.loads(_ledger_path(state).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(item) for item in data.get("ids") or []}


def save_uploaded(state: Path, ids: set[str]) -> None:
    path = _ledger_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ids": sorted(ids)}), encoding="utf-8")


def pending_history(items: list[HistoryItem], uploaded: set[str]) -> list[HistoryItem]:
    fresh = [item for item in items if item.identity not in uploaded]
    fresh.sort(key=lambda item: item.mtime, reverse=True)
    return fresh[:_MAX_NEW_FILES]


def compact_summary(items: list[HistoryItem], *, cap: int = _SUMMARY_CAP, bundle: str = "", issue: str = "") -> str:
    """One line per new file and one line per signature. Cut at ``cap``."""
    lines = ["Earlier logs (new since the last auto-diag upload):"]
    seen: set[str] = set()
    for item in items:
        lines.append(f"- {item.kind} {item.name}")
        for finding in scan_log_text(item.text):
            if finding.signature in seen:
                continue
            seen.add(finding.signature)
            label = finding.catalog_id or finding.kind
            lines.append(f"  sig {finding.signature} {label}: {finding.redacted[:160]}")
            if len("\n".join(lines)) >= cap:
                lines.append("…")
                break
        if len("\n".join(lines)) >= cap:
            break
    if not items:
        lines.append("- No new logs.")
    if bundle:
        lines.append(f"Bundle: {bundle}")
    if issue:
        lines.append(f"Issue: {issue}")
    return "\n".join(lines)[:cap]


def write_redacted_zip(items: list[HistoryItem], dest: Path, *, cap: int = _BUNDLE_CAP) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    used = 0
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, item in enumerate(items):
            data = item.text.encode("utf-8")
            room = cap - used
            if room <= 0:
                break
            if len(data) > room:
                data = data[:room]
            archive.writestr(f"{item.kind}/{index:02d}-{item.name}", data)
            used += len(data)
    return dest


def _gist_upload(zip_path: Path) -> str | None:
    """Create a gist only when explicitly enabled. No token is placed on the command line."""
    flag = os.environ.get("MAGIC_AUTO_DIAG_GIST", "").strip().lower()
    if flag not in ("1", "true", "yes"):
        return None
    try:
        import subprocess

        proc = subprocess.run(
            ["gh", "gist", "create", str(zip_path), "--desc", "Redacted Win11 Magic Upgrade auto-diag bundle"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"https://gist\.github\.com/\S+", (proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0 or match is None:
        return None
    return match.group(0)


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


def _with_history(body: str, summary: str) -> str:
    if not summary:
        return body
    return body + "\n\n## Earlier logs\n\n" + summary


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

    def upload_bundle(self, zip_path: Path) -> str | None:
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
    sources: list[HistoryItem] | None = None,
) -> dict[str, Any]:
    """Scan this run and any earlier logs not yet packed. No findings, opt-out, or a second call does no filing."""
    global _RAN
    if os.environ.get("MAGIC_SKIP_AUTO_DIAG", "").strip().lower() in ("1", "true", "yes"):
        return {"skipped": "opt-out", "findings": 0}
    implicit = text is None and sources is None
    if implicit and _RAN:
        return {"skipped": "already", "findings": 0}
    if implicit:
        _RAN = True
        text = _read_recent_logs()
        sources = collect_history()
    clock = time.time() if now is None else now
    state = Path(state_dir) if state_dir is not None else STATE_DIR
    uploaded = load_uploaded(state)
    fresh = pending_history(list(sources or []), uploaded)
    skipped = len(list(sources or [])) - len([item for item in (sources or []) if item.identity not in uploaded])
    history_note: dict[str, Any] = {
        "new": len(fresh),
        "skipped": max(0, skipped),
        "attached": False,
        "bundle": "",
        "upload": "",
    }
    combined = text or ""
    if fresh:
        combined = combined + "\n" + "\n".join(item.text for item in fresh)
    findings = scan_log_text(combined)
    if not findings:
        return {"skipped": "", "findings": 0, "filed": [], "history": history_note}
    actor = client or IssueClient()
    facts = _facts(step)
    bundle_note = ""
    issue_hint = ""
    if fresh:
        stamp = hashlib.sha256("".join(item.identity for item in fresh).encode("utf-8")).hexdigest()[:12]
        bundle_path = state / "autodiag" / f"bundle-{stamp}.zip"
        write_redacted_zip(fresh, bundle_path)
        upload = actor.upload_bundle(bundle_path) or _gist_upload(bundle_path) or ""
        shown = upload or redact_diag(str(bundle_path))
        bundle_note = shown
        history_note["bundle"] = str(bundle_path)
        history_note["upload"] = upload
    summary = compact_summary(fresh, bundle=bundle_note)
    filed: list[dict[str, str]] = []
    for finding in findings[:5]:
        title, body = _report_body(finding, facts, mode)
        existing = actor.find(finding.signature)
        issue_hint = existing or ""
        body = _with_history(body, compact_summary(fresh, bundle=bundle_note, issue=issue_hint))
        if existing and actor.has_credentials():
            actor.comment(existing, body)
            filed.append({"signature": finding.signature, "action": "comment", "url": existing})
            continue
        url = _prefilled_url(title, _with_history(_report_body(finding, facts, mode)[1], summary))
        if not issue_hint:
            issue_hint = url
            body = _with_history(_report_body(finding, facts, mode)[1], compact_summary(fresh, bundle=bundle_note, issue=url))
        paste = _write_paste(state, finding.signature, title, body)
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
    if filed and fresh:
        save_uploaded(state, uploaded | {item.identity for item in fresh})
        history_note["attached"] = True
    return {"skipped": "", "findings": len(findings), "filed": filed, "history": history_note}


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
