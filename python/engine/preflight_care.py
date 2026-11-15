"""Offline preflight warnings. Nothing here writes the disk unless a flag says so."""
from __future__ import annotations

import os
from pathlib import Path


def hiberboot_enabled(registry_value: int | None, hiberfil_present: bool = False) -> bool:
    if registry_value == 1:
        return True
    return bool(hiberfil_present and registry_value is None)


def hiberboot_plan(*, enabled: bool) -> dict:
    """Disable Fast Startup before an offline repair. Restore is the caller's job."""
    return {
        "enabled": enabled,
        "disable": bool(enabled),
        "commands": ["powercfg /h off"] if enabled else [],
        "message": (
            "Fast Startup is on, so C: may stay hibernated. Offline repair and Linux will refuse it. "
            "Run powercfg /h off and shut down fully before that work, then turn hibernate back on if you still want it."
            if enabled
            else "Fast Startup is off."
        ),
    }


def onedrive_redirects(shell_folders: dict[str, str], profile: str) -> list[str]:
    """Known Folder Move paths that point under OneDrive."""
    root = str(profile or "").rstrip("\\/")
    moved: list[str] = []
    for name, raw in (shell_folders or {}).items():
        path = str(raw or "")
        if "onedrive" in path.lower() and (not root or path.lower().startswith(root.lower()) or "onedrive" in path.lower()):
            moved.append(f"{name} -> {path}")
    return moved


def onedrive_warning(moved: list[str]) -> str:
    if not moved:
        return ""
    return (
        "Desktop or Documents are redirected into OneDrive. Turning OneDrive off can look like the files vanished. "
        "They are still in the OneDrive folder. "
        + "; ".join(moved)
    )


def prevent_device_encryption_enabled() -> bool:
    return os.environ.get("MAGIC_PREVENT_DEVICE_ENCRYPTION", "").strip().lower() in ("1", "true", "yes")


def device_encryption_unattend_snippet() -> str:
    """Opt-in unattend fragment. Not written unless the caller asks."""
    if not prevent_device_encryption_enabled():
        return ""
    return (
        "<settings pass=\"specialize\">"
        "<component name=\"Microsoft-Windows-SecureStartup-FilterDriver\">"
        "<PreventDeviceEncryption>true</PreventDeviceEncryption>"
        "</component></settings>"
    )


def driver_inject_plan(*, export_dir: str, boot_wim: str, opt_in: bool | None = None) -> dict:
    """pnputil export plus DISM Add-Driver. Runs only with MAGIC_INJECT_RESCUE_DRIVERS=1."""
    allowed = (
        opt_in
        if opt_in is not None
        else os.environ.get("MAGIC_INJECT_RESCUE_DRIVERS", "").strip().lower() in ("1", "true", "yes")
    )
    commands = [
        f"pnputil /export-driver * {export_dir}",
        f"dism /Image:{boot_wim} /Add-Driver /Driver:{export_dir} /Recurse",
    ]
    return {"apply": bool(allowed), "commands": commands if allowed else []}


def ventoy_mok_note(secure_boot: bool, ventoy_present: bool) -> str:
    if not ventoy_present:
        return ""
    if secure_boot:
        return (
            "This USB looks like Ventoy and Secure Boot is on. Expect Verification failed (0x1A) "
            "until ENROLL_THIS_KEY_IN_MOKMANAGER.cer is enrolled once in MOK Manager. "
            "Prefer the Microsoft-signed WinPE rescue when Secure Boot stays on."
        )
    return "Ventoy was detected. Secure Boot is off, so MOK enrollment is not required."


def uup_fallback_plan(*, languages: list[str], wimlib_version: str, have_wim: bool) -> dict:
    """UUP conversion rules. Does not download or convert."""
    problems: list[str] = []
    if len({item.lower() for item in languages if item}) > 1:
        problems.append("use one language only; mixed ESDs duplicate image names")
    parts = (wimlib_version or "0").split(".")
    try:
        numbers = [int(part) for part in parts[:3]]
    except ValueError:
        numbers = [0]
    while len(numbers) < 3:
        numbers.append(0)
    if tuple(numbers) < (1, 14, 3):
        problems.append("wimlib must be 1.14.3 or newer for non-ASCII descriptions")
    if not have_wim:
        problems.append("driver add-ons need install.wim, not install.esd")
    return {"ok": not problems, "problems": problems}
