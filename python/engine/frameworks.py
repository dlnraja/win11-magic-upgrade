"""
Install common Windows frameworks / runtimes needed by apps after migration.

Uses winget when available, plus DISM for .NET 3.5. Pure Python — no PowerShell.

Env (also set from GUI checkboxes):
  MAGIC_INSTALL_FRAMEWORKS=1   Install the default set before Setup / after settle
  MAGIC_INSTALL_NETFX35=1
  MAGIC_INSTALL_NETFX48=1
  MAGIC_INSTALL_DOTNET_DESKTOP=1
  MAGIC_INSTALL_DIRECTX=1
  MAGIC_INSTALL_VCREDIST=1
  MAGIC_INSTALL_MEDIA_FEATURE=1
Disable all: MAGIC_SKIP_FRAMEWORKS=1
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logutil import log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class FrameworkJob:
    id: str
    label: str
    env: str
    default: bool = True
    winget_ids: list[str] = field(default_factory=list)
    dism_feature: str | None = None
    notes: str = ""


# Curated set — silent winget / DISM only
FRAMEWORK_JOBS: list[FrameworkJob] = [
    FrameworkJob(
        "netfx35",
        ".NET Framework 3.5 (NetFx3)",
        "MAGIC_INSTALL_NETFX35",
        default=True,
        dism_feature="NetFx3",
        notes="Legacy apps / some Setup paths",
    ),
    FrameworkJob(
        "netfx48",
        ".NET Framework 4.8",
        "MAGIC_INSTALL_NETFX48",
        default=True,
        winget_ids=["Microsoft.DotNet.Framework.DeveloperPack_4"],
        notes="winget may offer runtime/dev pack; best-effort",
    ),
    FrameworkJob(
        "dotnet_desktop",
        ".NET Desktop Runtime 8",
        "MAGIC_INSTALL_DOTNET_DESKTOP",
        default=True,
        winget_ids=["Microsoft.DotNet.DesktopRuntime.8"],
    ),
    FrameworkJob(
        "directx",
        "DirectX End-User Runtime (Jun 2010)",
        "MAGIC_INSTALL_DIRECTX",
        default=True,
        winget_ids=["Microsoft.DirectX"],
        notes="Legacy DirectX redistributable for older games/apps",
    ),
    FrameworkJob(
        "vcredist",
        "Visual C++ Redistributables 2015–2022",
        "MAGIC_INSTALL_VCREDIST",
        default=True,
        winget_ids=[
            "Microsoft.VCRedist.2015+.x64",
            "Microsoft.VCRedist.2015+.x86",
        ],
    ),
    FrameworkJob(
        "media_feature",
        "Media Feature Pack (N editions)",
        "MAGIC_INSTALL_MEDIA_FEATURE",
        default=False,
        dism_feature="MediaPlayback",
        notes="Also tries WindowsMediaPlayer capability on N SKUs",
    ),
]


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_false(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("0", "false", "no", "off")


def _find_winget() -> Path | None:
    candidates = [
        Path(os.environ.get("LocalAppData", "")) / "Microsoft" / "WindowsApps" / "winget.exe",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "winget.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    w = shutil.which("winget")
    return Path(w) if w else None


def _run(cmd: list[str], *, timeout: int = 900) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except OSError as e:
        return 1, str(e)


def _winget_install(pkg: str) -> bool:
    winget = _find_winget()
    if winget is None:
        log("winget not available — cannot install frameworks via packages", "WARN")
        return False
    code, out = _run(
        [
            str(winget),
            "install",
            "--id",
            pkg,
            "-e",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
            "--silent",
        ],
        timeout=1200,
    )
    low = out.lower()
    if code == 0 or "already installed" in low or code == -1978335189:
        log(f"Framework winget OK: {pkg}", "OK")
        return True
    log(f"Framework winget {pkg} failed ({code}): {out[:200]}", "WARN")
    return False


def _dism_enable(feature: str) -> bool:
    code, out = _run(
        [
            "dism",
            "/online",
            "/enable-feature",
            f"/featurename:{feature}",
            "/all",
            "/norestart",
        ],
        timeout=900,
    )
    low = out.lower()
    if code == 0 or "already" in low or "enabled" in low:
        log(f"DISM feature OK: {feature}", "OK")
        return True
    log(f"DISM {feature}: {out[-160:]}", "WARN")
    return False


def _dism_capability(name: str) -> bool:
    code, out = _run(
        [
            "dism",
            "/online",
            "/add-capability",
            f"/capabilityname:{name}",
            "/norestart",
        ],
        timeout=900,
    )
    low = out.lower()
    if code == 0 or "already" in low:
        log(f"DISM capability OK: {name}", "OK")
        return True
    log(f"DISM capability {name}: {out[-140:]}", "INFO")
    return False


def job_selected(job: FrameworkJob) -> bool:
    if _env_true("MAGIC_SKIP_FRAMEWORKS"):
        return False
    if _env_true(job.env):
        return True
    if _env_false(job.env):
        return False
    # Master switch selects defaults
    if _env_true("MAGIC_INSTALL_FRAMEWORKS"):
        return job.default
    return False


def selected_framework_ids() -> list[str]:
    return [j.id for j in FRAMEWORK_JOBS if job_selected(j)]


def install_frameworks(*, force_all_defaults: bool = False) -> dict[str, Any]:
    """
    Install selected frameworks. If force_all_defaults and no env set,
    install the default curated set (used by GUI « Install frameworks »).
    """
    result: dict[str, Any] = {"ok": [], "failed": [], "skipped": True}
    if _env_true("MAGIC_SKIP_FRAMEWORKS"):
        log("MAGIC_SKIP_FRAMEWORKS=1 — skip framework installs", "INFO")
        return result

    if force_all_defaults:
        os.environ["MAGIC_INSTALL_FRAMEWORKS"] = "1"
        os.environ.pop("MAGIC_SKIP_FRAMEWORKS", None)
        # If every individual flag is off/absent, enable curated defaults
        if not any(job_selected(j) for j in FRAMEWORK_JOBS):
            for j in FRAMEWORK_JOBS:
                if j.default:
                    os.environ[j.env] = "1"

    jobs = [j for j in FRAMEWORK_JOBS if job_selected(j)]
    if not jobs:
        log("No framework installs selected", "INFO")
        return result

    result["skipped"] = False
    log(f"=== Install frameworks ({len(jobs)}) ===", "STEP")
    for job in jobs:
        log(f"Framework: {job.label}", "STEP")
        ok = False
        if job.dism_feature:
            ok = _dism_enable(job.dism_feature) or ok
        if job.id == "media_feature":
            ok = (
                _dism_capability("Media.WindowsMediaPlayer~~~~0.0.1.0")
                or _dism_capability("Media.MediaFeaturePack~~~~0.0.1.0")
                or ok
            )
        for pkg in job.winget_ids:
            if _winget_install(pkg):
                ok = True
        # netfx48: also try DISM NetFx4 if present as feature name variants
        if job.id == "netfx48" and not ok:
            for feat in ("NetFx4", "NetFx4Extended-ASPNET45"):
                if _dism_enable(feat):
                    ok = True
                    break
        if ok:
            result["ok"].append(job.id)
        else:
            result["failed"].append(job.id)

    save_state({"FrameworksInstall": result})
    log(
        f"Frameworks done: ok={result['ok']} failed={result['failed']}",
        "OK" if not result["failed"] else "WARN",
    )
    return result
