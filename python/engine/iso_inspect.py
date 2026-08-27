"""
ISO identity inspection — Flyby11-inspired + real winver + MD5/SHA256.

Flyby11 IsoHandler pattern:
  mount → require sources\\setupprep.exe → classify media → run Setup

We go further:
  - Read sources\\cversion.ini (MinClient) for true build / winver
  - PE ProductVersion of setupprep.exe / setup.exe as fallback
  - MD5 + SHA256 of the .iso file (cached by path/size/mtime)
  - Reject wrong family (Win10 ISO when Win11 requested, and vice versa)
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log
from .progress import format_bytes, report_progress

CATALOG_FILE = STATE_DIR / "iso_catalog.json"

# Windows 11 starts at build 22000 (21H2)
WIN11_MIN_BUILD = 22000
WIN10_MIN_BUILD = 10240


@dataclass
class IsoInfo:
    path: str
    size: int
    mtime: float
    md5: str = ""
    sha256: str = ""
    min_client: str = ""
    build: int = 0
    ubr: int = 0
    win_family: str = ""  # "10" | "11" | ""
    display_version: str = ""
    has_setup: bool = False
    has_setupprep: bool = False
    setup_pe_version: str = ""
    mount_root: str = ""
    verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _catalog_load() -> dict:
    if CATALOG_FILE.exists():
        try:
            return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _catalog_save(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _catalog_key(path: Path, size: int, mtime: float) -> str:
    return f"{path.resolve()}|{size}|{int(mtime)}"


def catalog_lookup(path: Path) -> IsoInfo | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = _catalog_key(path, st.st_size, st.st_mtime)
    raw = _catalog_load().get(key)
    if not isinstance(raw, dict):
        return None
    try:
        return IsoInfo(**{k: raw[k] for k in IsoInfo.__dataclass_fields__ if k in raw})
    except Exception:
        return None


def catalog_store(info: IsoInfo) -> None:
    data = _catalog_load()
    key = _catalog_key(Path(info.path), info.size, info.mtime)
    data[key] = info.as_dict()
    # Also index by hashes for dedupe
    if info.sha256:
        data[f"sha256:{info.sha256}"] = key
    if info.md5:
        data[f"md5:{info.md5}"] = key
    _catalog_save(data)


def hash_iso_file(path: Path, *, want_md5: bool = True, want_sha256: bool = True) -> tuple[str, str]:
    """Stream MD5 + SHA256 with progress (large ISOs)."""
    md5 = hashlib.md5() if want_md5 else None
    sha = hashlib.sha256() if want_sha256 else None
    total = path.stat().st_size
    done = 0
    started = time.time()
    last_ui = 0.0
    report_progress(
        phase=f"Hash {path.name}",
        percent=0.0,
        detail="Computing MD5 / SHA256…",
    )
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024 * 4)
            if not chunk:
                break
            if md5:
                md5.update(chunk)
            if sha:
                sha.update(chunk)
            done += len(chunk)
            now = time.time()
            if now - last_ui >= 0.4 and total > 0:
                pct = 100.0 * done / total
                speed = done / max(now - started, 0.001)
                report_progress(
                    phase=f"Hash {path.name}",
                    percent=pct,
                    detail=f"{format_bytes(done)} / {format_bytes(total)} · {format_bytes(speed)}/s",
                    bytes_done=done,
                    bytes_total=total,
                    speed_bps=speed,
                )
                last_ui = now
    md5_hex = md5.hexdigest() if md5 else ""
    sha_hex = sha.hexdigest() if sha else ""
    log(f"ISO MD5:    {md5_hex}", "OK")
    log(f"ISO SHA256: {sha_hex}", "OK")
    report_progress(phase=f"Hash {path.name}", percent=100.0, detail="Hash complete")
    return md5_hex, sha_hex


def parse_cversion_ini(text: str) -> tuple[str, int, int]:
    """
    Parse sources\\cversion.ini HostBuild.MinClient → (raw, build, ubr).
    Example: MinClient=10.0.26100.1742
    """
    min_client = ""
    m = re.search(r"^\s*MinClient\s*=\s*([0-9.]+)", text, re.I | re.M)
    if m:
        min_client = m.group(1).strip()
    build = 0
    ubr = 0
    parts = min_client.split(".")
    if len(parts) >= 3:
        try:
            build = int(parts[2])
        except ValueError:
            build = 0
    if len(parts) >= 4:
        try:
            ubr = int(parts[3])
        except ValueError:
            ubr = 0
    return min_client, build, ubr


def pe_product_version(path: Path) -> str:
    """Read PE ProductVersion via Win32 version APIs (no pywin32)."""
    try:
        version = __import__("ctypes").windll.version
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buf = (__import__("ctypes").c_ubyte * size)()
        if not version.GetFileVersionInfoW(str(path), 0, size, buf):
            return ""
        # Query \\StringFileInfo\\%04x%04x\\ProductVersion via translation
        tlen = __import__("ctypes").c_uint()
        tbuf = __import__("ctypes").c_void_p()
        if not version.VerQueryValueW(buf, r"\VarFileInfo\Translation", __import__("ctypes").byref(tbuf), __import__("ctypes").byref(tlen)):
            # Fallback: fixed file info
            return _pe_fixed_file_version(buf)
        # First LANG/CODEPAGE
        raw = __import__("ctypes").string_at(tbuf.value, 4)
        lang, codepage = struct.unpack("<HH", raw)
        query = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\ProductVersion"
        vbuf = __import__("ctypes").c_void_p()
        vlen = __import__("ctypes").c_uint()
        if version.VerQueryValueW(buf, query, __import__("ctypes").byref(vbuf), __import__("ctypes").byref(vlen)):
            return __import__("ctypes").wstring_at(vbuf.value) or ""
        return _pe_fixed_file_version(buf)
    except Exception:
        return ""


def _pe_fixed_file_version(buf) -> str:
    try:
        version = __import__("ctypes").windll.version
        vbuf = __import__("ctypes").c_void_p()
        vlen = __import__("ctypes").c_uint()
        if not version.VerQueryValueW(buf, r"\\", __import__("ctypes").byref(vbuf), __import__("ctypes").byref(vlen)):
            return ""
        # VS_FIXEDFILEINFO
        class VS_FIXEDFILEINFO(__import__("ctypes").Structure):
            _fields_ = [
                ("dwSignature", __import__("ctypes").c_uint32),
                ("dwStrucVersion", __import__("ctypes").c_uint32),
                ("dwFileVersionMS", __import__("ctypes").c_uint32),
                ("dwFileVersionLS", __import__("ctypes").c_uint32),
                ("dwProductVersionMS", __import__("ctypes").c_uint32),
                ("dwProductVersionLS", __import__("ctypes").c_uint32),
            ]

        info = VS_FIXEDFILEINFO.from_address(vbuf.value)
        maj = info.dwProductVersionMS >> 16
        min_ = info.dwProductVersionMS & 0xFFFF
        build = info.dwProductVersionLS >> 16
        rev = info.dwProductVersionLS & 0xFFFF
        return f"{maj}.{min_}.{build}.{rev}"
    except Exception:
        return ""


def build_to_family(build: int) -> str:
    if build >= WIN11_MIN_BUILD:
        return "11"
    if build >= WIN10_MIN_BUILD:
        return "10"
    return ""


def build_to_display(build: int) -> str:
    # Common marketing labels
    table = {
        26100: "24H2/25H2",
        26200: "25H2",
        22631: "23H2",
        22621: "22H2",
        22000: "21H2",
        19045: "22H2",
        19044: "21H2",
        19043: "21H1",
        19042: "20H2",
        19041: "2004",
    }
    if build in table:
        return table[build]
    if build >= WIN11_MIN_BUILD:
        return f"Win11 build {build}"
    if build >= WIN10_MIN_BUILD:
        return f"Win10 build {build}"
    return f"build {build}" if build else ""


def inspect_mounted_root(root: str | Path) -> dict[str, Any]:
    """Flyby-style media probes on an already-mounted ISO root."""
    root = Path(root)
    sources = root / "sources"
    setup = root / "setup.exe"
    prep = sources / "setupprep.exe"
    cver = sources / "cversion.ini"
    min_client = ""
    build = 0
    ubr = 0
    pe_ver = ""
    if cver.exists():
        try:
            text = cver.read_text(encoding="utf-8", errors="replace")
            min_client, build, ubr = parse_cversion_ini(text)
            log(f"cversion.ini MinClient={min_client} (build {build}.{ubr})", "OK")
        except Exception as e:
            log(f"cversion.ini read: {e}", "WARN")
    for pe in (prep, setup):
        if pe.exists():
            pe_ver = pe_product_version(pe)
            if pe_ver:
                log(f"PE ProductVersion ({pe.name}): {pe_ver}", "INFO")
                if not build:
                    # 10.0.26100.xxxx or 11.0.…
                    m = re.search(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", pe_ver)
                    if m:
                        build = int(m.group(3))
                        ubr = int(m.group(4))
                        min_client = min_client or pe_ver
                break
    family = build_to_family(build)
    return {
        "min_client": min_client,
        "build": build,
        "ubr": ubr,
        "win_family": family,
        "display_version": build_to_display(build),
        "has_setup": setup.exists(),
        "has_setupprep": prep.exists(),
        "setup_pe_version": pe_ver,
        "mount_root": str(root),
    }


def inspect_iso(
    path: Path,
    *,
    compute_hash: bool = True,
    remount: bool = True,
) -> IsoInfo:
    """
    Full inspection: optional mount (Flyby) + winver + MD5/SHA256.
    Uses catalog cache when path/size/mtime unchanged.
    """
    path = path.resolve()
    st = path.stat()
    cached = catalog_lookup(path)
    if cached and cached.verified and cached.build > 0:
        if not compute_hash or (cached.md5 and cached.sha256):
            log(
                f"ISO catalog hit: {path.name} → Win{cached.win_family} "
                f"{cached.display_version} MD5={cached.md5[:8]}…",
                "OK",
            )
            return cached

    info = IsoInfo(
        path=str(path),
        size=st.st_size,
        mtime=st.st_mtime,
        md5=(cached.md5 if cached else ""),
        sha256=(cached.sha256 if cached else ""),
    )

    if remount:
        from .virtdisk import dismount_iso, mount_iso

        report_progress(
            phase=f"Inspect {path.name}",
            percent=None,
            detail="Mounting ISO (Flyby-style setupprep / cversion)…",
            indeterminate=True,
        )
        root = None
        try:
            root = mount_iso(path)
            meta = inspect_mounted_root(root)
            info.min_client = meta["min_client"]
            info.build = int(meta["build"] or 0)
            info.ubr = int(meta["ubr"] or 0)
            info.win_family = meta["win_family"]
            info.display_version = meta["display_version"]
            info.has_setup = bool(meta["has_setup"])
            info.has_setupprep = bool(meta["has_setupprep"])
            info.setup_pe_version = meta["setup_pe_version"]
            info.mount_root = meta["mount_root"]
            # Flyby gate: setupprep preferred; setup.exe acceptable
            info.verified = bool(info.has_setupprep or info.has_setup) and info.build > 0
            if not info.has_setupprep and info.has_setup:
                log("setupprep.exe missing — setup.exe present (non-Flyby media layout)", "WARN")
            if not info.verified:
                log(f"ISO media incomplete or unknown winver: {path.name}", "WARN")
        except Exception as e:
            log(f"ISO mount inspect failed ({path.name}): {e}", "WARN")
            info.verified = False
        finally:
            try:
                dismount_iso(path)
            except Exception:
                pass

    if compute_hash and (not info.md5 or not info.sha256):
        try:
            info.md5, info.sha256 = hash_iso_file(path)
        except Exception as e:
            log(f"ISO hash failed: {e}", "WARN")

    if info.build > 0 or info.md5:
        catalog_store(info)
    return info


def iso_matches_target(info: IsoInfo, win: str, arch: str = "x64") -> bool:
    """True if inspected ISO is the requested Windows family (and arch soft-check)."""
    if not info.verified or not info.win_family:
        return False
    if info.win_family != str(win):
        return False
    # Arch: Win11 is always x64; Win10 x86 rare — filename / pe soft signals only
    if win == "11" and arch != "x64":
        return False
    name = Path(info.path).name.lower()
    if arch == "x86" and ("x64" in name or "64bit" in name):
        return False
    if arch == "x64" and ("x86" in name and "x64" not in name) and "32bit" in name:
        return False
    return True


def verify_iso_for_win(
    path: Path,
    win: str,
    arch: str = "x64",
    *,
    compute_hash: bool = True,
) -> IsoInfo | None:
    """Inspect ISO; return IsoInfo if it matches requested Windows version."""
    info = inspect_iso(path, compute_hash=compute_hash, remount=True)
    if iso_matches_target(info, win, arch):
        log(
            f"ISO OK for Windows {win}: {path.name} | "
            f"winver={info.display_version} ({info.min_client}) | "
            f"MD5={info.md5} | setupprep={info.has_setupprep}",
            "OK",
        )
        return info
    if info.win_family:
        log(
            f"ISO rejected for Windows {win}: {path.name} is Windows {info.win_family} "
            f"({info.display_version})",
            "WARN",
        )
    else:
        log(f"ISO rejected (unverified/unknown): {path.name}", "WARN")
    return None
