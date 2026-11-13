"""Self-update from public GitHub Releases, and safe pin refresh.

The check uses the unauthenticated Releases API with a short timeout. Offline
failures keep the current build. A newer asset is stored under a pending
folder and swapped on the next launch, never during a boot repair. The
previous executable is kept so a bad swap can be rolled back.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from .gh_report import APP_VERSION, DEFAULT_REPO
from .logutil import STATE_DIR, log

_API = "https://api.github.com/repos/{repo}/releases/latest"
_PINS_URL = "https://raw.githubusercontent.com/{repo}/main/pins/release_pins.json"
_BOOT_FLAGS = frozenset(
    {
        "--boot-fix",
        "--boot-heal",
        "--mbr",
        "--srp",
        "--hybrid",
        "--hybrid-activate",
        "--oneclick",
        "--resume",
        "--boot-resume",
        "--openduet-write",
    }
)
_CRITICAL = 0

APPLY_HELPER = """@echo off
REM Swap a staged Win11 Magic Upgrade build after this process exits.
REM No pause and no console prompt.
setlocal
set PID=%~1
set SRC=%~2
set DST=%~3
set PREV=%~4
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if %ERRORLEVEL%==0 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
if not exist "%SRC%" exit /b 1
if exist "%DST%" move /y "%DST%" "%PREV%" >nul
move /y "%SRC%" "%DST%" >nul
if exist "%DST%" start "" "%DST%"
exit /b 0
"""


@dataclass(frozen=True)
class UpdateDecision:
    status: str
    current: str
    latest: str = ""
    asset_name: str = ""
    asset_url: str = ""
    sums_url: str = ""
    html_url: str = ""
    reason: str = ""


def parse_semver(value: str) -> tuple[int, int, int]:
    text = (value or "").strip().lstrip("vV")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _parse_ver(value: str) -> tuple[int, ...]:
    """Older callers compare this tuple directly."""
    return parse_semver(value)


def compare_semver(left: str, right: str) -> int:
    a, b = parse_semver(left), parse_semver(right)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def current_version() -> str:
    override = (os.environ.get("MAGIC_APP_VERSION") or "").strip()
    if override:
        return override.lstrip("vV")
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text.lstrip("vV")
    return APP_VERSION


def updates_root() -> Path:
    root = STATE_DIR / "updates"
    root.mkdir(parents=True, exist_ok=True)
    return root


def in_boot_critical() -> bool:
    return _CRITICAL > 0 or (os.environ.get("MAGIC_BOOT_CRITICAL") or "").strip() in ("1", "true", "yes")


class boot_critical_section:
    """Block update download and swap while boot repair is running."""

    def __enter__(self) -> "boot_critical_section":
        global _CRITICAL
        _CRITICAL += 1
        return self

    def __exit__(self, *_exc: object) -> None:
        global _CRITICAL
        _CRITICAL = max(0, _CRITICAL - 1)


def update_disabled(argv: list[str] | None = None) -> bool:
    flags = {a.lower() for a in (argv or [])}
    if "--update" in flags:
        return False
    if "--no-update" in flags:
        return True
    return (os.environ.get("MAGIC_NO_UPDATE") or os.environ.get("MAGIC_SKIP_UPDATE_CHECK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def boot_flags_present(argv: list[str] | None) -> bool:
    flags = {a.lower() for a in (argv or [])}
    return bool(flags & _BOOT_FLAGS) or in_boot_critical()


def select_asset(assets: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Prefer the portable exe, and the SHA256SUMS asset published beside it."""
    exe = None
    sums = None
    for asset in assets or []:
        name = str(asset.get("name") or "")
        lower = name.lower()
        if lower == "sha256sums.txt":
            sums = asset
        elif lower.endswith(".exe") and "win11magicupgrade" in lower.replace(" ", ""):
            exe = asset
    if exe is None:
        for asset in assets or []:
            name = str(asset.get("name") or "").lower()
            if name.endswith(".exe"):
                exe = asset
                break
    return exe, sums


