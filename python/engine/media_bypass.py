"""Writable Setup media bypass (Rufus/AveYo-class) for Win11 inplace upgrades.

Mounted ISOs are read-only — LabConfig alone is not enough when Microsoft disables
`/product server` (reported on some 25H2 channels). Staging a writable copy and
neutralizing Appraiser DLL/SDB removes the hardware gate at the media layer.

Does NOT patch setup.exe / gatherosstate (activation). No PowerShell.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .logutil import STATE_DIR, log

STAGE_DIR = STATE_DIR / "Win11SetupStage"

# Appraiser / SoftBlock artifacts commonly stripped by Rufus / community tools
APPRAISER_KILL_NAMES = (
    "appraiserres.dll",
    "appraiser.sdb",
    "appcompat.sdb",
    "SetupCompat.ini",  # sometimes present; we rewrite our own
)


def _run(cmd: list[str], timeout: int = 7200) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 1, str(e)


def neutralize_appraiser_on_media(root: Path) -> int:
    """Rename/remove Appraiser gate files under sources\\."""
    sources = root / "sources"
    if not sources.is_dir():
        return 0
    n = 0
    for name in APPRAISER_KILL_NAMES:
        candidates = [sources / name]
        candidates.extend(sources.glob(name))
        # de-dupe
        seen: set[str] = set()
        for p in candidates:
            key = str(p).lower()
            if key in seen or not p.exists() or not p.is_file():
                continue
            seen.add(key)
            try:
                bak = Path(str(p) + ".magic.bak")
                if bak.exists():
                    try:
                        bak.unlink()
                    except OSError:
                        pass
                p.rename(bak)
                n += 1
                log(f"Neutralized media Appraiser gate: {p.name}", "OK")
            except OSError as e:
                try:
                    p.unlink()
                    n += 1
                    log(f"Deleted media Appraiser gate: {p.name}", "OK")
                except OSError:
                    log(f"Could not neutralize {p.name}: {e}", "WARN")
    return n


def write_media_setupconfig(root: Path) -> None:
    sources = root / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    body = "\r\n".join(
        [
            "[SetupConfig]",
            "Compat=IgnoreWarning",
            "DynamicUpdate=Enable",
            "ShowOobe=None",
            "Telemetry=Disable",
            "",
        ]
    )
    for dest in (
        sources / "SetupConfig.ini",
        root / "SetupConfig.ini",
    ):
        try:
            dest.write_text(body, encoding="utf-8")
        except OSError:
            pass


def stage_writable_setup(iso_root: str | Path, *, force: bool = False) -> Path:
    """
    Copy ISO mount to a writable staging folder and neutralize Appraiser.
    Returns staged root containing setup.exe.
    """
    src = Path(iso_root)
    if not (src / "setup.exe").exists():
        raise FileNotFoundError(f"setup.exe not found under {src}")

    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    staged_setup = STAGE_DIR / "setup.exe"
    marker = STAGE_DIR / ".stage-ok"

    if (
        not force
        and staged_setup.exists()
        and marker.exists()
        and (STAGE_DIR / "sources").is_dir()
    ):
        log(f"Reusing writable Setup stage: {STAGE_DIR}", "OK")
        neutralize_appraiser_on_media(STAGE_DIR)
        write_media_setupconfig(STAGE_DIR)
        return STAGE_DIR

    log(
        f"Staging writable Win11 Setup (Appraiser bypass) → {STAGE_DIR} "
        "(may take several minutes)...",
        "STEP",
    )
    # Prefer robocopy for speed/resilience on large trees
    # /MIR mirrors; /R:1 /W:1 retry; /NFL /NDL /NJH /NJS quiet-ish
    code, out = _run(
        [
            "robocopy",
            str(src),
            str(STAGE_DIR),
            "/E",
            "/COPY:DAT",
            "/R:1",
            "/W:1",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
            "/NP",
            "/XD",
            "System Volume Information",
        ],
        timeout=7200,
    )
    # robocopy: 0-7 success-ish
    if code >= 8:
        log(f"robocopy failed ({code}): {out[-300:]} — trying shutil copytree", "WARN")
        if STAGE_DIR.exists():
            shutil.rmtree(STAGE_DIR, ignore_errors=True)
        STAGE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, STAGE_DIR, dirs_exist_ok=True)

    if not (STAGE_DIR / "setup.exe").exists():
        raise RuntimeError("Writable Setup stage incomplete (no setup.exe)")

    n = neutralize_appraiser_on_media(STAGE_DIR)
    write_media_setupconfig(STAGE_DIR)
    marker.write_text(f"appraiser_neutralized={n}\n", encoding="utf-8")
    log(f"Writable Setup ready ({n} Appraiser gates neutralized)", "OK")
    return STAGE_DIR


def prepare_setup_root(iso_mount: str | Path, *, win11: bool) -> Path:
    """
    For Win11: stage writable media with Appraiser neutralized.
    For Win10 intermediate: use mount directly (faster).
    """
    mount = Path(iso_mount)
    if not win11:
        return mount
    try:
        return stage_writable_setup(mount)
    except Exception as e:
        log(f"Writable Setup stage failed ({e}) — falling back to ISO mount", "WARN")
        return mount
