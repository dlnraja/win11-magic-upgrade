"""
Cloud false-positive declarations — VirusTotal + Kaspersky (autonomous).

Keys (optional but enable full API submit):
  Env:  MAGIC_VT_API_KEY, MAGIC_KASPERSKY_OPENTIP_KEY
  File: %LOCALAPPDATA%\\Win11MagicUpgrade\\av_keys.json
        { "virustotal": "...", "kaspersky_opentip": "..." }

Without keys: still builds submission pack, emails draft to newvirus@kaspersky.com
when possible, and opens VT/OpenTIP portals once per hash (MAGIC_AV_OPEN_BROWSER=0 to disable).
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log

APP_NAME = "Win11 Magic Upgrade"
FP_COMMENT = (
    "FALSE POSITIVE. Win11MagicUpgrade is a legitimate open-source Windows 10→11 "
    "migration helper. It downloads official Microsoft ISOs only, mounts them, and "
    "runs Setup. It contains NO PDF exploit, NO Trojan, NO ransomware, NO credential theft. "
    "Heuristic hits (e.g. Trojan.PDF) come from unsigned PyInstaller + UAC elevation. "
    "#false_positive #win11 #legitimate_software"
)

VT_API = "https://www.virustotal.com/api/v3"
KASP_SCAN = "https://opentip.kaspersky.com/api/v1/scan/file"
KASP_HASH = "https://opentip.kaspersky.com/api/v1/search/hash"
KASP_EMAIL = "newvirus@kaspersky.com"
KEYS_FILE = STATE_DIR / "av_keys.json"
STATE_FILE = STATE_DIR / "av_cloud_state.json"


def _ctx():
    return ssl.create_default_context()


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    data: bytes | None = None,
    timeout: int = 180,
) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, context=_ctx(), timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = getattr(resp, "status", 200) or 200
            try:
                return code, json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                return code, {"raw": raw}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return int(e.code), parsed
    except Exception as e:
        return 0, {"error": str(e)}


def load_api_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    if KEYS_FILE.exists():
        try:
            data = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, str) and v.strip() and not v.strip().startswith("<"):
                        keys[k.strip().lower()] = v.strip()
        except Exception as e:
            log(f"av_keys.json read: {e}", "WARN")
    vt = os.environ.get("MAGIC_VT_API_KEY", "").strip() or os.environ.get("VIRUSTOTAL_API_KEY", "").strip()
    kp = (
        os.environ.get("MAGIC_KASPERSKY_OPENTIP_KEY", "").strip()
        or os.environ.get("KASPERSKY_OPENTIP_KEY", "").strip()
    )
    if vt:
        keys["virustotal"] = vt
    if kp:
        keys["kaspersky_opentip"] = kp
    return keys


def ensure_keys_template() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if KEYS_FILE.exists():
        return
    KEYS_FILE.write_text(
        json.dumps(
            {
                "virustotal": "<paste free API key from https://www.virustotal.com/gui/my-apikey>",
                "kaspersky_opentip": "<paste OpenTIP token from https://opentip.kaspersky.com/>",
                "_comment": "After filling keys, One-Click submits FP declarations autonomously.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    log(f"Created API key template: {KEYS_FILE}", "INFO")


def target_binary() -> Path | None:
    if getattr(__import__("sys"), "frozen", False):
        return Path(__import__("sys").executable).resolve()
    # Prefer built portable EXE
    root = Path(__file__).resolve().parents[2]
    for cand in (
        root / "dist" / "Win11MagicUpgrade-Portable" / "Win11MagicUpgrade.exe",
        root / "dist" / "Win11MagicUpgrade.exe",
    ):
        if cand.exists():
            return cand
    return None


def file_hashes(path: Path) -> dict[str, str]:
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
    return {"md5": md5.hexdigest(), "sha1": sha1.hexdigest(), "sha256": sha256.hexdigest()}


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _already_done(sha256: str, vendor: str) -> bool:
    st = _load_state()
    entry = (st.get("submissions") or {}).get(sha256, {})
    return bool(entry.get(vendor) in ("ok", "submitted", "voted"))


def _mark_done(sha256: str, vendor: str, status: str, extra: dict | None = None) -> None:
    st = _load_state()
    subs = st.setdefault("submissions", {})
    entry = subs.setdefault(sha256, {})
    entry[vendor] = status
    entry[f"{vendor}_at"] = datetime.now().isoformat(timespec="seconds")
    if extra:
        entry.update(extra)
    _save_state(st)


def build_fp_package(path: Path, hashes: dict[str, str]) -> Path:
    """ZIP with password 'infected' (Kaspersky convention) + declaration text."""
    out_dir = STATE_DIR / "fp_submissions"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    decl = out_dir / f"DECLARATION_{stamp}.txt"
    decl.write_text(
        "\n".join(
            [
                APP_NAME,
                "=" * 60,
                FP_COMMENT,
                "",
                f"File: {path.name}",
                f"Path: {path}",
                f"Size: {path.stat().st_size} bytes",
                f"MD5:    {hashes['md5']}",
                f"SHA1:   {hashes['sha1']}",
                f"SHA256: {hashes['sha256']}",
                "",
                f"VirusTotal: https://www.virustotal.com/gui/file/{hashes['sha256']}",
                f"OpenTIP:    https://opentip.kaspersky.com/{hashes['sha256']}/results",
                f"Kaspersky FP email: {KASP_EMAIL}",
                "",
                "Request: please mark as CLEAN / Trusted Application / remove Trojan.PDF heuristic.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    zpath = out_dir / f"Win11MagicUpgrade_FP_{hashes['sha256'][:12]}.zip"
    # Ship clear ZIP + note: Kaspersky support often wants password "infected" when re-packing
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, arcname=path.name)
        zf.write(decl, arcname=decl.name)
        zf.writestr(
            "PASSWORD.txt",
            "If re-packing for Kaspersky support, use password: infected\n",
        )
    meta = out_dir / f"SUBMIT_{hashes['sha256'][:12]}.json"
    meta.write_text(
        json.dumps(
            {
                "file": str(path),
                "hashes": hashes,
                "declaration": str(decl),
                "zip": str(zpath),
                "kaspersky_email": KASP_EMAIL,
                "vt_url": f"https://www.virustotal.com/gui/file/{hashes['sha256']}",
                "opentip_url": f"https://opentip.kaspersky.com/{hashes['sha256']}/results",
                "created": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"FP package ready: {zpath}", "OK")
    log(f"Declaration text: {decl}", "OK")
    return zpath


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----MagicBoundary{int(time.time()*1000)}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    for name, (fname, content, ctype) in files.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'.encode()
        )
        body.extend(f"Content-Type: {ctype}\r\n\r\n".encode())
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def declare_virustotal(path: Path, hashes: dict[str, str], keys: dict[str, str]) -> str:
    """Upload + harmless vote + FP comment. Returns status string."""
    sha = hashes["sha256"]
    if _already_done(sha, "virustotal"):
        log("VirusTotal: already declared for this build (skip)", "OK")
        return "skip"

    key = keys.get("virustotal", "")
    vt_url = f"https://www.virustotal.com/gui/file/{sha}"

    if not key:
        log("VirusTotal: no API key — package + portal (set MAGIC_VT_API_KEY or av_keys.json)", "WARN")
        _mark_done(sha, "virustotal", "pending_key", {"url": vt_url})
        _maybe_open_browser(vt_url)
        return "pending_key"

    headers = {"x-apikey": key, "accept": "application/json"}
    size = path.stat().st_size
    log(f"VirusTotal: uploading {path.name} ({size/1e6:.1f} MB)…", "STEP")

    upload_url = f"{VT_API}/files"
    if size > 32 * 1024 * 1024:
        code, data = _http_json("GET", f"{VT_API}/files/upload_url", headers=headers)
        if code == 200 and isinstance(data, dict) and data.get("data"):
            upload_url = str(data["data"])
        else:
            log(f"VirusTotal upload_url failed: {code} {data}", "WARN")

    raw = path.read_bytes()
    body, ctype = _multipart({}, {"file": (path.name, raw, "application/octet-stream")})
    code, data = _http_json(
        "POST",
        upload_url,
        headers={**headers, "content-type": ctype},
        data=body,
        timeout=600,
    )
    analysis_id = None
    if code in (200, 201) and isinstance(data, dict):
        analysis_id = (data.get("data") or {}).get("id")
        log(f"VirusTotal upload OK (analysis={analysis_id})", "OK")
    else:
        # File may already exist — continue with hash
        log(f"VirusTotal upload response {code}: {str(data)[:200]}", "WARN")

    # Vote harmless
    vote_body = json.dumps(
        {"data": {"type": "vote", "attributes": {"verdict": "harmless"}}}
    ).encode()
    code, data = _http_json(
        "POST",
        f"{VT_API}/files/{sha}/votes",
        headers={**headers, "content-type": "application/json"},
        data=vote_body,
    )
    if code in (200, 201):
        log("VirusTotal: voted HARLESS (false positive)", "OK")
    else:
        log(f"VirusTotal vote: {code} {str(data)[:160]}", "WARN")

    # Comment
    comment_body = json.dumps(
        {"data": {"type": "comment", "attributes": {"text": FP_COMMENT}}}
    ).encode()
    code, data = _http_json(
        "POST",
        f"{VT_API}/files/{sha}/comments",
        headers={**headers, "content-type": "application/json"},
        data=comment_body,
    )
    if code in (200, 201):
        log("VirusTotal: FP comment posted", "OK")
        _mark_done(sha, "virustotal", "ok", {"url": vt_url, "analysis": analysis_id})
        return "ok"

    log(f"VirusTotal comment: {code} {str(data)[:160]}", "WARN")
    _mark_done(sha, "virustotal", "partial", {"url": vt_url, "analysis": analysis_id})
    return "partial"


def declare_kaspersky_cloud(path: Path, hashes: dict[str, str], keys: dict[str, str]) -> str:
    """OpenTIP upload + local email draft. Returns status string."""
    sha = hashes["sha256"]
    if _already_done(sha, "kaspersky"):
        log("Kaspersky cloud: already declared for this build (skip)", "OK")
        return "skip"

    tip_url = f"https://opentip.kaspersky.com/{sha}/results"
    key = keys.get("kaspersky_opentip", "")

    if key:
        log("Kaspersky OpenTIP: uploading sample for analysis…", "STEP")
        q = urllib.parse.urlencode({"filename": path.name})
        url = f"{KASP_SCAN}?{q}"
        code, data = _http_json(
            "POST",
            url,
            headers={
                "x-api-key": key,
                "Content-Type": "application/octet-stream",
            },
            data=path.read_bytes(),
            timeout=600,
        )
        if code in (200, 201):
            status = ""
            if isinstance(data, dict):
                status = str(data.get("FileStatus") or data.get("Status") or "")
            log(f"Kaspersky OpenTIP upload OK ({status or 'queued'})", "OK")
            # Lookup hash report
            q2 = urllib.parse.urlencode({"request": sha})
            c2, d2 = _http_json(
                "GET",
                f"{KASP_HASH}?{q2}",
                headers={"x-api-key": key, "accept": "application/json"},
            )
            if c2 == 200:
                log(f"Kaspersky hash lookup: {str(d2)[:180]}", "INFO")
            _mark_done(sha, "kaspersky", "ok", {"url": tip_url, "response": status})
            _write_kaspersky_email_draft(path, hashes)
            _maybe_open_browser(tip_url)
            return "ok"
        log(f"Kaspersky OpenTIP upload failed {code}: {str(data)[:200]}", "WARN")

    log(
        "Kaspersky: no OpenTIP key or upload failed — writing email draft + opening portal",
        "WARN",
    )
    _write_kaspersky_email_draft(path, hashes)
    _maybe_open_browser(tip_url)
    _mark_done(sha, "kaspersky", "pending_key" if not key else "partial", {"url": tip_url})
    return "pending_key" if not key else "partial"


def _write_kaspersky_email_draft(path: Path, hashes: dict[str, str]) -> Path:
    out = STATE_DIR / "fp_submissions"
    out.mkdir(parents=True, exist_ok=True)
    eml_body = "\n".join(
        [
            f"To: {KASP_EMAIL}",
            "Subject: False positive — Win11MagicUpgrade.exe (NOT Trojan.PDF)",
            "",
            FP_COMMENT,
            "",
            f"Filename: {path.name}",
            f"SHA256: {hashes['sha256']}",
            f"SHA1:   {hashes['sha1']}",
            f"MD5:    {hashes['md5']}",
            f"OpenTIP: https://opentip.kaspersky.com/{hashes['sha256']}/results",
            f"VirusTotal: https://www.virustotal.com/gui/file/{hashes['sha256']}",
            "",
            "Please reclassify as clean / trusted. Sample available in attached ZIP "
            "(re-pack with password 'infected' if required).",
            "",
        ]
    )
    draft = out / f"EMAIL_kaspersky_{hashes['sha256'][:12]}.txt"
    draft.write_text(eml_body, encoding="utf-8")
    log(f"Kaspersky email draft: {draft} (send to {KASP_EMAIL})", "OK")

    # Best-effort: open default mail client with prefilled body (no attachment via mailto)
    if os.environ.get("MAGIC_AV_OPEN_MAIL", "1").strip() not in ("0", "false", "no"):
        try:
            subject = urllib.parse.quote("False positive — Win11MagicUpgrade.exe")
            body = urllib.parse.quote(eml_body.split("\n\n", 1)[-1][:1800])
            webbrowser.open(f"mailto:{KASP_EMAIL}?subject={subject}&body={body}")
            log("Opened mail client for Kaspersky FP email", "OK")
        except Exception as e:
            log(f"mailto open: {e}", "WARN")
    return draft


def _maybe_open_browser(url: str) -> None:
    if os.environ.get("MAGIC_AV_OPEN_BROWSER", "1").strip() in ("0", "false", "no"):
        return
    st = _load_state()
    opened = st.setdefault("browsers_opened", {})
    if opened.get(url):
        return
    try:
        webbrowser.open(url)
        opened[url] = datetime.now().isoformat(timespec="seconds")
        _save_state(st)
        log(f"Opened portal: {url}", "OK")
    except Exception as e:
        log(f"browser open: {e}", "WARN")


def declare_virustotal_and_kaspersky(path: Path | None = None) -> dict[str, str]:
    """
    Cloud FP declarations for VirusTotal + Kaspersky.
    Intended for CI/CD Release (build/ci_declare_av.ps1), not One-Click.
    Skips duplicate SHA256 submissions.
    """
    ensure_keys_template()
    path = path or target_binary()
    result = {"virustotal": "skip", "kaspersky": "skip", "package": ""}
    if path is None or not path.exists():
        log("Cloud FP: no EXE found to declare", "WARN")
        return result

    log("=" * 60, "STEP")
    log(f"CLOUD FP — VirusTotal + Kaspersky for {path.name}", "STEP")
    hashes = file_hashes(path)
    log(f"SHA256: {hashes['sha256']}", "INFO")
    log(f"SHA1:   {hashes['sha1']}", "INFO")
    log(f"MD5:    {hashes['md5']}", "INFO")

    zpath = build_fp_package(path, hashes)
    result["package"] = str(zpath)

    keys = load_api_keys()
    if not keys.get("virustotal"):
        log(f"Tip: add VirusTotal key → {KEYS_FILE} or MAGIC_VT_API_KEY", "INFO")
    if not keys.get("kaspersky_opentip"):
        log(f"Tip: add Kaspersky OpenTIP key → {KEYS_FILE} or MAGIC_KASPERSKY_OPENTIP_KEY", "INFO")

    try:
        result["virustotal"] = declare_virustotal(path, hashes, keys)
    except Exception as e:
        log(f"VirusTotal declare error: {e}", "WARN")
        result["virustotal"] = "error"

    try:
        result["kaspersky"] = declare_kaspersky_cloud(path, hashes, keys)
    except Exception as e:
        log(f"Kaspersky declare error: {e}", "WARN")
        result["kaspersky"] = "error"

    summary = STATE_DIR / "fp_submissions" / "LAST_CLOUD_DECLARE.json"
    summary.write_text(
        json.dumps({"path": str(path), "hashes": hashes, "result": result}, indent=2),
        encoding="utf-8",
    )
    log(f"Cloud FP done: VT={result['virustotal']} Kasp={result['kaspersky']}", "OK")
    return result
