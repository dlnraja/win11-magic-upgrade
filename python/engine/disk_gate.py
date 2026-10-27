"""Dynamic free-space gate + optional reclaim before ISO hops."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .logutil import log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MIN_FREE_GB = float(os.environ.get("MAGIC_MIN_FREE_GB", "18") or 18)


def free_gb(drive: str | None = None) -> float:
    root = drive or (os.environ.get("SystemDrive", "C:") + "\\")
    try:
        u = shutil.disk_usage(root)
        return round(u.free / (1024**3), 1)
    except OSError:
        return 0.0


def reclaim_space(*, aggressive: bool = False) -> dict[str, Any]:
    """
    Best-effort free space. Aggressive (hiberfil / temp) when MAGIC_FREE_SPACE=1
    or free < MIN_FREE_GB.
    """
    summary: dict[str, Any] = {"before": free_gb(), "actions": [], "after": 0.0}
    allow = os.environ.get("MAGIC_FREE_SPACE", "").strip().lower() in ("1", "true", "yes")
    if not allow and summary["before"] >= MIN_FREE_GB and not aggressive:
        summary["after"] = summary["before"]
        return summary

    log(f"=== Disk space reclaim (free={summary['before']} GB, min={MIN_FREE_GB}) ===", "STEP")
    # Temp
    for env in ("TEMP", "TMP"):
        t = Path(os.environ.get(env, ""))
        if t.is_dir():
            n = 0
            try:
                for p in t.glob("*"):
                    try:
                        if p.is_file():
                            p.unlink(missing_ok=True)
                            n += 1
                        elif p.is_dir():
                            shutil.rmtree(p, ignore_errors=True)
                            n += 1
                    except OSError:
                        continue
            except OSError:
                pass
            if n:
                summary["actions"].append(f"temp:{env}:{n}")
    # Recycle is per-user; skip heavy shell
    # Hibernation off when still low
    if free_gb() < MIN_FREE_GB or aggressive or allow:
        try:
            r = subprocess.run(
                ["powercfg", "/hibernate", "off"],
                capture_output=True,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                summary["actions"].append("hibernate_off")
                log("Hibernation disabled to reclaim hiberfil.sys", "OK")
        except (OSError, subprocess.TimeoutExpired):
            pass
    summary["after"] = free_gb()
    log(f"Free space: {summary['before']} → {summary['after']} GB", "OK")
    save_state({"DiskReclaim": summary})
    return summary


def ensure_space_for_upgrade(report: Any | None = None) -> dict[str, Any]:
    """Gate: reclaim if needed; warn/block only when MAGIC_BLOCK_LOW_SPACE=1."""
    before = free_gb()
    result = {"ok": True, "free_gb": before, "reclaim": None}
    if before < MIN_FREE_GB:
        result["reclaim"] = reclaim_space(aggressive=True)
        result["free_gb"] = free_gb()
    if result["free_gb"] < MIN_FREE_GB:
        msg = (
            f"Low free disk: {result['free_gb']} GB (recommend ≥{MIN_FREE_GB} GB). "
            "Set MAGIC_FREE_SPACE=1 already applied reclaim; free more manually if Setup fails."
        )
        log(msg, "WARN")
        if os.environ.get("MAGIC_BLOCK_LOW_SPACE", "").strip().lower() in ("1", "true", "yes"):
            result["ok"] = False
            from .errors import UpgradeBlockedError

            raise UpgradeBlockedError(msg, kind="low-disk")
    else:
        log(f"Disk space OK: {result['free_gb']} GB free", "OK")
    if report is not None and hasattr(report, "free_gb"):
        try:
            report.free_gb = result["free_gb"]  # type: ignore[attr-defined]
        except Exception:
            pass
    return result