def parse_sha256sums(text: str, filename: str) -> str | None:
    want = filename.lower()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1].lstrip("*")
        if name.lower() == want and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return digest.lower()
    return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def signature_acceptable(status: str | None) -> tuple[bool, str]:
    """Accept a missing signature. Reject a signature that is present and bad."""
    text = (status or "").strip().lower()
    if text in ("", "notsigned", "skipped", "none"):
        return True, "no Authenticode signature; sha256 is the check"
    if text == "valid":
        return True, "Authenticode signature is valid"
    return False, f"Authenticode signature is {status}"


def decide_update(
    *,
    current: str,
    latest: str | None,
    offline: bool = False,
    hash_ok: bool | None = None,
    signature: str | None = None,
) -> UpdateDecision:
    if offline or not latest:
        return UpdateDecision("offline", current, latest or "", reason="GitHub Releases unreachable; keeping this build")
    cmp = compare_semver(current, latest)
    if cmp == 0:
        return UpdateDecision("same", current, latest, reason="already on this release")
    if cmp > 0:
        return UpdateDecision("older", current, latest, reason="this build is newer than the published release")
    if hash_ok is False:
        return UpdateDecision("bad_hash", current, latest, reason="downloaded file does not match SHA256SUMS")
    ok_sig, why = signature_acceptable(signature)
    if not ok_sig:
        return UpdateDecision("bad_signature", current, latest, reason=why)
    return UpdateDecision("newer", current, latest, reason="a newer release is available")


