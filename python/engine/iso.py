"""Official Microsoft ISO download (Fido-compatible API) - pure urllib, no PowerShell."""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .logutil import STATE_DIR, log
from .progress import format_bytes, format_eta, format_speed, report_progress

ORG_ID = "y6jn8c31"
PROFILE_ID = "606624d44113"
INSTANCE_ID = "560dc9f3-1aa5-4a2f-b63c-9e18f8d0e175"

# Minimum size to treat a file as a real Windows retail ISO (incomplete downloads are smaller)
MIN_ISO_BYTES = 2_000_000_000

# Fido product edition IDs (x64-oriented). Updated from Fido v1.70 table.
PRODUCTS = {
    "11": {
        "page": "windows11",
        "edition_ids": [3321, 3324],  # Home/Pro/Edu
        "label": "Windows 11",
    },
    "10": {
        "page": "Windows10ISO",
        "edition_ids": [2618],  # Home/Pro/Edu 22H2
        "label": "Windows 10 22H2",
    },
}

LANG_MAP = {
    "fr-FR": "French",
    "fr-CA": "French",
    "en-US": "English",
    "en-GB": "English",
    "de-DE": "German",
    "es-ES": "Spanish",
    "it-IT": "Italian",
    "pt-BR": "Brazilian Portuguese",
    "nl-NL": "Dutch",
    "pl-PL": "Polish",
    "ru-RU": "Russian",
    "ja-JP": "Japanese",
    "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
    "ko-KR": "Korean",
    "ar-SA": "Arabic",
    "tr-TR": "Turkish",
}


def _ctx():
    # Old Win10 may need TLS1.2 explicitly; Python 3.12 enables it by default
    return ssl.create_default_context()


def _http_json(url: str, headers: dict | None = None, timeout: int = 45) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, context=_ctx(), timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def _http_text(url: str, headers: dict | None = None, timeout: int = 45) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, context=_ctx(), timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _http_void(url: str, timeout: int = 30) -> None:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, context=_ctx(), timeout=timeout) as resp:
            resp.read(1)
    except Exception:
        # Many of these return empty / redirect; ignore
        pass


def _whitelist_session(session_id: str) -> None:
    _http_void(f"https://vlscppe.microsoft.com/tags?org_id={ORG_ID}&session_id={session_id}")
    mdt = _http_text(
        f"https://ov-df.microsoft.com/mdt.js?instanceId={INSTANCE_ID}&PageId=si&session_id={session_id}"
    )
    w = None
    rticks = None
    m = re.search(r"[?&]w=([A-F0-9]+)", mdt)
    if m:
        w = m.group(1)
    m = re.search(r"rticks\s*=\s*\"?\+?(\d+)", mdt)
    if m:
        rticks = m.group(1)
    if w and rticks:
        now = int(time.time() * 1000)
        _http_void(
            "https://ov-df.microsoft.com/"
            f"?session_id={session_id}&CustomerId={INSTANCE_ID}&PageId=si"
            f"&w={w}&mdt={now}&rticks={rticks}"
        )


