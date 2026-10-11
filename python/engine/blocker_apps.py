"""
Blocking-app catalog: soft uninstall before Setup, restore compatible versions after.

SafeOS / 0xC1900101 frequently fail because of backup filters, legacy LGS, AV, VPN, etc.
One-Click:
  1) Detect installed blockers from Apps&Features registry
  2) Capture license keys + registry/AppData (app_carry) before remove
  3) Soft-uninstall curated entries (QuietUninstallString / msiexec)
  4) Persist PendingAppRestores in state.json
  5) After final OS hop: winget-install modern packages, then re-apply carry

Never force-uninstall EDR (CrowdStrike, SentinelOne, …).
AV soft-uninstall only when MAGIC_SOFT_UNINSTALL_AV=1 (default: warn + schedule restore skip).
Disable restore: MAGIC_SKIP_APP_RESTORE=1
Disable soft uninstall: MAGIC_SKIP_SOFT_UNINSTALL=1
Disable key/settings carry: MAGIC_SKIP_APP_CARRY=1
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import winreg
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, load_state, log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class BlockerEntry:
    id: str
    name_pattern: str
    reason: str
    category: str  # backup | gaming | av | vpn | virtual_cd | encryption | edr | virt | storage
    soft_uninstall: bool
    # winget package id for compatible reinstall (None = manual / URL only)
    winget_id: str | None = None
    # Prefer this successor over winget_id when set (e.g. LGS → GHUB)
    winget_successor: str | None = None
    min_os_build_for_restore: int = 10240  # Win10+
    notes: str = ""


# Curated catalog — only soft_uninstall=True entries are auto-removed.
CATALOG: list[BlockerEntry] = [
    # --- Backup / imaging (SafeOS killers) ---
    BlockerEntry(
        "acronis",
        r"Acronis True Image|Acronis Cyber Protect|Acronis Backup",
        "Disk/VSS filter — SafeOS 0xC1900101",
        "backup",
        soft_uninstall=True,
        winget_id=None,  # no stable winget id; mark for manual / vendor URL
        notes="Reinstall latest Acronis True Image from vendor after Win11 (x64 only).",
    ),
    BlockerEntry(
        "easeus_todo",
        r"EaseUS Todo Backup",
        "Disk/VSS filter — SafeOS",
        "backup",
        soft_uninstall=True,
        winget_id="EaseUS.TodoBackup",
    ),
    BlockerEntry(
        "macrium",
        r"Macrium Reflect",
        "Disk/VSS filter — SafeOS",
        "backup",
        soft_uninstall=True,
        winget_id="Macrium.ReflectFree",
        notes="Home/Free winget id; paid Reflect: reinstall from Macrium account.",
    ),
    BlockerEntry(
        "aomei",
        r"AOMEI Backupper|AOMEI Partition",
        "Disk/VSS filter",
        "backup",
        soft_uninstall=True,
        winget_id="AOMEI.Backupper",
    ),
    BlockerEntry(
        "veeam_agent",
        r"Veeam Agent",
        "Disk/VSS filter",
        "backup",
        soft_uninstall=True,
        winget_id=None,
        notes="Reinstall Veeam Agent for Windows from Veeam after migration.",
    ),
    # --- Legacy gaming ---
    BlockerEntry(
        "logitech_lgs",
        r"Logitech Gaming Software",
        "Legacy LGS filters / 0xC1900101",
        "gaming",
        soft_uninstall=True,
        winget_id="Logitech.LGS",
        winget_successor="Logitech.GHUB",
        notes="Prefer Logitech G HUB on Win10/11; LGS only if GHUB unavailable.",
    ),
    # --- Virtual CD ---
    BlockerEntry(
        "daemon_tools",
        r"Daemon Tools",
        "Virtual CD filter (SPTD)",
        "virtual_cd",
        soft_uninstall=True,
        winget_id=None,
        notes="Avoid reinstalling SPTD stacks; use Explorer ISO mount when possible.",
    ),
    BlockerEntry(
        "alcohol120",
        r"Alcohol 120%",
        "Virtual CD filter",
        "virtual_cd",
        soft_uninstall=True,
        winget_id=None,
    ),
    # --- VPN (soft) ---
    BlockerEntry(
        "nordvpn",
        r"^NordVPN$",
        "VPN TAP/WFP filters",
        "vpn",
        soft_uninstall=True,
        winget_id="NordSecurity.NordVPN",
    ),
    BlockerEntry(
        "expressvpn",
        r"ExpressVPN",
        "VPN TAP/WFP filters",
        "vpn",
        soft_uninstall=True,
        winget_id="ExpressVPN.ExpressVPN",
    ),
    BlockerEntry(
        "wireguard",
        r"^WireGuard$",
        "VPN Wintun filter",
        "vpn",
        soft_uninstall=True,
        winget_id="WireGuard.WireGuard",
    ),
    # --- AV (warn by default; soft only with MAGIC_SOFT_UNINSTALL_AV=1) ---
    BlockerEntry(
        "avast",
        r"^Avast |Avast Free Antivirus|Avast Premier",
        "AV filter SafeOS",
        "av",
        soft_uninstall=False,
        winget_id="XPDNZMZZNM34KL",  # winget: Avast.Antivirus when available; may vary by source
        notes="Soft-uninstall AV only with MAGIC_SOFT_UNINSTALL_AV=1; else pause AV + restore manually.",
    ),
    BlockerEntry(
        "avg",
        r"^AVG |AVG Antivirus",
        "AV filter SafeOS",
        "av",
        soft_uninstall=False,
        winget_id="AVG.Antivirus",
    ),
    BlockerEntry(
        "malwarebytes",
        r"Malwarebytes",
        "AV filter SafeOS",
        "av",
        soft_uninstall=False,
        winget_id="Malwarebytes.Malwarebytes",
    ),
    BlockerEntry(
        "bitdefender",
        r"Bitdefender",
        "AV filter SafeOS",
        "av",
        soft_uninstall=False,
        winget_id=None,
    ),
    BlockerEntry(
        "kaspersky",
        r"Kaspersky",
        "AV filter SafeOS",
        "av",
        soft_uninstall=False,
        winget_id=None,
    ),
    BlockerEntry(
        "norton",
        r"Norton",
        "AV filter SafeOS",
        "av",
        soft_uninstall=False,
        winget_id=None,
    ),
    BlockerEntry(
        "mcafee",
        r"McAfee",
        "AV filter SafeOS",
        "av",
        soft_uninstall=False,
        winget_id=None,
    ),
    BlockerEntry(
        "eset",
        r"ESET ",
        "AV filter SafeOS",
        "av",
        soft_uninstall=False,
        winget_id=None,
    ),
    # --- EDR: never auto-uninstall ---
    BlockerEntry(
        "crowdstrike",
        r"CrowdStrike|Falcon",
        "EDR kernel — do not auto-uninstall",
        "edr",
        soft_uninstall=False,
        winget_id=None,
        notes="Contact IT / disable sensor policy; never soft-removed by this tool.",
    ),
    BlockerEntry(
        "sentinelone",
        r"SentinelOne|Sentinel Agent",
        "EDR kernel — do not auto-uninstall",
        "edr",
        soft_uninstall=False,
        winget_id=None,
    ),
    # --- Encryption: warn only ---
    BlockerEntry(
        "veracrypt",
        r"VeraCrypt|TrueCrypt",
        "Encryption filter — decrypt system volume first",
        "encryption",
        soft_uninstall=False,
        winget_id="IDRIX.VeraCrypt",
        notes="Never auto-uninstall if system encrypted; decrypt first, then optional restore.",
    ),
]


@dataclass
class InstalledMatch:
    catalog_id: str
    display_name: str
    publisher: str = ""
    version: str = ""
    uninstall_string: str = ""
    quiet_uninstall: str = ""
    install_location: str = ""
    category: str = ""
    reason: str = ""
    winget_id: str | None = None
    winget_successor: str | None = None
    soft_uninstall: bool = False
    notes: str = ""
    min_os_build_for_restore: int = 10240

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _run(cmd: list[str], *, timeout: int = 300) -> tuple[int, str]:
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
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return int(r.returncode), out
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT after {timeout}s"
    except OSError as e:
        return -1, str(e)


def _enum_uninstall_entries() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as root:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            def _q(name: str) -> str:
                                try:
                                    v, _ = winreg.QueryValueEx(k, name)
                                    return str(v or "")
                                except OSError:
                                    return ""

                            dn = _q("DisplayName")
                            if not dn:
                                continue
                            rows.append(
                                {
                                    "display_name": dn,
                                    "publisher": _q("Publisher"),
                                    "version": _q("DisplayVersion"),
                                    "uninstall_string": _q("UninstallString"),
                                    "quiet_uninstall": _q("QuietUninstallString"),
                                    "install_location": _q("InstallLocation"),
                                }
                            )
                    except OSError:
                        continue
        except OSError:
            continue
    return rows


def match_installed_blockers() -> list[InstalledMatch]:
    """Return catalog matches currently installed."""
    found: list[InstalledMatch] = []
    seen: set[str] = set()
    for row in _enum_uninstall_entries():
        name = row["display_name"]
        for entry in CATALOG:
            if not re.search(entry.name_pattern, name, re.I):
                continue
            key = f"{entry.id}|{name.lower()}"
            if key in seen:
                continue
            seen.add(key)
            found.append(
                InstalledMatch(
                    catalog_id=entry.id,
                    display_name=name,
                    publisher=row.get("publisher", ""),
                    version=row.get("version", ""),
                    uninstall_string=row.get("uninstall_string", ""),
                    quiet_uninstall=row.get("quiet_uninstall", ""),
                    install_location=row.get("install_location", ""),
                    category=entry.category,
                    reason=entry.reason,
                    winget_id=entry.winget_id,
                    winget_successor=entry.winget_successor,
                    soft_uninstall=entry.soft_uninstall,
                    notes=entry.notes,
                    min_os_build_for_restore=entry.min_os_build_for_restore,
                )
            )
            break
    return found


def _still_installed(display_name: str) -> bool:
    needle = display_name.lower()
    for row in _enum_uninstall_entries():
        if row["display_name"].lower() == needle:
            return True
        # fuzzy: same product family
        if needle[:20] and needle[:20] in row["display_name"].lower():
            return True
    return False


def _build_soft_uninstall_cmd(m: InstalledMatch) -> list[str] | None:
    raw = (m.quiet_uninstall or m.uninstall_string or "").strip()
    if not raw:
        return None
    # msiexec /I{GUID} → /X{GUID} /qn /norestart
    msi = re.search(r"msiexec\.exe\s+/[IiXx]\s*(\{[0-9A-Fa-f\-]+\})", raw, re.I)
    if msi:
        return ["msiexec.exe", "/X", msi.group(1), "/qn", "/norestart"]
    msi2 = re.search(r"(\{[0-9A-Fa-f\-]{36}\})", raw)
    if msi2 and "msiexec" in raw.lower():
        return ["msiexec.exe", "/X", msi2.group(1), "/qn", "/norestart"]

    # Quoted exe + args
    if raw.startswith('"'):
        parts = re.findall(r'"([^"]+)"|(\S+)', raw)
        tokens = [a or b for a, b in parts]
    else:
        tokens = raw.split()
    if not tokens:
        return None
    exe = tokens[0]
    args = tokens[1:]
    low = " ".join(args).lower()
    # Soft flags if not already quiet
    if "/quiet" not in low and "/qn" not in low and "/s" not in low.split() and "-silent" not in low:
        # NSIS / Inno common
        if exe.lower().endswith(".exe"):
            args = args + ["/S", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    return [exe] + args


def soft_uninstall_match(m: InstalledMatch) -> bool:
    cmd = _build_soft_uninstall_cmd(m)
    if not cmd:
        log(f"No uninstall string for {m.display_name} — skip soft remove", "WARN")
        return False
    log(f"Soft-uninstall: {m.display_name} → {cmd[0]} …", "STEP")
    code, out = _run(cmd, timeout=600)
    if code == 0 or not _still_installed(m.display_name):
        log(f"Soft-uninstalled: {m.display_name}", "OK")
        return True
    # msiexec 1605 = already removed; 3010 = reboot required
    if code in (1605, 3010):
        log(f"Soft-uninstall exit {code} for {m.display_name} (treat as OK)", "OK")
        return True
    log(f"Soft-uninstall incomplete ({m.display_name}) code={code}: {out[:200]}", "WARN")
    return False


def _pending_path() -> Path:
    return STATE_DIR / "pending-app-restores.json"


def _load_pending() -> list[dict[str, Any]]:
    p = _pending_path()
    if p.is_file():
        try:
            import json

            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (OSError, ValueError):
            pass
    st = load_state()
    raw = st.get("PendingAppRestores")
    return list(raw) if isinstance(raw, list) else []


def _save_pending(items: list[dict[str, Any]]) -> None:
    import json

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _pending_path().write_text(json.dumps(items, indent=2), encoding="utf-8")
    save_state({"PendingAppRestores": items, "PendingAppRestoreCount": len(items)})


def queue_restore(m: InstalledMatch) -> None:
    items = _load_pending()
    # dedupe by catalog_id
    items = [x for x in items if x.get("catalog_id") != m.catalog_id]
    items.append(
        {
            **m.as_dict(),
            "queued_at_build": int(load_state().get("HostBuildAtQueue") or 0),
            "status": "pending",
        }
    )
    _save_pending(items)


def find_winget() -> Path | None:
    candidates = [
        Path(os.environ.get("LocalAppData", "")) / "Microsoft" / "WindowsApps" / "winget.exe",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "winget.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    w = shutil.which("winget")
    return Path(w) if w else None


def _winget_install(pkg: str) -> bool:
    winget = find_winget()
    if winget is None:
        log("winget not available — cannot auto-restore packages", "WARN")
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
        timeout=900,
    )
    if code == 0:
        log(f"winget restored: {pkg}", "OK")
        return True
    # already installed
    if "already installed" in out.lower() or code == -1978335189:
        log(f"winget: {pkg} already present", "OK")
        return True
    log(f"winget install {pkg} failed ({code}): {out[:240]}", "WARN")
    return False


def process_blockers_before_upgrade(*, do_uninstall: bool = True) -> dict[str, Any]:
    """
    Detect blockers, soft-uninstall safe ones, queue restores.
    Called from migration patches / One-Click.
    """
    summary: dict[str, Any] = {
        "detected": [],
        "uninstalled": [],
        "warned": [],
        "queued_restore": [],
        "skipped": [],
        "carry": [],
    }
    if _env_true("MAGIC_SKIP_SOFT_UNINSTALL") and do_uninstall:
        log("MAGIC_SKIP_SOFT_UNINSTALL=1 — detect only", "INFO")
        do_uninstall = False

    allow_av = _env_true("MAGIC_SOFT_UNINSTALL_AV")
    matches = match_installed_blockers()
    save_state({"HostBuildAtQueue": _current_build()})

    # Windows product key + activation channel (local only; digital license usually survives)
    try:
        from .app_carry import capture_windows_product_key_local

        capture_windows_product_key_local()
    except Exception as e:
        log(f"Windows key carry snapshot: {e}", "WARN")

    log(f"=== Blocker catalog scan ({len(matches)} match(es)) ===", "STEP")
    for m in matches:
        summary["detected"].append(m.display_name)
        log(f"Blocker: {m.display_name} [{m.category}] — {m.reason}", "WARN")

        can_soft = m.soft_uninstall
        if m.category == "av":
            can_soft = allow_av
            if not allow_av:
                summary["warned"].append(m.display_name)
                log(
                    f"AV present ({m.display_name}): pause real-time protection for Setup. "
                    "Set MAGIC_SOFT_UNINSTALL_AV=1 to soft-uninstall + restore via winget after.",
                    "WARN",
                )
                continue
        if m.category == "edr":
            summary["warned"].append(m.display_name)
            log(f"EDR ({m.display_name}): NOT auto-uninstalled — {m.notes or m.reason}", "WARN")
            continue
        if m.category == "encryption":
            summary["warned"].append(m.display_name)
            log(f"Encryption app ({m.display_name}): decrypt first if system volume — {m.notes}", "WARN")
            # Still queue restore if later removed manually
            queue_restore(m)
            summary["queued_restore"].append(m.catalog_id)
            continue

        if not can_soft or not do_uninstall:
            summary["warned"].append(m.display_name)
            queue_restore(m)
            summary["queued_restore"].append(m.catalog_id)
            continue

        # Soft uninstall + queue restore (only if it was installed)
        queue_restore(m)
        summary["queued_restore"].append(m.catalog_id)
        try:
            from .app_carry import capture_app_carry

            carry = capture_app_carry(m.catalog_id, display_name=m.display_name)
            if carry.get("ok"):
                summary["carry"].append(m.catalog_id)
        except Exception as e:
            log(f"App carry capture ({m.catalog_id}): {e}", "WARN")
        if soft_uninstall_match(m):
            summary["uninstalled"].append(m.display_name)
        else:
            summary["skipped"].append(m.display_name)

    save_state(
        {
            "BlockerScan": {
                "detected": summary["detected"],
                "uninstalled": summary["uninstalled"],
                "warned": summary["warned"],
            }
        }
    )
    if summary["uninstalled"]:
        log(
            f"Soft-removed {len(summary['uninstalled'])} blocker(s); "
            "will restore compatible versions after migration.",
            "OK",
        )
    return summary


def _current_build() -> int:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        ) as k:
            v, _ = winreg.QueryValueEx(k, "CurrentBuildNumber")
            return int(v)
    except OSError:
        return 0


def _package_present_for_restore(item: dict[str, Any]) -> bool:
    """True if original or successor product already installed."""
    name = str(item.get("display_name") or "")
    if name and _still_installed(name):
        return True
    cid = item.get("catalog_id")
    for m in match_installed_blockers():
        if m.catalog_id == cid:
            return True
    # Successor product names
    succ = item.get("winget_successor") or ""
    if "GHUB" in succ.upper() or succ == "Logitech.GHUB":
        for row in _enum_uninstall_entries():
            if re.search(r"Logitech G HUB|LGHub", row["display_name"], re.I):
                return True
    return False


def restore_queued_apps(*, host_build: int | None = None) -> dict[str, Any]:
    """
    After migration: reinstall compatible packages for apps we soft-removed
    (or queued) if they are not already present.
    """
    result: dict[str, Any] = {
        "restored": [],
        "manual": [],
        "skipped": [],
        "failed": [],
        "carry_applied": [],
    }
    if _env_true("MAGIC_SKIP_APP_RESTORE"):
        log("MAGIC_SKIP_APP_RESTORE=1 — skip app restore", "INFO")
        return result

    build = host_build if host_build is not None else _current_build()
    items = _load_pending()
    if not items:
        log("No pending app restores", "OK")
        save_state({"AppRestoreDone": True})
        return result

    log(f"=== Restore compatible apps ({len(items)} queued) ===", "STEP")
    remaining: list[dict[str, Any]] = []

    def _apply_carry(catalog_id: str) -> None:
        try:
            from .app_carry import apply_app_carry

            applied = apply_app_carry(str(catalog_id))
            if applied.get("ok"):
                result["carry_applied"].append(catalog_id)
        except Exception as e:
            log(f"App carry apply ({catalog_id}): {e}", "WARN")

    for item in items:
        cid = item.get("catalog_id", "?")
        name = item.get("display_name", cid)
        min_b = int(item.get("min_os_build_for_restore") or 10240)
        if build < min_b:
            log(f"Defer restore {name}: need build>={min_b} (now {build})", "INFO")
            remaining.append(item)
            continue
        if _package_present_for_restore(item):
            log(f"Already present — skip package install, apply carried settings: {name}", "OK")
            result["skipped"].append(name)
            _apply_carry(str(cid))
            continue

        # Prefer successor (LGS → GHUB)
        pkgs: list[str] = []
        succ = item.get("winget_successor")
        primary = item.get("winget_id")
        if succ:
            pkgs.append(str(succ))
        if primary and primary not in pkgs:
            pkgs.append(str(primary))

        if not pkgs:
            note = item.get("notes") or "Reinstall manually from vendor."
            log(f"Manual restore required: {name} — {note}", "WARN")
            result["manual"].append({"name": name, "notes": note})
            # keep in remaining so SupportGuide can list it, but mark once
            item = dict(item)
            item["status"] = "manual"
            remaining.append(item)
            # Still try settings restore if user reinstalled manually later
            _apply_carry(str(cid))
            continue

        ok = False
        for pkg in pkgs:
            if _winget_install(pkg):
                ok = True
                result["restored"].append({"name": name, "package": pkg})
                _apply_carry(str(cid))
                break
        if not ok:
            result["failed"].append(name)
            item = dict(item)
            item["status"] = "failed"
            remaining.append(item)

    _save_pending(remaining)
    save_state(
        {
            "AppRestoreDone": len(remaining) == 0
            or all(x.get("status") == "manual" for x in remaining),
            "AppRestoreResult": result,
        }
    )
    # Write human checklist for manual restores
    try:
        guide = STATE_DIR / "AppRestoreGuide.txt"
        lines = [
            "Win11 Magic Upgrade — App restore guide",
            "=" * 40,
            f"Host build: {build}",
            "",
        ]
        if result["restored"]:
            lines.append("Restored via winget:")
            for r in result["restored"]:
                lines.append(f"  - {r['name']} → {r['package']}")
            lines.append("")
        if result.get("carry_applied"):
            lines.append("Activation keys / registry / settings re-applied (when compatible):")
            for cid in result["carry_applied"]:
                lines.append(f"  - {cid}")
            lines.append(
                "  (Stored only under %LOCALAPPDATA%\\Win11MagicUpgrade\\app-carry\\ — never in Support ZIP)"
            )
            lines.append("")
        if result["manual"]:
            lines.append("Manual reinstall (no winget package / vendor portal):")
            for r in result["manual"]:
                lines.append(f"  - {r['name']}: {r['notes']}")
            lines.append("")
        if result["failed"]:
            lines.append("winget failed — retry later:")
            for n in result["failed"]:
                lines.append(f"  - {n}")
        guide.write_text("\n".join(lines), encoding="utf-8")
        desk = Path.home() / "Desktop" / "Win11MagicUpgrade-AppRestore.txt"
        try:
            desk.write_text(guide.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
        log(f"App restore guide: {guide}", "OK")
    except OSError as e:
        log(f"App restore guide write: {e}", "WARN")
    return result


def pending_restores_remaining() -> bool:
    items = _load_pending()
    return any(x.get("status", "pending") == "pending" for x in items)


def format_blocker_report(matches: list[InstalledMatch] | None = None) -> str:
    matches = matches if matches is not None else match_installed_blockers()
    if not matches:
        return "No catalogued blocker apps detected."
    lines = ["Installed blocker catalog matches:"]
    for m in matches:
        action = "soft-uninstall+restore" if m.soft_uninstall else "warn-only"
        if m.category == "av":
            action = "warn (MAGIC_SOFT_UNINSTALL_AV=1 to remove)"
        if m.category == "edr":
            action = "never-auto-uninstall"
        lines.append(f"  - {m.display_name} [{m.category}] ({action}) — {m.reason}")
    return "\n".join(lines)