def _read_json(url: str, timeout: int, opener: Callable | None) -> dict[str, Any] | None:
    fetch = opener or urlopen
    try:
        request = Request(url, headers={"User-Agent": f"Win11MagicUpgrade/{current_version()}", "Accept": "application/vnd.github+json"})
        with fetch(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def fetch_latest_release(
    *,
    repo: str = DEFAULT_REPO,
    timeout: int = 20,
    opener: Callable | None = None,
) -> dict[str, Any] | None:
    return _read_json(_API.format(repo=repo), timeout, opener)


def bundled_pins() -> dict[str, Any]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "pins" / "release_pins.json"
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def load_pins() -> dict[str, Any]:
    """Cached pins, then the copy shipped in the repo. Never raises."""
    cache = STATE_DIR / "pins-cache.json"
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("opencore") and data.get("win11_floor"):
                return data
        except Exception:
            pass
    return bundled_pins()


def refresh_pins(
    *,
    repo: str = DEFAULT_REPO,
    timeout: int = 20,
    opener: Callable | None = None,
) -> dict[str, Any]:
    """Refresh Win11 floor and OpenCore pin when the network works. Offline keeps the last good copy."""
    if in_boot_critical():
        log("Pin refresh skipped during a boot-critical operation", "INFO")
        return load_pins()
    data = _read_json(_PINS_URL.format(repo=repo), timeout, opener)
    if not isinstance(data, dict):
        log("Pin refresh offline; using the saved pins", "INFO")
        return load_pins()
    oc = data.get("opencore") or {}
    floor = data.get("win11_floor") or {}
    sha = str(oc.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha) or not str(oc.get("version") or ""):
        log("Pin refresh rejected: OpenCore pin is incomplete", "WARN")
        return load_pins()
    try:
        int(floor.get("build"))
        int(floor.get("ubr"))
    except (TypeError, ValueError):
        log("Pin refresh rejected: Win11 floor is incomplete", "WARN")
        return load_pins()
    cache = STATE_DIR / "pins-cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log(f"Pins refreshed (OpenCore {oc.get('version')}, Win11 floor {floor.get('version')})", "OK")
    return data


def pin_opencore() -> tuple[str, str] | None:
    oc = (load_pins().get("opencore") or {})
    version = str(oc.get("version") or "")
    sha = str(oc.get("sha256") or "").lower()
    if version and re.fullmatch(r"[0-9a-f]{64}", sha):
        return version, sha
    return None


def pin_win11_floor() -> tuple[str, int, int] | None:
    floor = (load_pins().get("win11_floor") or {})
    try:
        return str(floor.get("version") or ""), int(floor["build"]), int(floor["ubr"])
    except (KeyError, TypeError, ValueError):
        return None


def stage_bytes(root: Path, name: str, data: bytes, expected_sha: str) -> UpdateDecision:
    """Write the download beside the install. A bad hash deletes the temp file."""
    digest = sha256_bytes(data)
    if digest.lower() != (expected_sha or "").lower():
        return UpdateDecision("bad_hash", current_version(), reason="downloaded file does not match SHA256SUMS")
    pending = root / "pending"
    if pending.exists():
        shutil.rmtree(pending, ignore_errors=True)
    pending.mkdir(parents=True, exist_ok=True)
    target = pending / name
    tmp = pending / (name + ".partial")
    tmp.write_bytes(data)
    tmp.replace(target)
    (pending / "SHA256").write_text(digest.lower() + "  " + name + "\n", encoding="utf-8")
    (pending / "apply_update.cmd").write_text(APPLY_HELPER, encoding="ascii")
    return UpdateDecision("staged", current_version(), reason=f"staged {name} for the next launch")


def apply_staged(root: Path, *, install_name: str = "Win11MagicUpgrade.exe") -> bool:
    """Swap pending into place and keep the previous file. Used on the next launch."""
    pending = root / "pending" / install_name
    sums = root / "pending" / "SHA256"
    if not pending.is_file() or not sums.is_file():
        return False
    expected = parse_sha256sums(sums.read_text(encoding="utf-8"), install_name)
    if not expected or sha256_bytes(pending.read_bytes()) != expected:
        shutil.rmtree(root / "pending", ignore_errors=True)
        return False
    current = root / install_name
    previous_dir = root / "previous"
    previous_dir.mkdir(parents=True, exist_ok=True)
    previous = previous_dir / install_name
    if current.is_file():
        if previous.exists():
            previous.unlink()
        shutil.copy2(current, previous)
    staged = root / (install_name + ".new")
    shutil.copy2(pending, staged)
    staged.replace(current)
    shutil.rmtree(root / "pending", ignore_errors=True)
    return current.is_file() and sha256_bytes(current.read_bytes()) == expected


def rollback_installed(root: Path, *, install_name: str = "Win11MagicUpgrade.exe") -> bool:
    previous = root / "previous" / install_name
    current = root / install_name
    if not previous.is_file():
        return False
    backup = root / (install_name + ".rollback-tmp")
    if current.is_file():
        if backup.exists():
            backup.unlink()
        current.replace(backup)
    shutil.copy2(previous, current)
    if backup.exists():
        backup.replace(previous)
    return current.is_file()


def is_code_path(path: str) -> bool:
    text = path.replace("\\", "/")
    if text.startswith(("docs/", "pins/")) or text.endswith(".md"):
        return False
    if text in ("VERSION", "build/version_info.txt"):
        return False
    return text.startswith(("python/", "src/", "build/", ".github/workflows/")) and text.endswith(
        (".py", ".ps1", ".yml", ".cmd")
    )


def should_tag_release(ref: str, message: str, files: list[str]) -> bool:
    """Tags are created only for a code push that already landed on main."""
    if ref != "refs/heads/main":
        return False
    text = message or ""
    if "chore: release" in text or "[skip release]" in text:
        return False
    return any(is_code_path(path) for path in files)


def bump_level(messages: list[str]) -> str:
    major = minor = False
    for message in messages:
        first = (message or "").splitlines()[0]
        if "BREAKING CHANGE" in message or first.startswith(("feat!", "fix!")) or re.search(r"^[a-z]+(\([^)]+\))?!:", first):
            major = True
        elif first.startswith("feat"):
            minor = True
    if major:
        return "major"
    if minor:
        return "minor"
    return "patch"


def next_version(current: str, level: str) -> str:
    major, minor, patch = parse_semver(current)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _read_bytes(url: str, timeout: int, opener: Callable | None) -> bytes | None:
    fetch = opener or urlopen
    try:
        request = Request(url, headers={"User-Agent": f"Win11MagicUpgrade/{current_version()}"})
        with fetch(request, timeout=timeout) as response:
            return response.read()
    except Exception:
        return None


def stage_release_payload(
    payload: dict[str, Any],
    *,
    timeout: int = 60,
    opener: Callable | None = None,
    root: Path | None = None,
) -> UpdateDecision:
    """Download the published exe only when SHA256SUMS matches. A bad hash is deleted."""
    current = current_version()
    exe, sums = select_asset(list(payload.get("assets") or []))
    if not exe or not sums:
        return UpdateDecision("newer", current, reason="release has no portable exe or SHA256SUMS; not installing")
    sums_bytes = _read_bytes(str(sums.get("browser_download_url") or ""), timeout, opener)
    data = _read_bytes(str(exe.get("browser_download_url") or ""), timeout, opener)
    if sums_bytes is None or data is None:
        return UpdateDecision("offline", current, reason="download failed; keeping this build")
    expected = parse_sha256sums(sums_bytes.decode("utf-8", errors="replace"), str(exe.get("name") or ""))
    if not expected:
        return UpdateDecision("bad_hash", current, reason="SHA256SUMS has no line for the portable exe")
    staged = stage_bytes(root or updates_root(), str(exe.get("name") or "Win11MagicUpgrade.exe"), data, expected)
    if staged.status != "staged":
        return staged
    pending = (root or updates_root()) / "pending" / str(exe.get("name") or "Win11MagicUpgrade.exe")
    status = _authenticode(pending)
    ok, why = signature_acceptable(status)
    if not ok:
        shutil.rmtree((root or updates_root()) / "pending", ignore_errors=True)
        return UpdateDecision("bad_signature", current, reason=why)
    return staged


def _authenticode(path: Path) -> str:
    if os.name != "nt" or not path.is_file():
        return "skipped"
    try:
        from .proc_guard import run_bounded

        result = run_bounded(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-AuthenticodeSignature -LiteralPath '{path}').Status",
            ],
            timeout=30,
        )
        return (result.output or "skipped").strip().splitlines()[-1] if result.output else "skipped"
    except Exception:
        return "skipped"