def resolve_iso_url(win: str = "11", lang_hint: str = "en-US", arch: str = "x64") -> str:
    """Return a temporary Microsoft CDN URL for the official retail ISO."""
    meta = PRODUCTS[win]
    session_id = str(uuid.uuid4())
    log(f"Requesting official {meta['label']} ISO URL from Microsoft (no Fido/PS)...", "STEP")
    _whitelist_session(session_id)

    skus: list[dict] = []
    last_err = None
    for eid in meta["edition_ids"]:
        url = (
            "https://www.microsoft.com/software-download-connector/api/getskuinformationbyproductedition"
            f"?profile={PROFILE_ID}&productEditionId={eid}&SKU=undefined"
            f"&friendlyFileName=undefined&Locale=en-US&sessionID={session_id}"
        )
        for attempt in range(3):
            try:
                data = _http_json(url)
                if data.get("Errors"):
                    last_err = data["Errors"]
                    time.sleep(2)
                    continue
                skus = data.get("Skus") or []
                if skus:
                    break
            except Exception as e:
                last_err = e
                time.sleep(2)
        if skus:
            break

    if not skus:
        raise RuntimeError(f"Could not get SKUs from Microsoft: {last_err}")

    want = LANG_MAP.get(lang_hint, "English")
    sku = None
    for s in skus:
        loc = (s.get("LocalizedLanguage") or s.get("Language") or "")
        if want.lower() in loc.lower() or lang_hint.lower() in (s.get("Language") or "").lower():
            sku = s
            break
    if sku is None:
        # Prefer English
        for s in skus:
            loc = (s.get("LocalizedLanguage") or "")
            if "English" in loc:
                sku = s
                break
    if sku is None:
        sku = skus[0]

    log(f"Selected language SKU: {sku.get('LocalizedLanguage')} ({sku.get('Id')})", "OK")

    link_url = (
        "https://www.microsoft.com/software-download-connector/api/GetProductDownloadLinksBySku"
        f"?profile={PROFILE_ID}&productEditionId=undefined&SKU={sku['Id']}"
        f"&friendlyFileName=undefined&Locale=en-US&sessionID={session_id}"
    )
    ref = f"https://www.microsoft.com/software-download/{meta['page']}"
    data = _http_json(link_url, headers={"Referer": ref})
    if data.get("Errors"):
        raise RuntimeError(f"Microsoft link error: {data['Errors']}")
    options = data.get("ProductDownloadOptions") or []
    if not options:
        raise RuntimeError("No ProductDownloadOptions from Microsoft")

    # DownloadType: 1=x86, 2=x64 typically (Fido Get-Arch-From-Type)
    chosen = None
    for opt in options:
        dtype = opt.get("DownloadType")
        uri = opt.get("Uri") or ""
        if arch == "x64" and (dtype == 2 or "x64" in uri.lower() or "X64" in uri):
            chosen = uri
            break
        if arch != "x64" and dtype == 1:
            chosen = uri
            break
    if not chosen:
        chosen = options[0].get("Uri")
    if not chosen:
        raise RuntimeError("Empty ISO URI")
    log("Microsoft CDN URL obtained.", "OK")
    return chosen


def download_file(url: str, dest: Path, log_every_mb: int = 32) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"Downloading -> {dest.name} (urllib, resumable)...", "STEP")
    report_progress(
        phase=f"Download {dest.name}",
        percent=0.0,
        detail="Connecting to Microsoft CDN…",
        indeterminate=True,
    )
    existing = dest.stat().st_size if dest.exists() else 0
    headers = {}
    mode = "wb"
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
        mode = "ab"
        log(f"Resuming from {existing} bytes", "INFO")

    req = urllib.request.Request(url, headers=headers)
    started = time.time()
    last_ui = 0.0
    with urllib.request.urlopen(req, context=_ctx(), timeout=120) as resp:
        total = resp.headers.get("Content-Length")
        total_i = int(total) + existing if total and existing else (int(total) if total else None)
        written = existing
        last_report = existing
        with dest.open(mode) as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                now = time.time()
                elapsed = max(now - started, 0.001)
                downloaded_session = max(written - existing, 0)
                speed = downloaded_session / elapsed
                eta = None
                pct = None
                if total_i and total_i > 0:
                    pct = 100.0 * written / total_i
                    remain = max(total_i - written, 0)
                    if speed > 0:
                        eta = remain / speed
                # UI throttle ~0.4s
                if now - last_ui >= 0.4:
                    detail = (
                        f"{format_bytes(written)}"
                        + (f" / {format_bytes(total_i)}" if total_i else "")
                        + f"  ·  {format_speed(speed)}"
                        + f"  ·  ETA {format_eta(eta)}"
                    )
                    report_progress(
                        phase=f"Download {dest.name}",
                        percent=pct,
                        detail=detail,
                        bytes_done=written,
                        bytes_total=total_i or 0,
                        speed_bps=speed,
                        eta_seconds=eta,
                        indeterminate=total_i is None,
                    )
                    last_ui = now
                if written - last_report >= log_every_mb * 1024 * 1024:
                    if total_i:
                        log(
                            f"Download {written/1e9:.2f}/{total_i/1e9:.2f} GB "
                            f"({100.0 * written / total_i:.1f}%) · {format_speed(speed)} · ETA {format_eta(eta)}"
                        )
                    else:
                        log(f"Download {written/1e9:.2f} GB · {format_speed(speed)}")
                    last_report = written

    if dest.stat().st_size < 1_000_000_000:
        raise RuntimeError(f"ISO too small / incomplete: {dest}")
    log(f"Download complete: {dest} ({dest.stat().st_size/1e9:.2f} GB)", "OK")
    report_progress(
        phase=f"Download {dest.name}",
        percent=100.0,
        detail=f"Complete — {format_bytes(dest.stat().st_size)}",
        bytes_done=dest.stat().st_size,
        bytes_total=dest.stat().st_size,
        speed_bps=0.0,
        eta_seconds=0.0,
    )
    return dest


