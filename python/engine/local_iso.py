"""Find a Windows ISO or extracted setup folder already on this PC.

The scan is read-only, skips system directories and reparse-point loops, and
stops after about 60 seconds. It does not use the network. A valid local image
is preferred over a download. SHA-256 is checked against known official hashes
when that list is present; otherwise the file is accepted with one warning.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

MIN_MEDIA_BYTES = 3_000_000_000
SCAN_BUDGET_S = 60
_REPARSE = 0x400
_SKIP_DIRS = {
    "windows",
    "programdata",
    "program files",
    "program files (x86)",
    "$recycle.bin",
    "system volume information",
    "recovery",
    "perflogs",
    "documents and settings",
    "config.msi",
}
_PRIORITY = (
    "users",
    "downloads",
    "téléchargements",
    "telechargements",
    "desktop",
    "bureau",
    "documents",
    "onedrive",
    "iso",
    "isos",
    "images",
)

_lock = threading.Lock()
_thread: threading.Thread | None = None


def cache_path() -> Path:
    override = (os.environ.get("MAGIC_ISO_SCAN_CACHE") or "").strip()
    if override:
        return Path(override)
    from .logutil import STATE_DIR

    return STATE_DIR / "local-iso-scan.json"


def known_hashes() -> set[str]:
    """Official SHA-256 values shipped with the app, plus MAGIC_ISO_SHA256."""
    found: set[str] = set()
    extra = (os.environ.get("MAGIC_ISO_SHA256") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", extra):
        found.add(extra)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "official_iso_hashes.json"
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            break
        values = payload.get("sha256") if isinstance(payload, dict) else payload
        if isinstance(values, list):
            for item in values:
                text = str(item).strip().lower()
                if re.fullmatch(r"[0-9a-f]{64}", text):
                    found.add(text)
        break
    return found


def override_iso() -> Path | None:
    raw = (os.environ.get("MAGIC_ISO") or "").strip().strip('"')
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() else None


def apply_cli_iso(argv: list[str]) -> list[str]:
    """Remove --iso PATH from argv and remember it for this process."""
    kept: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        lower = arg.lower()
        if lower == "--iso" and index + 1 < len(argv):
            os.environ["MAGIC_ISO"] = argv[index + 1]
            index += 2
            continue
        if lower.startswith("--iso="):
            os.environ["MAGIC_ISO"] = arg.split("=", 1)[1]
            index += 1
            continue
        kept.append(arg)
        index += 1
    return kept


def remember_iso_path(path: str) -> None:
    text = (path or "").strip()
    if text:
        os.environ["MAGIC_ISO"] = text
    else:
        os.environ.pop("MAGIC_ISO", None)
    try:
        target = cache_path().with_name("iso-override.txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError:
        pass


def saved_iso_path() -> str:
    env = (os.environ.get("MAGIC_ISO") or "").strip()
    if env:
        return env
    try:
        target = cache_path().with_name("iso-override.txt")
        if target.is_file():
            return target.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attrs = getattr(path.lstat(), "st_file_attributes", 0) or 0
        return bool(int(attrs) & _REPARSE)
    except OSError:
        return True


def _skip_dir(name: str) -> bool:
    return name.lower() in _SKIP_DIRS or name.startswith("$")


def _priority(name: str) -> tuple[int, str]:
    lower = name.lower()
    for index, item in enumerate(_PRIORITY):
        if lower == item or lower.startswith(item):
            return (index, lower)
    return (len(_PRIORITY), lower)


def scan_tree(
    roots: list[Path],
    *,
    budget_s: float = SCAN_BUDGET_S,
    min_bytes: int = MIN_MEDIA_BYTES,
    clock: Callable[[], float] = time.monotonic,
) -> list[dict[str, Any]]:
    """Read-only walk. Returns iso / install.wim / install.esd files at or above ``min_bytes``."""
    deadline = clock() + max(0.0, float(budget_s))
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    stack: list[Path] = [Path(root) for root in roots if Path(root).exists()]
    while stack:
        if clock() >= deadline:
            break
        folder = stack.pop()
        try:
            key = str(folder.resolve()).lower()
        except OSError:
            key = str(folder).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            children = list(folder.iterdir())
        except OSError:
            continue
        dirs: list[Path] = []
        for child in children:
            if clock() >= deadline:
                break
            name = child.name
            try:
                if child.is_dir():
                    if _skip_dir(name) or _is_reparse(child):
                        continue
                    dirs.append(child)
                    continue
                lower = name.lower()
                kind = ""
                if lower.endswith(".iso"):
                    kind = "iso"
                elif lower in ("install.wim", "install.esd"):
                    kind = "wim" if lower.endswith(".wim") else "esd"
                if not kind:
                    continue
                size = child.stat().st_size
            except OSError:
                continue
            if size < min_bytes:
                continue
            found.append({"path": str(child), "size": int(size), "kind": kind, "mtime": child.stat().st_mtime})
        dirs.sort(key=lambda item: _priority(item.name))
        stack.extend(reversed(dirs))
    return found


def parse_cversion(text: str) -> int:
    match = re.search(r"(?i)MinClient\s*=\s*\d+\.\d+\.(\d+)", text or "")
    return int(match.group(1)) if match else 0


def parse_lang_ini(text: str) -> str:
    match = re.search(r"(?im)^\s*([a-z]{2}(?:-[a-z]{2})?)\s*=", text or "")
    return match.group(1) if match else ""


def parse_wim_info(text: str) -> dict[str, str | int]:
    """Fields from DISM /Get-WimInfo or a similar listing."""

    def field(label: str) -> str:
        match = re.search(rf"(?im)^{label}\s*:\s*(.+)$", text or "")
        return match.group(1).strip() if match else ""

    version = field("Version")
    build = 0
    match = re.search(r"\d+\.\d+\.(\d+)", version)
    if match:
        build = int(match.group(1))
    edition = field("Name") or field("Edition")
    arch = field("Architecture").lower()
    if arch in ("x64", "amd64", "64-bit"):
        arch = "x64"
    elif arch in ("x86", "32-bit"):
        arch = "x86"
    elif "arm" in arch:
        arch = "arm64"
    return {
        "build": build,
        "edition": edition,
        "language": field("Default Language") or field("Language"),
        "arch": arch,
    }


def _lang_key(value: str) -> str:
    return (value or "").strip().lower().replace("_", "-").split("-")[0]


def _edition_ok(candidate: str, want: str) -> bool:
    if not want or not candidate:
        return True
    left = re.sub(r"[^a-z0-9]+", "", candidate.lower())
    right = re.sub(r"[^a-z0-9]+", "", want.lower())
    return right in left or left in right


def _arch_ok(candidate: str, want: str) -> bool:
    if not want or not candidate:
        return True
    aliases = {"amd64": "x64", "x86_64": "x64", "ia32": "x86"}
    return aliases.get(candidate.lower(), candidate.lower()) == aliases.get(want.lower(), want.lower())


def hash_verdict(path: Path, *, digest: str = "", hasher: Callable[[Path], str] | None = None) -> str:
    """official, unknown, or mismatch. Unknown is accepted by the caller with a warning."""
    known = known_hashes()
    expected = (os.environ.get("MAGIC_ISO_SHA256") or "").strip().lower()
    if not known and not expected:
        return "unknown"
    value = (digest or "").strip().lower()
    if not value and hasher is not None:
        value = hasher(path).strip().lower()
    if expected and value != expected:
        return "mismatch"
    if value and value in known:
        return "official"
    return "unknown"


def pick_best(
    records: list[dict[str, Any]],
    *,
    win: str,
    arch: str = "x64",
    language: str = "",
    edition: str = "",
    min_build: int = 0,
) -> dict[str, Any] | None:
    """Newest matching Windows build. ISOs beat extracted wim/esd at the same build."""
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    family = str(win)
    for record in records:
        if str(record.get("win") or family) not in ("", family):
            continue
        build = int(record.get("build") or 0)
        if min_build and build and build < min_build:
            continue
        if record.get("hash") == "mismatch":
            continue
        if not _lang_ok(str(record.get("language") or ""), language):
            continue
        if not _edition_ok(str(record.get("edition") or ""), edition):
            continue
        if not _arch_ok(str(record.get("arch") or ""), arch):
            continue
        kind_rank = 1 if record.get("kind") == "iso" else 0
        ranked.append(((build, kind_rank, int(record.get("size") or 0)), record))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _lang_ok(candidate: str, want: str) -> bool:
    if not want or not candidate:
        return True
    return _lang_key(candidate) == _lang_key(want)


def save_cache(files: list[dict[str, Any]], *, complete: bool) -> None:
    payload = {"scanned_at": time.time(), "complete": complete, "files": files}
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def load_cache(*, max_age_s: float = 24 * 3600) -> list[dict[str, Any]]:
    path = cache_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        scanned = float(payload.get("scanned_at") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []
    if scanned and time.time() - scanned > max_age_s:
        return []
    fresh: list[dict[str, Any]] = []
    for item in payload.get("files") or []:
        if not isinstance(item, dict):
            continue
        file_path = Path(str(item.get("path") or ""))
        try:
            if file_path.is_file() and file_path.stat().st_size == int(item.get("size") or -1):
                fresh.append(item)
        except OSError:
            continue
    return fresh


def profile_media_dirs() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    names = (
        "Downloads",
        "Téléchargements",
        "Telechargements",
        "Desktop",
        "Bureau",
        "Documents",
        "OneDrive",
    )
    found: list[Path] = []
    for name in names:
        folder = home / name
        if folder.is_dir():
            found.append(folder)
    try:
        for folder in home.glob("OneDrive*"):
            if folder.is_dir():
                found.append(folder)
    except OSError:
        pass
    return found


def default_scan_roots() -> list[Path]:
    """Fixed and removable Windows drives, user folders, and MAGIC_ISO_SCAN_ROOTS."""
    raw = (os.environ.get("MAGIC_ISO_SCAN_ROOTS") or "").strip()
    if raw:
        return [Path(part.strip().strip('"')) for part in raw.split(";") if part.strip()]
    if os.name != "nt":
        return []
    roots: list[Path] = []
    roots.extend(profile_media_dirs())
    for part in (os.environ.get("MAGIC_ISO_DIRS") or "").split(";"):
        part = part.strip().strip('"')
        if part:
            roots.append(Path(part))
    try:
        import ctypes

        mask = int(ctypes.windll.kernel32.GetLogicalDrives())
        for index in range(26):
            if not mask & (1 << index):
                continue
            letter = f"{chr(65 + index)}:\\"
            kind = int(ctypes.windll.kernel32.GetDriveTypeW(letter))
            if kind not in (2, 3):
                continue
            drive = Path(letter)
            for sub in ("ISO", "ISOs", "Images", "OS"):
                folder = drive / sub
                if folder.is_dir():
                    roots.append(folder)
            roots.append(drive)
    except Exception:
        pass
    return roots


def _identity_from_tree(path: Path) -> dict[str, Any]:
    root = path if path.is_dir() else path.parent.parent if path.name.lower().startswith("install.") else path.parent
    build = 0
    language = ""
    cversion = root / "sources" / "cversion.ini"
    lang = root / "sources" / "lang.ini"
    try:
        if cversion.is_file():
            build = parse_cversion(cversion.read_text(encoding="utf-8", errors="replace"))
        if lang.is_file():
            language = parse_lang_ini(lang.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass
    edition = ""
    name = path.name.lower()
    for label in ("enterprise", "education", "professional", "pro", "home", "core"):
        if label in name:
            edition = "Pro" if label in ("pro", "professional") else label.title()
            break
    arch = ""
    if "arm64" in name:
        arch = "arm64"
    elif "x86" in name and "x64" not in name:
        arch = "x86"
    elif "x64" in name or "amd64" in name:
        arch = "x64"
    family = ""
    if re.search(r"win(?:dows)?\s*11|win11", name):
        family = "11"
    elif re.search(r"win(?:dows)?\s*10|win10", name):
        family = "10"
    return {"build": build, "language": language, "edition": edition, "arch": arch, "win": family}


def describe_media(path: Path, *, reader: Callable[[Path], dict[str, Any]] | None = None) -> dict[str, Any]:
    if reader is not None:
        described = dict(reader(path))
        described.setdefault("path", str(path))
        return described
    info = _identity_from_tree(path)
    info["path"] = str(path)
    try:
        info["size"] = path.stat().st_size
        info["mtime"] = path.stat().st_mtime
    except OSError:
        info["size"] = 0
    lower = path.name.lower()
    if path.is_dir():
        info["kind"] = "dir"
    elif lower.endswith(".iso"):
        info["kind"] = "iso"
    elif lower.endswith(".esd"):
        info["kind"] = "esd"
    else:
        info["kind"] = "wim"
    info["hash"] = hash_verdict(path)
    return info


def setup_root_for(path: Path) -> Path | None:
    """Folder that contains setup.exe, if this path is already-extracted media."""
    if path.is_dir() and (path / "setup.exe").is_file():
        return path
    if path.name.lower() in ("install.wim", "install.esd"):
        root = path.parent.parent
        if (root / "setup.exe").is_file():
            return root
    return None


def _warn_hash(record: dict[str, Any]) -> None:
    from .logutil import log

    if record.get("hash") == "unknown":
        log(f"No official SHA-256 for {Path(str(record.get('path'))).name}; accepting the local file", "WARN")
    elif record.get("hash") == "official":
        log(f"Official SHA-256 matches {Path(str(record.get('path'))).name}", "OK")


def choose_local_iso(
    *,
    win: str,
    arch: str = "x64",
    language: str = "",
    edition: str = "",
    min_build: int = 0,
    roots: list[Path] | None = None,
    reader: Callable[[Path], dict[str, Any]] | None = None,
    budget_s: float = SCAN_BUDGET_S,
    min_bytes: int = MIN_MEDIA_BYTES,
) -> Path | None:
    """Prefer a manual path, then the cache, then a bounded scan. Never downloads."""
    from .logutil import log

    forced = override_iso()
    if forced is not None:
        described = describe_media(forced, reader=reader)
        described.setdefault("win", win)
        if described.get("hash") == "mismatch":
            log(f"Local ISO override hash does not match: {forced}", "WARN")
        else:
            best = pick_best([described], win=win, arch=arch, language=language, edition=edition, min_build=min_build)
            if best:
                _warn_hash(best)
                folder = setup_root_for(forced)
                log(f"Using local media from --iso: {forced}", "OK")
                return folder or forced
            log(f"Local ISO override is not a matching Windows {win} image: {forced}", "WARN")

    files = load_cache()
    if not files:
        scan_roots = list(roots) if roots is not None else default_scan_roots()
        if scan_roots:
            log(f"Scanning local disks for a Windows {win} ISO (budget {int(budget_s)}s, read-only)", "STEP")
            files = scan_tree(scan_roots, budget_s=budget_s, min_bytes=min_bytes)
            save_cache(files, complete=True)
    records: list[dict[str, Any]] = []
    for item in files:
        path = Path(str(item.get("path") or ""))
        if not path.is_file():
            continue
        described = describe_media(path, reader=reader)
        described.setdefault("kind", item.get("kind"))
        described.setdefault("size", item.get("size"))
        records.append(described)
    best = pick_best(records, win=win, arch=arch, language=language, edition=edition, min_build=min_build)
    if not best:
        return None
    _warn_hash(best)
    chosen = Path(str(best["path"]))
    folder = setup_root_for(chosen)
    log(f"Using local Windows media: {chosen}", "OK")
    return folder or chosen


def start_background_scan() -> None:
    """Start one read-only scan. A second call does nothing while the first is running."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return

        def _run() -> None:
            try:
                roots = default_scan_roots()
                if not roots:
                    return
                files = scan_tree(roots)
                save_cache(files, complete=True)
            except Exception:
                pass

        _thread = threading.Thread(target=_run, name="local-iso-scan", daemon=True)
        _thread.start()


def attach_media(path: Path) -> str:
    """Setup folder for extracted media, or a read-only mount for an ISO file."""
    folder = setup_root_for(Path(path))
    if folder is not None:
        return str(folder)
    from .virtdisk import mount_iso

    return mount_iso(path)
