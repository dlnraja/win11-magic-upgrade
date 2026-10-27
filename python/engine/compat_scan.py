"""
Pre-Setup compat intelligence: parse CompatData / Appraiser human-readable XML,
soften BlockMigration, detect blocking drivers/apps, optional orphan cleanup.

Does not run full setup.exe /compat scanonly (needs ISO mount); instead harvests
existing Panther caches + softens them before quiet Setup.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log, save_state


@dataclass
class CompatScanResult:
    blockers_apps: list[str] = field(default_factory=list)
    blockers_drivers: list[str] = field(default_factory=list)
    orphan_paths: list[str] = field(default_factory=list)
    softened: int = 0
    files_scanned: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _panther_roots() -> list[Path]:
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return [
        Path(r"C:\$WINDOWS.~BT\Sources\Panther"),
        windir / "Panther",
        STATE_DIR / "Panther",
        Path(os.environ.get("SystemDrive", "C:") + r"\Windows\Logs\MoSetup"),
    ]


def _iter_compat_xml() -> list[Path]:
    out: list[Path] = []
    for root in _panther_roots():
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*.xml"):
                n = p.name.lower()
                if "compatdata" in n or "appraiser" in n or "humanreadable" in n:
                    out.append(p)
        except OSError:
            continue
    return out[:40]


def _textish(el: ET.Element) -> str:
    parts = [(el.text or "")]
    for c in el.iter():
        if c is el:
            continue
        if c.text:
            parts.append(c.text)
        for k, v in (c.attrib or {}).items():
            parts.append(f"{k}={v}")
    return " ".join(parts)


def parse_compat_xml(path: Path, result: CompatScanResult) -> None:
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except (OSError, ET.ParseError) as e:
        result.notes.append(f"parse skip {path.name}: {e}")
        return
    result.files_scanned += 1
    blob = ET.tostring(root, encoding="unicode", method="xml")
    # Driver packages blocking migration
    for m in re.finditer(
        r'BlockMigration\s*=\s*"?True"?[^>]{0,200}(?:Driver|Inf|Name|Path)\s*=\s*"([^"]+)"',
        blob,
        re.I,
    ):
        result.blockers_drivers.append(m.group(1)[:200])
    for m in re.finditer(r'DriverPackage[^>]*BlockMigration="True"[^>]*Name="([^"]+)"', blob, re.I):
        result.blockers_drivers.append(m.group(1)[:200])
    # Blocking applications
    if "DT_ANY_FMC_BlockingApplication=True" in blob or "BlockingApplication" in blob:
        for m in re.finditer(
            r"LowerCaseLongPathUnexpanded[^>]*>?\s*([^<\r\n]+)|"
            r'LowerCaseLongPathUnexpanded="([^"]+)"|'
            r'Name="([^"]+)"[^>]{0,80}BlockMigration="True"',
            blob,
            re.I,
        ):
            val = next((g for g in m.groups() if g), "")
            val = val.strip()
            if val and len(val) > 3:
                if "\\" in val or "/" in val:
                    result.orphan_paths.append(val[:260])
                else:
                    result.blockers_apps.append(val[:200])
    # Generic BlockMigration app names
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        attrs = {k.lower(): v for k, v in (el.attrib or {}).items()}
        if attrs.get("blockmigration", "").lower() == "true":
            name = attrs.get("name") or attrs.get("id") or _textish(el)[:120]
            if name:
                if any(x in name.lower() for x in (".inf", "driver", "\\")):
                    result.blockers_drivers.append(name[:200])
                else:
                    result.blockers_apps.append(name[:200])


def soften_block_migration_in_file(path: Path) -> int:
    """Rewrite BlockMigration=True → False in CompatData caches (best-effort)."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if "BlockMigration" not in raw:
        return 0
    new, n = re.subn(
        r'BlockMigration\s*=\s*"True"',
        'BlockMigration="False"',
        raw,
        flags=re.I,
    )
    new2, n2 = re.subn(
        r"(BlockMigration\s*>\s*)True(\s*<)",
        r"\1False\2",
        new,
        flags=re.I,
    )
    total = n + n2
    if total:
        try:
            path.write_text(new2, encoding="utf-8")
        except OSError:
            return 0
    return total


def cleanup_orphan_blocker_files(paths: list[str], *, apply: bool = False) -> list[str]:
    """
    Remove leftover blocker files only when MAGIC_CLEAN_ORPHAN_BLOCKERS=1.
    Default: report paths only (safe).
    """
    allow = os.environ.get("MAGIC_CLEAN_ORPHAN_BLOCKERS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    cleaned: list[str] = []
    if not apply or not allow:
        return cleaned
    for raw in paths[:30]:
        p = Path(os.path.expandvars(raw.strip().strip('"')))
        # Only under Program Files / ProgramData — never System32 random deletes
        try:
            s = str(p.resolve()).lower()
        except OSError:
            continue
        if not any(
            x in s
            for x in (
                "\\program files\\",
                "\\program files (x86)\\",
                "\\programdata\\",
            )
        ):
            continue
        if not p.is_file():
            continue
        try:
            p.unlink()
            cleaned.append(str(p))
            log(f"Removed orphan blocker file: {p}", "OK")
        except OSError as e:
            log(f"Orphan cleanup skip {p}: {e}", "WARN")
    return cleaned


def run_compat_scan(*, soften: bool = True) -> CompatScanResult:
    result = CompatScanResult()
    log("=== CompatData / Appraiser scan ===", "STEP")
    files = _iter_compat_xml()
    if not files:
        result.notes.append("No CompatData/Appraiser XML caches found yet (normal before first Setup).")
        log(result.notes[-1], "INFO")
        save_state({"CompatScan": result.as_dict()})
        return result
    for path in files:
        parse_compat_xml(path, result)
        if soften:
            result.softened += soften_block_migration_in_file(path)
    # Dedupe
    result.blockers_apps = sorted(set(result.blockers_apps))[:40]
    result.blockers_drivers = sorted(set(result.blockers_drivers))[:40]
    result.orphan_paths = sorted(set(result.orphan_paths))[:40]
    cleaned = cleanup_orphan_blocker_files(result.orphan_paths, apply=True)
    if cleaned:
        result.notes.append(f"Removed {len(cleaned)} orphan blocker file(s)")
    if result.softened:
        log(f"Softened BlockMigration in {result.softened} place(s)", "OK")
    if result.blockers_apps:
        log("Blocking apps (CompatData): " + ", ".join(result.blockers_apps[:8]), "WARN")
    if result.blockers_drivers:
        log("Blocking drivers: " + ", ".join(result.blockers_drivers[:8]), "WARN")
    save_state({"CompatScan": result.as_dict()})
    try:
        (STATE_DIR / "compat-scan.json").write_text(
            __import__("json").dumps(result.as_dict(), indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    return result