def _iso_name_matches(win: str, arch: str, name: str) -> bool:
    n = name.lower()
    arch_ok = (arch == "x64" and ("x64" in n or "64bit" in n or "x86" not in n)) or (
        arch == "x86" and ("x86" in n or "32bit" in n)
    )
    if win == "11":
        if arch != "x64":
            return False
        return any(
            k in n
            for k in (
                "win11",
                "windows11",
                "windows_11",
                "win_11",
                "26100",
                "26200",
                "22631",
                "22621",
            )
        ) and ("x86" not in n or "x64" in n)
    if win == "10":
        return arch_ok and any(
            k in n
            for k in (
                "win10",
                "windows10",
                "windows_10",
                "win_10",
                "22h2",
                "19045",
                "19044",
            )
        )
    return False


def _user_profile_dirs() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    names = (
        "Downloads",
        "Téléchargements",
        "Telechargements",
        "Desktop",
        "Bureau",
        "Documents",
        "Videos",
        "Vidéos",
    )
    out: list[Path] = []
    for name in names:
        p = home / name
        if p.is_dir():
            out.append(p)
    # Localized Downloads via Explorer Shell Folders
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as key:
            for value_name in ("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"):
                try:
                    val, _ = winreg.QueryValueEx(key, value_name)
                    if val and Path(val).is_dir():
                        out.append(Path(val))
                except OSError:
                    pass
    except Exception:
        pass
    # de-dupe
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        k = str(p.resolve()).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def iso_search_roots(extra: Path | None = None) -> list[Path]:
    """Folders scanned for existing Windows ISOs (no full-disk crawl)."""
    roots: list[Path] = []
    if extra:
        roots.append(extra)
    roots.append(STATE_DIR / "iso")
    roots.extend(_user_profile_dirs())

    # Portable / app folders
    try:
        import sys

        if getattr(sys, "frozen", False):
            roots.append(Path(sys.executable).resolve().parent)
            roots.append(Path(sys.executable).resolve().parent / "iso")
        else:
            root = Path(__file__).resolve().parents[2]
            roots.extend([root, root / "iso", root / "dist", root / "media"])
    except Exception:
        pass

    # Common drop locations on fixed drives
    for letter in "CDEFGHI":
        drive = Path(f"{letter}:/")
        if not drive.exists():
            continue
        for sub in ("ISO", "ISOs", "WindowsISO", "WinISO", "ESD", "Images", "OS"):
            p = drive / sub
            if p.is_dir():
                roots.append(p)

    # Extra dirs from env (semicolon-separated)
    for part in (os.environ.get("MAGIC_ISO_DIRS") or "").split(";"):
        part = part.strip().strip('"')
        if part and Path(part).is_dir():
            roots.append(Path(part))

    seen: set[str] = set()
    uniq: list[Path] = []
    for p in roots:
        try:
            if not p.exists():
                continue
            k = str(p.resolve()).lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(p)
        except Exception:
            continue
    return uniq


