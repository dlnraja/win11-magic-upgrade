"""
Carry activation keys + registry/settings across soft-uninstall → Win11-compatible reinstall.

Before uninstall: export known HKLM/HKCU trees, license value names, and AppData folders
into %LOCALAPPDATA%\\Win11MagicUpgrade\\app-carry\\<id>\\

After winget restore: re-import .reg (best-effort), restore userdata, rewrite license
values when the new product still uses compatible registry paths.

Local-only secrets: licenses.json is NEVER copied to Desktop Support ZIP / GitHub reports.
Disable: MAGIC_SKIP_APP_CARRY=1
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import winreg
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logutil import STATE_DIR, log, save_state

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CARRY_ROOT = STATE_DIR / "app-carry"

# Value name patterns that often hold serials / license blobs (captured locally only)
_LICENSE_NAME_RE = re.compile(
    r"(serial|license|licence|product.?key|activation|regist(er|ration)|"
    r"unlock|cd.?key|pkey|install.?key|volume.?key)",
    re.I,
)


@dataclass
class CarryProfile:
    catalog_id: str
    # Registry paths relative to HKLM or HKCU (prefix HKLM\ or HKCU\)
    reg_paths: list[str] = field(default_factory=list)
    # Extra AppData-relative folders (expanded with env)
    userdata_dirs: list[str] = field(default_factory=list)
    # Successor note when LGS → GHUB etc.
    notes: str = ""


# Curated carry maps (paths that commonly survive vendor upgrades)
CARRY_PROFILES: dict[str, CarryProfile] = {
    "acronis": CarryProfile(
        "acronis",
        reg_paths=[
            r"HKLM\SOFTWARE\Acronis",
            r"HKLM\SOFTWARE\WOW6432Node\Acronis",
            r"HKCU\SOFTWARE\Acronis",
        ],
        userdata_dirs=[
            r"%ProgramData%\Acronis",
            r"%AppData%\Acronis",
            r"%LocalAppData%\Acronis",
        ],
        notes="Acronis often re-binds license to account; registry serial restored when path matches.",
    ),
    "easeus_todo": CarryProfile(
        "easeus_todo",
        reg_paths=[
            r"HKLM\SOFTWARE\EaseUS",
            r"HKLM\SOFTWARE\WOW6432Node\EaseUS",
            r"HKCU\SOFTWARE\EaseUS",
        ],
        userdata_dirs=[
            r"%AppData%\EaseUS",
            r"%LocalAppData%\EaseUS",
            r"%ProgramData%\EaseUS",
        ],
    ),
    "macrium": CarryProfile(
        "macrium",
        reg_paths=[
            r"HKLM\SOFTWARE\Macrium",
            r"HKLM\SOFTWARE\WOW6432Node\Macrium",
            r"HKCU\SOFTWARE\Macrium",
        ],
        userdata_dirs=[
            r"%AppData%\Macrium",
            r"%ProgramData%\Macrium",
        ],
        notes="Paid Reflect licenses are often account-tied; Free winget restore may not need a key.",
    ),
    "aomei": CarryProfile(
        "aomei",
        reg_paths=[
            r"HKLM\SOFTWARE\AOMEI Tech Co.",
            r"HKLM\SOFTWARE\WOW6432Node\AOMEI Tech Co.",
            r"HKCU\SOFTWARE\AOMEI Tech Co.",
            r"HKLM\SOFTWARE\AOMEI",
            r"HKLM\SOFTWARE\WOW6432Node\AOMEI",
        ],
        userdata_dirs=[r"%AppData%\AOMEI", r"%ProgramData%\AOMEI"],
    ),
    "logitech_lgs": CarryProfile(
        "logitech_lgs",
        reg_paths=[
            r"HKLM\SOFTWARE\Logitech",
            r"HKLM\SOFTWARE\WOW6432Node\Logitech",
            r"HKCU\SOFTWARE\Logitech",
        ],
        userdata_dirs=[
            # LGS legacy
            r"%AppData%\Logitech\Logitech Gaming Software",
            r"%LocalAppData%\Logitech\Logitech Gaming Software",
            # G HUB successor (restore into these after GHUB install)
            r"%AppData%\LGHUB",
            r"%LocalAppData%\LGHUB",
            r"%AppData%\G HUB",
            r"%AppData%\lghub",
            r"%ProgramData%\LGHUB",
        ],
        notes="LGS→GHUB: settings folders restored when compatible; GHUB may rebuild profiles.",
    ),
    "nordvpn": CarryProfile(
        "nordvpn",
        reg_paths=[r"HKCU\SOFTWARE\NordVPN", r"HKLM\SOFTWARE\NordVPN"],
        userdata_dirs=[r"%LocalAppData%\NordVPN", r"%AppData%\NordVPN"],
        notes="VPN accounts usually re-login; tokens may be account-cloud only.",
    ),
    "expressvpn": CarryProfile(
        "expressvpn",
        reg_paths=[r"HKCU\SOFTWARE\ExpressVPN", r"HKLM\SOFTWARE\ExpressVPN"],
        userdata_dirs=[r"%LocalAppData%\ExpressVPN", r"%AppData%\ExpressVPN"],
    ),
    "wireguard": CarryProfile(
        "wireguard",
        reg_paths=[r"HKLM\SOFTWARE\WireGuard", r"HKCU\SOFTWARE\WireGuard"],
        userdata_dirs=[r"%ProgramFiles%\WireGuard\Data", r"%ProgramData%\WireGuard"],
        notes="WireGuard tunnel configs carried when Data folder restore succeeds.",
    ),
    "malwarebytes": CarryProfile(
        "malwarebytes",
        reg_paths=[
            r"HKLM\SOFTWARE\Malwarebytes",
            r"HKLM\SOFTWARE\WOW6432Node\Malwarebytes",
            r"HKCU\SOFTWARE\Malwarebytes",
        ],
        userdata_dirs=[r"%ProgramData%\Malwarebytes", r"%AppData%\Malwarebytes"],
    ),
    "veracrypt": CarryProfile(
        "veracrypt",
        reg_paths=[r"HKLM\SOFTWARE\VeraCrypt", r"HKCU\SOFTWARE\VeraCrypt"],
        userdata_dirs=[],
        notes="Never auto-copy system-encryption state; user must decrypt first.",
    ),
}


def _env_skip() -> bool:
    return os.environ.get("MAGIC_SKIP_APP_CARRY", "").strip().lower() in ("1", "true", "yes")


def carry_dir(catalog_id: str) -> Path:
    d = CARRY_ROOT / catalog_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hive_and_sub(path: str) -> tuple[int, str] | None:
    p = path.replace("/", "\\").strip()
    if p.upper().startswith("HKLM\\"):
        return winreg.HKEY_LOCAL_MACHINE, p[5:]
    if p.upper().startswith("HKCU\\"):
        return winreg.HKEY_CURRENT_USER, p[5:]
    if p.upper().startswith("HKEY_LOCAL_MACHINE\\"):
        return winreg.HKEY_LOCAL_MACHINE, p[len("HKEY_LOCAL_MACHINE\\") :]
    if p.upper().startswith("HKEY_CURRENT_USER\\"):
        return winreg.HKEY_CURRENT_USER, p[len("HKEY_CURRENT_USER\\") :]
    return None


def _export_reg(path: str, dest_reg: Path) -> bool:
    """Export via reg.exe (Unicode)."""
    parsed = _hive_and_sub(path)
    if not parsed:
        return False
    # reg export needs full path like HKLM\SOFTWARE\...
    full = path if path.upper().startswith("HK") else path
    try:
        r = subprocess.run(
            ["reg", "export", full, str(dest_reg), "/y"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode == 0 and dest_reg.is_file()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _import_reg(reg_file: Path) -> bool:
    try:
        r = subprocess.run(
            ["reg", "import", str(reg_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _walk_license_values(hive: int, sub: str, out: dict[str, str], *, depth: int = 0) -> None:
    if depth > 6:
        return
    try:
        with winreg.OpenKey(hive, sub) as k:
            i = 0
            while True:
                try:
                    name, val, typ = winreg.EnumValue(k, i)
                    i += 1
                except OSError:
                    break
                if not _LICENSE_NAME_RE.search(str(name)):
                    continue
                if typ in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                    s = str(val or "").strip()
                    if 4 <= len(s) <= 256:
                        out[f"{sub}\\{name}"] = s
                elif typ == winreg.REG_DWORD:
                    out[f"{sub}\\{name}"] = str(int(val))
    except OSError:
        return
    try:
        with winreg.OpenKey(hive, sub) as k:
            i = 0
            while True:
                try:
                    child = winreg.EnumKey(k, i)
                    i += 1
                except OSError:
                    break
                _walk_license_values(hive, sub + "\\" + child, out, depth=depth + 1)
    except OSError:
        return


def _collect_licenses(reg_paths: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in reg_paths:
        parsed = _hive_and_sub(path)
        if not parsed:
            continue
        hive, sub = parsed
        _walk_license_values(hive, sub, found)
    return found


def _set_reg_sz(hive: int, path: str, name: str, value: str) -> bool:
    try:
        key = winreg.CreateKeyEx(hive, path, 0, winreg.KEY_SET_VALUE)
        with key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        return True
    except OSError:
        return False


def _backup_userdata(dirs: list[str], dest_root: Path) -> list[str]:
    saved: list[str] = []
    ud = dest_root / "userdata"
    ud.mkdir(parents=True, exist_ok=True)
    for raw in dirs:
        src = Path(os.path.expandvars(raw))
        if not src.exists():
            continue
        # Cap size: skip huge cache trees (>500 MB)
        try:
            if src.is_file():
                size = src.stat().st_size
            else:
                size = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
            if size > 500 * 1024 * 1024:
                log(f"Carry skip large userdata ({size // (1024**2)} MB): {src}", "WARN")
                continue
        except OSError:
            continue
        target = ud / re.sub(r"[^\w.\-]+", "_", raw.replace("%", ""))
        try:
            if src.is_dir():
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(src, target, dirs_exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
            saved.append(raw)
            # Remember mapping for restore
            meta = dest_root / "userdata_map.json"
            mapping = {}
            if meta.is_file():
                try:
                    mapping = json.loads(meta.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    mapping = {}
            mapping[str(target.name)] = raw
            meta.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
        except OSError as e:
            log(f"Carry userdata skip {src}: {e}", "WARN")
    return saved


def _restore_userdata(dest_root: Path) -> int:
    meta = dest_root / "userdata_map.json"
    if not meta.is_file():
        return 0
    try:
        mapping = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    ud = dest_root / "userdata"
    for name, raw in mapping.items():
        src = ud / name
        dst = Path(os.path.expandvars(raw))
        if not src.exists():
            continue
        try:
            if src.is_dir():
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    # Merge: copy files without wiping newer vendor defaults entirely
                    for f in src.rglob("*"):
                        if f.is_file():
                            rel = f.relative_to(src)
                            out = dst / rel
                            out.parent.mkdir(parents=True, exist_ok=True)
                            if not out.exists():
                                shutil.copy2(f, out)
                else:
                    shutil.copytree(src, dst)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            n += 1
        except OSError as e:
            log(f"Restore userdata {raw}: {e}", "WARN")
    return n


def capture_app_carry(catalog_id: str, *, display_name: str = "") -> dict[str, Any]:
    """Snapshot licenses + registry + userdata BEFORE soft-uninstall."""
    result: dict[str, Any] = {
        "catalog_id": catalog_id,
        "display_name": display_name,
        "reg_exported": 0,
        "licenses": 0,
        "userdata": [],
        "ok": False,
    }
    if _env_skip():
        log("MAGIC_SKIP_APP_CARRY=1 — skip license/settings capture", "INFO")
        return result
    prof = CARRY_PROFILES.get(catalog_id)
    if prof is None:
        # Generic: still try uninstall entry publisher tree if empty
        result["notes"] = "no carry profile"
        return result

    root = carry_dir(catalog_id)
    # Registry exports
    reg_dir = root / "reg"
    reg_dir.mkdir(exist_ok=True)
    for i, path in enumerate(prof.reg_paths):
        dest = reg_dir / f"export_{i}.reg"
        if _export_reg(path, dest):
            result["reg_exported"] += 1
            log(f"Carry registry: {path}", "OK")

    licenses = _collect_licenses(prof.reg_paths)
    if licenses:
        lic_path = root / "licenses.json"
        lic_path.write_text(json.dumps(licenses, indent=2), encoding="utf-8")
        try:
            # Restrict ACL best-effort: remove inheritance not portable without win32security
            os.chmod(lic_path, 0o600)
        except OSError:
            pass
        result["licenses"] = len(licenses)
        log(
            f"Carry captured {len(licenses)} license/serial value(s) for {catalog_id} "
            "(local only — not uploaded)",
            "OK",
        )

    result["userdata"] = _backup_userdata(prof.userdata_dirs, root)
    if result["userdata"]:
        log(f"Carry userdata folders: {len(result['userdata'])}", "OK")

    meta = {
        "catalog_id": catalog_id,
        "display_name": display_name,
        "notes": prof.notes,
        "reg_exported": result["reg_exported"],
        "license_count": result["licenses"],
        "userdata": result["userdata"],
    }
    (root / "carry_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    result["ok"] = result["reg_exported"] > 0 or result["licenses"] > 0 or bool(result["userdata"])
    save_state({f"AppCarry_{catalog_id}": {"ok": result["ok"], "licenses": result["licenses"]}})
    return result


def apply_app_carry(catalog_id: str) -> dict[str, Any]:
    """After reinstall: import .reg, restore userdata, rewrite license values."""
    result: dict[str, Any] = {
        "catalog_id": catalog_id,
        "reg_imported": 0,
        "licenses_written": 0,
        "userdata_restored": 0,
        "ok": False,
    }
    if _env_skip():
        return result
    root = CARRY_ROOT / catalog_id
    if not root.is_dir():
        return result

    log(f"=== Apply carried keys/settings for {catalog_id} ===", "STEP")
    reg_dir = root / "reg"
    if reg_dir.is_dir():
        for regf in sorted(reg_dir.glob("*.reg")):
            if _import_reg(regf):
                result["reg_imported"] += 1
                log(f"Imported {regf.name}", "OK")
            else:
                log(f"Registry import failed (may be obsolete path): {regf.name}", "WARN")

    lic_path = root / "licenses.json"
    if lic_path.is_file():
        try:
            licenses = json.loads(lic_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            licenses = {}
        for full, value in licenses.items():
            # full like SOFTWARE\Acronis\...\Serial
            if "\\" not in full:
                continue
            sub, name = full.rsplit("\\", 1)
            # Prefer HKLM then HKCU
            wrote = _set_reg_sz(winreg.HKEY_LOCAL_MACHINE, sub, name, value)
            if not wrote:
                wrote = _set_reg_sz(winreg.HKEY_CURRENT_USER, sub, name, value)
            if wrote:
                result["licenses_written"] += 1
        if result["licenses_written"]:
            log(
                f"Re-applied {result['licenses_written']} license/serial value(s) for {catalog_id}",
                "OK",
            )
        else:
            log(
                f"License values for {catalog_id} not writable on new paths — "
                "re-enter key in the app if activation fails",
                "WARN",
            )

    result["userdata_restored"] = _restore_userdata(root)
    if result["userdata_restored"]:
        log(f"Restored {result['userdata_restored']} userdata tree(s)", "OK")

    result["ok"] = (
        result["reg_imported"] > 0
        or result["licenses_written"] > 0
        or result["userdata_restored"] > 0
    )
    save_state({f"AppCarryApply_{catalog_id}": result})
    return result


def capture_windows_product_key_local() -> dict[str, Any]:
    """
    Save Windows BackupProductKeyDefault locally (inplace usually keeps digital license).
    Never placed on Desktop / Support ZIP.
    """
    out: dict[str, Any] = {"key_present": False, "partial": ""}
    if _env_skip():
        return out
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SoftwareProtectionPlatform",
        ) as k:
            try:
                key, _ = winreg.QueryValueEx(k, "BackupProductKeyDefault")
                key = str(key or "").strip()
            except OSError:
                key = ""
    except OSError:
        key = ""
    root = carry_dir("_windows")
    if key and len(key) >= 10:
        (root / "windows-product-key.txt").write_text(key + "\n", encoding="utf-8")
        try:
            os.chmod(root / "windows-product-key.txt", 0o600)
        except OSError:
            pass
        out["key_present"] = True
        out["partial"] = key[-5:]
        log(
            f"Windows product key saved locally (***{out['partial']}) — "
            "digital entitlement usually survives inplace; key kept for emergency re-entry only",
            "OK",
        )
    else:
        log("No BackupProductKeyDefault in registry (digital license / OEM may still activate)", "INFO")
    # Also snapshot channel via existing activation helper
    try:
        from .activation import snapshot_activation

        snapshot_activation(label="pre_carry")
    except Exception:
        pass
    save_state({"WindowsKeyCarry": {"present": out["key_present"], "partial": out["partial"]}})
    return out
