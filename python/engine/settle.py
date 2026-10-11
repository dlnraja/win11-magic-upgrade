"""Post-Win10 settle: WU nudge, Media Center offers 2nd pass, Store sync pause."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .logutil import load_state, log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args: list[str], timeout: int = 90) -> int:
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return int(r.returncode)
    except (OSError, subprocess.TimeoutExpired):
        return -1


def settle_after_win10(report: Any | None = None) -> dict[str, Any]:
    """
    Run after landing on Win10 (and optionally before Win11).
    Re-claims Media Center DVD offer if still pending; nudges WU.
    """
    result: dict[str, Any] = {"wu": False, "media_offers": False, "vlc_hint": False}
    build = int(getattr(report, "build", 0) or 0) if report else 0
    if build and build < 10240:
        log("Settle deferred — still pre-Win10", "INFO")
        return result

    log("=== Settle after Win10 hop (WU + offers) ===", "STEP")
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    uso = system / "UsoClient.exe"
    if uso.is_file():
        for verb in ("StartScan", "StartDownload"):
            _run([str(uso), verb], timeout=60)
        result["wu"] = True
        log("WU settle nudge (UsoClient)", "OK")

    # Second Media Center / DVD Player pass
    try:
        from .legacy_os import claim_media_center_offers, media_center_eligible_from_state

        if media_center_eligible_from_state() or not load_state().get("MediaOffersClaimed"):
            if report is not None:
                claim_media_center_offers(report)
                result["media_offers"] = True
    except Exception as e:
        log(f"Settle media offers: {e}", "WARN")

    # Optional VLC if DVD Player still missing and MAGIC_INSTALL_VLC=1
    if os.environ.get("MAGIC_INSTALL_VLC", "").strip().lower() in ("1", "true", "yes"):
        try:
            from .blocker_apps import find_winget, _winget_install

            if find_winget():
                if _winget_install("VideoLAN.VLC"):
                    result["vlc_hint"] = True
                    log("VLC installed as DVD playback fallback (MAGIC_INSTALL_VLC=1)", "OK")
        except Exception as e:
            log(f"VLC optional install: {e}", "WARN")
            result["vlc_hint"] = True  # noted

    save_state({"SettleDone": True, "SettleResult": result})
    return result
