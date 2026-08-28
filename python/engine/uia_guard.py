"""
UI Automation (UIA) / remote / AI-driven automation defenses.

Goal: stop silent One-Click / elevation when a non-human agent may be driving the UI
(UI Automation clients, AutoHotkey, RDP, known bot parents). Not a security boundary —
defense-in-depth for an admin-elevating installer.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field

from .logutil import log

# Parent / sibling process name hints (lowercase, no .exe required)
_AUTOMATION_PROCESS_HINTS = (
    "autohotkey",
    "ahk",
    "autoit",
    "au3",
    "flaui",
    "inspect",
    "uiautomation",
    "pywinauto",
    "sikuli",
    "selenium",
    "appium",
    "winappdriver",
    "cucumber",
    "testcomplete",
    "ranorex",
    "leapwork",
    "powerautomate",
    "ui.path",
    "uipath",
    "robotframework",
    "nw.js",  # sometimes used to host bots
)

# Env vars often set by AI / agent hosts (score 1 alone; need another signal)
_AGENT_ENV_HINTS = (
    "CURSOR_AGENT",
    "CURSOR_TRACE_ID",
    "CLAUDE_CODE",
    "CONTINUE_AGENT",
    "GITHUB_ACTIONS",
    "CI",
    "TF_BUILD",
    "JENKINS_URL",
    "GITLAB_CI",
)
# Prefix matches (score 1)
_AGENT_ENV_PREFIXES = ("AIDER_",)


@dataclass
class UiaRisk:
    risky: bool = False
    reasons: list[str] = field(default_factory=list)
    score: int = 0

    def add(self, points: int, reason: str) -> None:
        self.score += points
        self.reasons.append(reason)
        if self.score >= 2:
            self.risky = True


def _env_allows_automation() -> bool:
    v = os.environ.get("MAGIC_ALLOW_AUTOMATION", "").strip().lower()
    return v in ("1", "true", "yes")


def _remote_session() -> bool:
    try:
        # SM_REMOTESESSION = 0x1000
        return bool(__import__("ctypes").windll.user32.GetSystemMetrics(0x1000))
    except Exception:
        return False


def _process_names() -> set[str]:
    names: set[str] = set()
    try:
        import subprocess

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Process | Select-Object -ExpandProperty Name",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=flags,
        )
        if p.returncode == 0 and p.stdout:
            for line in p.stdout.splitlines():
                n = (line or "").strip().lower()
                if n:
                    names.add(n.replace(".exe", ""))
    except Exception:
        pass
    return names


def assess_uia_risk() -> UiaRisk:
    risk = UiaRisk()
    if _env_allows_automation():
        return risk

    if _remote_session():
        # Soft signal only — RDP alone must not block intentional One-Click (false positive)
        risk.add(1, "remote_desktop_session")

    # Agent / CI environment (weak alone — combine with RDP / automation tools)
    agent_hits: list[str] = []
    for k in _AGENT_ENV_HINTS:
        if os.environ.get(k):
            agent_hits.append(k)
    for pref in _AGENT_ENV_PREFIXES:
        if any(ek.startswith(pref) for ek in os.environ):
            agent_hits.append(pref + "*")
    if agent_hits:
        risk.add(1, "agent_env:" + ",".join(agent_hits[:4]))

    try:
        procs = _process_names()
        hits = sorted({h for h in _AUTOMATION_PROCESS_HINTS if any(h in p for p in procs)})
        if hits:
            risk.add(2, "automation_process:" + ",".join(hits[:6]))
    except Exception:
        pass

    # Extremely fast start after process birth → likely scripted launch
    try:
        boot = float(os.environ.get("MAGIC_APP_START_MONO", "0") or 0)
        if boot > 0:
            elapsed = time.monotonic() - boot
            if elapsed < 0.8 and "--auto" in " ".join(sys.argv).lower():
                risk.add(1, "instant_auto_launch")
    except Exception:
        pass

    return risk


def require_human_confirm(ask_yes_no, *, title: str, message: str, action: str) -> bool:
    """
    ask_yes_no(title, message) -> bool  (e.g. messagebox.askyesno)
    Returns True if the action may proceed.
    """
    if _env_allows_automation():
        log("MAGIC_ALLOW_AUTOMATION=1 — UIA guard bypassed", "WARN")
        return True

    risk = assess_uia_risk()
    force = os.environ.get("MAGIC_CONFIRM", "").strip() in ("1", "true", "yes")
    if not risk.risky and not force and action != "oneclick":
        return True

    # One-Click always gets a confirm when risk OR MAGIC_CONFIRM; when risk, always confirm.
    if not risk.risky and not force:
        # Still require confirm for oneclick if MAGIC_UIA_STRICT=1
        if os.environ.get("MAGIC_UIA_STRICT", "").strip().lower() not in ("1", "true", "yes"):
            return True

    reasons = ", ".join(risk.reasons) if risk.reasons else "policy"
    log(f"UIA/automation guard active ({reasons})", "WARN")
    detail = message
    if risk.reasons:
        detail += "\n\nAutomation risk signals:\n- " + "\n- ".join(risk.reasons)
        detail += "\n\nSet MAGIC_ALLOW_AUTOMATION=1 only if you intentionally automate."
    try:
        return bool(ask_yes_no(title, detail))
    except Exception as e:
        log(f"UIA confirm UI failed: {e}", "ERROR")
        return False


def mark_app_start() -> None:
    """Call once at process start for dwell-time checks."""
    os.environ.setdefault("MAGIC_APP_START_MONO", str(time.monotonic()))


def cli_blocks_silent_oneclick() -> bool:
    """CLI --oneclick / --cli without TTY should not run silently under automation risk."""
    if _env_allows_automation():
        return False
    risk = assess_uia_risk()
    if risk.risky:
        log(
            "Refusing silent CLI upgrade under automation risk: "
            + ", ".join(risk.reasons)
            + " (set MAGIC_ALLOW_AUTOMATION=1 to override)",
            "ERROR",
        )
        return True
    return False