def check_for_update(
    *,
    argv: list[str] | None = None,
    opener: Callable | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    """Query the latest release. Stage a verified download only when it is safe to do so."""
    current = current_version()
    result: dict[str, Any] = {
        "current": current,
        "latest": "",
        "newer": False,
        "url": "",
        "notes": "",
        "status": "skipped",
    }
    if boot_flags_present(argv) or in_boot_critical():
        result["status"] = "deferred"
        result["notes"] = "update deferred until boot repair finishes"
        log(result["notes"], "INFO")
        return result
    if update_disabled(argv):
        result["notes"] = "update check disabled"
        log(result["notes"], "INFO")
        return result
    payload = fetch_latest_release(timeout=timeout, opener=opener)
    if not payload:
        decision = decide_update(current=current, latest=None, offline=True)
        result["status"] = decision.status
        result["notes"] = decision.reason
        log(decision.reason, "INFO")
        return result
    tag = str(payload.get("tag_name") or "")
    latest = tag.lstrip("vV")
    result["latest"] = latest
    result["url"] = str(payload.get("html_url") or "")
    decision = decide_update(current=current, latest=latest)
    result["status"] = decision.status
    result["newer"] = decision.status == "newer"
    result["notes"] = decision.reason
    if decision.status == "newer":
        staged = stage_release_payload(payload, timeout=timeout, opener=opener)
        result["status"] = staged.status
        result["notes"] = staged.reason
        result["newer"] = staged.status in ("newer", "staged")
        log(staged.reason, "WARN" if staged.status in ("newer", "staged", "bad_hash", "bad_signature") else "INFO")
        if staged.status == "staged":
            log(f"Newer release {latest} (this build is {current}). It will be swapped on the next launch.", "WARN")
    elif decision.status == "same":
        log(f"Up to date ({current})", "OK")
    else:
        log(decision.reason, "INFO")
    try:
        from .logutil import save_state

        save_state({"UpdateCheck": result})
    except Exception:
        pass
    return result


def startup_update(argv: list[str] | None = None) -> dict[str, Any]:
    """Apply a previously staged build, then look for a newer one. Boot flags skip both."""
    if boot_flags_present(argv):
        log("Self-update skipped: a boot-critical action is starting", "INFO")
        return {"status": "deferred"}
    if "--no-update" in {a.lower() for a in (argv or [])} or (
        update_disabled(argv) and "--update" not in {a.lower() for a in (argv or [])}
    ):
        log("Self-update disabled", "INFO")
        return {"status": "skipped"}
    root = updates_root()
    if apply_staged(root):
        log("Staged update is now the current build. The previous build was kept for rollback.", "OK")
    try:
        refresh_pins()
    except Exception as exc:
        log(f"Pin refresh skipped: {exc}", "INFO")
    return check_for_update(argv=argv)
