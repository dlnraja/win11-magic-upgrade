"""
GUI option definitions + persistence for Win11 Magic Upgrade.

Checkboxes map to MAGIC_* environment variables applied before One-Click.
Preferences saved under %LOCALAPPDATA%\\Win11MagicUpgrade\\gui-options.json
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GuiOption:
    key: str
    env: str
    label_en: str
    label_fr: str
    default: bool
    invert: bool = False  # True = checked means env=0 / skip off
    group: str = "migration"


OPTIONS: list[GuiOption] = [
    # Migration
    GuiOption(
        "soft_uninstall",
        "MAGIC_SKIP_SOFT_UNINSTALL",
        "Soft-uninstall blocker apps (backup/LGS/VPN…)",
        "Désinstaller en douceur les apps bloquantes (backup/LGS/VPN…)",
        True,
        invert=True,
        group="migration",
    ),
    GuiOption(
        "restore_apps",
        "MAGIC_SKIP_APP_RESTORE",
        "Restore compatible apps after migration (winget)",
        "Réinstaller les apps compatibles après migration (winget)",
        True,
        invert=True,
        group="migration",
    ),
    GuiOption(
        "app_carry",
        "MAGIC_SKIP_APP_CARRY",
        "Carry activation keys + registry/settings",
        "Porter clés d'activation + registre/réglages",
        True,
        invert=True,
        group="migration",
    ),
    GuiOption(
        "unlock_pack",
        "MAGIC_SKIP_UNLOCK_PACK",
        "Unlock pack (Media Center / DVD / Win8 / HW bypass)",
        "Pack unlock (Media Center / DVD / Win8 / bypass matériel)",
        True,
        invert=True,
        group="migration",
    ),
    GuiOption(
        "soft_av",
        "MAGIC_SOFT_UNINSTALL_AV",
        "Also soft-uninstall antivirus (advanced)",
        "Aussi désinstaller l'antivirus en douceur (avancé)",
        False,
        group="migration",
    ),
    GuiOption(
        "install_vlc",
        "MAGIC_INSTALL_VLC",
        "Install VLC if Windows DVD Player missing",
        "Installer VLC si Lecteur DVD Windows absent",
        False,
        group="migration",
    ),
    GuiOption(
        "free_space",
        "MAGIC_FREE_SPACE",
        "Aggressive free disk space before Setup",
        "Libérer l'espace disque agressivement avant Setup",
        False,
        group="migration",
    ),
    GuiOption(
        "confirm",
        "MAGIC_CONFIRM",
        "Ask confirmation before One-Click",
        "Demander confirmation avant One-Click",
        False,
        group="migration",
    ),
    GuiOption(
        "boot_auto_fix",
        "MAGIC_BOOT_AUTO_FIX",
        "Extra Boot Fix pass during patches (ESP/MBR failover)",
        "Passage Boot Fix extra pendant les patches (failover EFI/MBR)",
        False,
        group="migration",
    ),
    GuiOption(
        "write_boot_scripts",
        "MAGIC_BOOT_SCRIPTS",
        "Write WinRE/Desktop remediation scripts on Boot Fix",
        "Écrire scripts WinRE/Bureau lors du Boot Fix",
        True,
        group="migration",
    ),
    GuiOption(
        "openduet_prepare",
        "MAGIC_OPENDUET_PREPARE",
        "Legacy BIOS only: prepare OpenDuet/OpenCore USB files (no disk write)",
        "BIOS Legacy seulement : préparer les fichiers USB OpenDuet/OpenCore (sans écriture disque)",
        False,
        group="migration",
    ),
    # Frameworks
    GuiOption(
        "fw_master",
        "MAGIC_INSTALL_FRAMEWORKS",
        "Install frameworks before / during migration",
        "Installer les frameworks avant / pendant la migration",
        True,
        group="frameworks",
    ),
    GuiOption(
        "fw_netfx35",
        "MAGIC_INSTALL_NETFX35",
        ".NET Framework 3.5",
        ".NET Framework 3.5",
        True,
        group="frameworks",
    ),
    GuiOption(
        "fw_netfx48",
        "MAGIC_INSTALL_NETFX48",
        ".NET Framework 4.8",
        ".NET Framework 4.8",
        True,
        group="frameworks",
    ),
    GuiOption(
        "fw_dotnet",
        "MAGIC_INSTALL_DOTNET_DESKTOP",
        ".NET Desktop Runtime 8",
        ".NET Desktop Runtime 8",
        True,
        group="frameworks",
    ),
    GuiOption(
        "fw_directx",
        "MAGIC_INSTALL_DIRECTX",
        "DirectX End-User Runtime",
        "DirectX End-User Runtime",
        True,
        group="frameworks",
    ),
    GuiOption(
        "fw_vcredist",
        "MAGIC_INSTALL_VCREDIST",
        "Visual C++ 2015–2022 Redistributables",
        "Redistribuables Visual C++ 2015–2022",
        True,
        group="frameworks",
    ),
    GuiOption(
        "fw_media",
        "MAGIC_INSTALL_MEDIA_FEATURE",
        "Media Feature Pack (N editions)",
        "Media Feature Pack (éditions N)",
        False,
        group="frameworks",
    ),
]

GROUP_LABELS = {
    "migration": ("Migration options", "Options de migration"),
    "frameworks": ("Frameworks & runtimes", "Frameworks et runtimes"),
}


def options_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Win11MagicUpgrade"
    base.mkdir(parents=True, exist_ok=True)
    return base / "gui-options.json"


def load_option_values() -> dict[str, bool]:
    defaults = {o.key: o.default for o in OPTIONS}
    p = options_path()
    if not p.is_file():
        return defaults
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if k in defaults:
                    defaults[k] = bool(v)
    except (OSError, ValueError):
        pass
    return defaults


def save_option_values(values: dict[str, bool]) -> None:
    try:
        options_path().write_text(json.dumps(values, indent=2), encoding="utf-8")
    except OSError:
        pass


def apply_options_to_environ(values: dict[str, bool]) -> dict[str, str]:
    """Set MAGIC_* env from checkbox values. Returns applied map."""
    applied: dict[str, str] = {}
    for opt in OPTIONS:
        checked = bool(values.get(opt.key, opt.default))
        if opt.invert:
            # Checked = feature ON → do NOT set skip (or set 0)
            if checked:
                os.environ.pop(opt.env, None)
                applied[opt.env] = "0 (cleared)"
            else:
                os.environ[opt.env] = "1"
                applied[opt.env] = "1"
        else:
            if checked:
                os.environ[opt.env] = "1"
                applied[opt.env] = "1"
            else:
                # Explicit 0 so MAGIC_INSTALL_FRAMEWORKS defaults do not re-enable
                os.environ[opt.env] = "0"
                applied[opt.env] = "0"
    # If master frameworks off, skip all
    if not values.get("fw_master", True):
        os.environ["MAGIC_SKIP_FRAMEWORKS"] = "1"
        applied["MAGIC_SKIP_FRAMEWORKS"] = "1"
    else:
        os.environ.pop("MAGIC_SKIP_FRAMEWORKS", None)
    return applied


def option_label(opt: GuiOption, lang: str) -> str:
    if lang.startswith("fr"):
        return opt.label_fr
    return opt.label_en


def group_label(group: str, lang: str) -> str:
    en, fr = GROUP_LABELS.get(group, (group, group))
    return fr if lang.startswith("fr") else en


def snapshot_for_log(values: dict[str, bool]) -> str:
    lines = ["GUI options:"]
    for opt in OPTIONS:
        on = bool(values.get(opt.key, opt.default))
        lines.append(f"  [{'x' if on else ' '}] {opt.key}")
    return "\n".join(lines)