def _iter_isos_in_dir(folder: Path, *, recursive: bool = False) -> list[Path]:
    found: list[Path] = []
    try:
        if recursive:
            # Cap depth: only one level of subdirs for Downloads (speed)
            for p in folder.glob("*.iso"):
                found.append(p)
            for sub in folder.iterdir():
                if sub.is_dir():
                    try:
                        found.extend(sub.glob("*.iso"))
                    except Exception:
                        pass
        else:
            found.extend(folder.glob("*.iso"))
    except Exception:
        pass
    return found


def find_local_iso(win: str, arch: str = "x64", out_dir: Path | None = None) -> Path | None:
    """
    Auto-detect an existing Windows ISO on the PC:
    cache, Downloads/Téléchargements, Desktop, Documents, common ISO folders, MAGIC_ISO_DIRS.
    """
    arch = "x86" if arch.lower() in ("x86", "x32", "32", "i386") else "x64"
    candidates: list[Path] = []
    roots = iso_search_roots(out_dir)
    log(f"Scanning {len(roots)} location(s) for Windows {win} ISO…", "STEP")
    for root in roots:
        # Downloads / Desktop: also scan one subfolder level
        deep = root.name.lower() in (
            "downloads",
            "téléchargements",
            "telechargements",
            "desktop",
            "bureau",
            "documents",
            "iso",
            "isos",
        )
        for p in _iter_isos_in_dir(root, recursive=deep):
            try:
                if p.stat().st_size < MIN_ISO_BYTES:
                    continue
                if _iso_name_matches(win, arch, p.name):
                    candidates.append(p)
            except OSError:
                continue

    if not candidates:
        log(f"No local Windows {win} ISO found in Downloads / cache / common folders", "INFO")
        return None

    # Prefer newest (mtime), then largest
    candidates.sort(key=lambda x: (x.stat().st_mtime, x.stat().st_size), reverse=True)
    best = candidates[0]
    log(f"Auto-detected local ISO: {best} ({best.stat().st_size/1e9:.2f} GB)", "OK")
    if len(candidates) > 1:
        log(f"  ({len(candidates) - 1} other matching ISO(s) ignored)", "INFO")
    return best


def get_iso(
    win: str,
    locale: str,
    out_dir: Path | None = None,
    arch: str = "x64",
) -> Path:
    out_dir = out_dir or (STATE_DIR / "iso")
    out_dir.mkdir(parents=True, exist_ok=True)
    arch = "x86" if arch.lower() in ("x86", "x32", "32", "i386") else "x64"
    label = f"Windows {win} ({arch})"
    report_progress(
        phase=f"ISO {label}",
        percent=None,
        detail="Searching Downloads / cache / PC for existing ISO…",
        indeterminate=True,
    )

    # 1) Auto-detect on PC (Downloads, cache, common folders)
    local = find_local_iso(win, arch=arch, out_dir=out_dir)
    if local is not None:
        report_progress(
            phase=f"ISO {label}",
            percent=100.0,
            detail=f"Reused local — {local.name}",
        )
        return local

    if win == "11" and arch != "x64":
        raise RuntimeError("Windows 11 ISO is 64-bit only")

    report_progress(
        phase=f"ISO {label}",
        percent=None,
        detail="No local ISO — resolving official Microsoft CDN URL…",
        indeterminate=True,
    )
    url = resolve_iso_url(win=win, lang_hint=locale, arch=arch)
    m = re.search(r"/([^/?]+\.iso)", url, re.I)
    fname = m.group(1) if m else f"Windows{win}_{arch}_{int(time.time())}.iso"
    return download_file(url, out_dir / fname)
