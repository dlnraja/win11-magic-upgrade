"""Official Microsoft ISO download (Fido-compatible API) — pure urllib, no PowerShell."""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .logutil import STATE_DIR, log

ORG_ID = "y6jn8c31"
PROFILE_ID = "606624d44113"
INSTANCE_ID = "560dc9f3-1aa5-4a2f-b63c-9e18f8d0e175"

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


def download_file(url: str, dest: Path, log_every_mb: int = 64) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"Downloading -> {dest.name} (urllib, resumable)...", "STEP")
    existing = dest.stat().st_size if dest.exists() else 0
    headers = {}
    mode = "wb"
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
        mode = "ab"
        log(f"Resuming from {existing} bytes", "INFO")

    req = urllib.request.Request(url, headers=headers)
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
                if written - last_report >= log_every_mb * 1024 * 1024:
                    if total_i:
                        pct = 100.0 * written / total_i
                        log(f"Download {written/1e9:.2f}/{total_i/1e9:.2f} GB ({pct:.1f}%)")
                    else:
                        log(f"Download {written/1e9:.2f} GB")
                    last_report = written

    if dest.stat().st_size < 1_000_000_000:
        raise RuntimeError(f"ISO too small / incomplete: {dest}")
    log(f"Download complete: {dest} ({dest.stat().st_size/1e9:.2f} GB)", "OK")
    return dest


def get_iso(win: str, locale: str, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (STATE_DIR / "iso")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Reuse existing large ISO
    for p in sorted(out_dir.glob("*.iso"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.stat().st_size > 3_000_000_000:
            name = p.name.lower()
            if win == "11" and ("win11" in name or "windows11" in name or "22631" in name or "26100" in name or "26200" in name):
                log(f"Reusing ISO: {p}", "OK")
                return p
            if win == "10" and ("win10" in name or "windows10" in name or "22h2" in name or "19045" in name):
                log(f"Reusing ISO: {p}", "OK")
                return p

    url = resolve_iso_url(win=win, lang_hint=locale, arch="x64")
    m = re.search(r"/([^/?]+\.iso)", url, re.I)
    fname = m.group(1) if m else f"Windows{win}_{int(time.time())}.iso"
    return download_file(url, out_dir / fname)
