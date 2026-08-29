"""
Offline Dynamic Update cab staging (MAGIC_DU_CAB_DIR).

Forums: SafeOS / SSU / LCU DU packages reduce 0xC1900101 / WIM apply failures
on air-gapped or flaky-network PCs. We never invent packages — only copy user-supplied .cab.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log

MAX_CABS = 40
MAX_CAB_BYTES = 800 * 1024 * 1024  # skip absurdly large files


def du_cab_dir() -> Path | None:
    raw = (os.environ.get("MAGIC_DU_CAB_DIR") or "").strip().strip('"')
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def prefer_offline_du() -> bool:
    """Use /dynamicupdate disable when offline cabs staged or MAGIC_DU_OFFLINE=1."""
    if os.environ.get("MAGIC_DU_OFFLINE", "").strip().lower() in ("1", "true", "yes"):
        return True
    return False


def stage_offline_du_cabs(setup_root: Path | None = None) -> dict[str, Any]:
    """
    Copy *.cab from MAGIC_DU_CAB_DIR into:
      1) STATE_DIR/DynamicUpdate (always, if writable)
      2) setup_root/sources/DynamicUpdate when the media tree is writable
    Returns summary for logging / SetupConfig.
    """
    result: dict[str, Any] = {
        "ok": False,
        "copied": 0,
        "dest": None,
        "media_dest": None,
        "names": [],
    }
    src = du_cab_dir()
    if not src:
        return result

    cabs = sorted(src.glob("*.cab"))[:MAX_CABS]
    if not cabs:
        log(f"MAGIC_DU_CAB_DIR set but no .cab files in {src}", "WARN")
        return result

    dest = STATE_DIR / "DynamicUpdate"
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log(f"Cannot create DU stage {dest}: {e}", "WARN")
        return result

    copied = 0
    names: list[str] = []
    for cab in cabs:
        try:
            if cab.stat().st_size > MAX_CAB_BYTES:
                log(f"Skip oversized cab {cab.name}", "WARN")
                continue
            target = dest / cab.name
            if not target.exists() or target.stat().st_size != cab.stat().st_size:
                shutil.copy2(cab, target)
            copied += 1
            names.append(cab.name)
        except OSError as e:
            log(f"DU cab copy {cab.name}: {e}", "WARN")

    result["copied"] = copied
    result["dest"] = str(dest)
    result["names"] = names
    result["ok"] = copied > 0
    if copied:
        log(f"Staged {copied} offline Dynamic Update cab(s) → {dest}", "OK")

    # Writable Setup media (staged ISO copy) — optional second copy
    if setup_root and copied:
        sources = Path(setup_root) / "sources"
        media_du = sources / "DynamicUpdate"
        try:
            if sources.is_dir() and os.access(str(sources), os.W_OK):
                media_du.mkdir(parents=True, exist_ok=True)
                for name in names:
                    s = dest / name
                    t = media_du / name
                    if s.is_file() and (not t.exists() or t.stat().st_size != s.stat().st_size):
                        shutil.copy2(s, t)
                result["media_dest"] = str(media_du)
                log(f"Also staged DU cabs on writable media → {media_du}", "OK")
        except OSError as e:
            log(f"Media DU stage skipped: {e}", "INFO")

    return result
