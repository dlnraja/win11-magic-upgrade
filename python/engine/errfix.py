"""Extra migration error fixes (SetupDiag / MS Support / forums).

Covers gaps beyond the core patches module:
  - CompatData / Appraiser blocking apps (0xC1900208)
  - Duplicate / broken user profiles (SetupDiag DuplicateUserProfileFailure)
  - Safe Mode / Audit Mode hardblocks
  - VHD / Portable Workspace hardblocks
  - Windows Update service + cache reset (0x80070002 / 0x80240034 / 0x80070422)
  - Dirty volume / offline files / secondary disks
  - Delivery Optimization + SoftwareDistribution cleanup
  - CrowdStrike / more EDR service stops
  - Long path / profile path warnings
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import winreg
import xml.etree.ElementTree as ET
from pathlib import Path

from .logutil import log

# Expanded error fingerprints for prior-log scanning
EXTRA_ERROR_PATTERNS = (
    r"0xC1900223|0x80070002|0x80070003|0x80070005|0x8007000[DdEe]|"
    r"0x80070020|0x80070422|0x800705[Bb]4|0x80072[Ee][Ff][Ee]|"
    r"0x80073712|0x800F0922|0x80240034|0x80246007|0x80248014|"
    r"0xC1900101\s*-\s*0x[0-9A-Fa-f]+|DuplicateUserProfile|CompatBlocked|"
    r"BlockMigration|DT_ANY_FMC_BlockingApplication|InsufficientSystemPartition|"
    r"BitLockerHardblock|VHDHardblock|AuditMode|SafeModeHardblock|"
    r"InstallPathTooLong|OfflineFiles|CrowdStrike|CSAgent|"
    r"ERROR_ALREADY_EXISTS|AttachVirtualDisk failed:\s*183"
)


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return str(e)


def detect_safe_or_audit_mode() -> None:
    """SetupDiag: SafeModeHardblock / AuditModeHardblock."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\SafeBoot\Option",
        ):
            log("SAFE MODE detected - exit Safe Mode before upgrading (SetupDiag SafeModeHardblock)", "ERROR")
    except OSError:
        pass
    # Audit mode: ImageState or SetupType
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Setup\State") as k:
            state, _ = winreg.QueryValueEx(k, "ImageState")
            if "AUDIT" in str(state).upper():
                log(f"Audit mode ImageState={state} - exit audit mode before upgrade", "ERROR")
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\Setup") as k:
            st, _ = winreg.QueryValueEx(k, "AuditBoot")
            if int(st) != 0:
                log("AuditBoot set - SetupDiag AuditModeHardblock risk", "ERROR")
    except OSError:
        pass


def detect_vhd_boot() -> None:
    """SetupDiag VHDHardblock - Windows on VHD often blocks feature upgrades."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\SystemInformation",
        ) as k:
            # Not always present; also check SystemProductName / firmware
            pass
    except OSError:
        pass
    # Physical vs virtual disk for system drive
    out = _run(["wmic", "logicaldisk", "where", "DeviceID='C:'", "get", "ProviderName,Description"])
    if re.search(r"Virtual|VHD|differencing", out, re.I):
        log("System appears on virtual/VHD media - feature upgrades often blocked (VHDHardblock)", "WARN")
    # Mountvol / diskpart style: check if C: is on a VHD via Get-Disk - use fsutil
    out2 = _run(["fsutil", "fsinfo", "ntfsinfo", "C:"])
    if "VHD" in out2.upper():
        log("NTFS reports VHD characteristics on C: - upgrade may hard-block", "WARN")


def detect_duplicate_user_profiles() -> None:
    """SetupDiag DuplicateUserProfileFailure - multiple SIDs / broken ProfileList."""
    base = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            paths: dict[str, list[str]] = {}
            i = 0
            while True:
                try:
                    sid = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break
                if not sid.startswith("S-1-5-21-"):
                    continue
                try:
                    with winreg.OpenKey(root, sid) as k:
                        img, _ = winreg.QueryValueEx(k, "ProfileImagePath")
                        img_s = str(img).lower()
                        paths.setdefault(img_s, []).append(sid)
                        # Missing folder
                        expanded = os.path.expandvars(str(img))
                        if not Path(expanded).exists():
                            log(
                                f"Broken profile entry SID={sid} path missing: {img} "
                                "(can cause DuplicateUserProfile / MIG failures)",
                                "WARN",
                            )
                except OSError:
                    continue
            for path, sids in paths.items():
                if len(sids) > 1:
                    log(
                        f"Duplicate ProfileList path {path} used by {len(sids)} SIDs: {', '.join(sids[:4])} "
                        "- backup/remove unused accounts before upgrade (SetupDiag)",
                        "WARN",
                    )
    except OSError as e:
        log(f"ProfileList scan skipped: {e}", "INFO")


def scan_compatdata_blockers() -> None:
    """Parse CompatData / Appraiser XML for 0xC1900208 blocking apps."""
    roots = [
        Path(r"C:\$WINDOWS.~BT\Sources\Panther"),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Panther",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "appcompat" / "appraiser",
    ]
    found = 0
    for root in roots:
        if not root.is_dir():
            continue
        for pat in ("CompatData*.xml", "*APPRAISER*HumanReadable*.xml", "*Appraiser*.xml"):
            for xml_path in root.glob(pat):
                try:
                    text = xml_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                # Fast string hits before full XML parse
                if not re.search(
                    r"BlockMigration\s*=\s*True|DT_ANY_FMC_BlockingApplication\s*=\s*True|HardBlock",
                    text,
                    re.I,
                ):
                    continue
                # Extract names near BlockMigration
                for m in re.finditer(
                    r"(?:Name|DisplayName|Title|LowerCaseLongPathUnexpanded)[^<]*>([^<]{2,120})",
                    text,
                    re.I,
                ):
                    name = m.group(1).strip()
                    if len(name) < 3 or name.lower() in {"true", "false", "yes", "no"}:
                        continue
                    # Only log if near a block marker within 500 chars
                    idx = m.start()
                    window = text[max(0, idx - 400) : idx + 400]
                    if re.search(r"BlockMigration|BlockingApplication|HardBlock", window, re.I):
                        log(f"Compat block artifact ({xml_path.name}): {name}", "WARN")
                        found += 1
                        if found >= 25:
                            log("More compat blockers may exist - see CompatData XML in Panther", "WARN")
                            return
                # ElementTree for BlockMigration attributes
                try:
                    # CompatData can be huge; only parse if not enormous
                    if xml_path.stat().st_size > 8_000_000:
                        continue
                    tree = ET.parse(xml_path)
                    for el in tree.iter():
                        attrib = {k.lower(): v for k, v in el.attrib.items()}
                        joined = " ".join(f"{k}={v}" for k, v in attrib.items())
                        if re.search(r"blockmigration.?=.?true|blockingapplication.?=.?true", joined, re.I):
                            name = (
                                attrib.get("name")
                                or attrib.get("displayname")
                                or attrib.get("title")
                                or el.text
                                or xml_path.name
                            )
                            log(f"CompatData BlockMigration: {str(name)[:120]}", "WARN")
                            found += 1
                except Exception:
                    pass
    if found:
        log(f"Found {found} compatibility block hints (0xC1900208) — neutralizing...", "WARN")
        try:
            from .compat import neutralize_compatdata_blocks

            neutralize_compatdata_blocks()
        except Exception as e:
            log(f"Compat neutralize skipped: {e}", "WARN")
    else:
        log("No CompatData BlockMigration hits in local Panther/appraiser caches", "OK")


def reset_windows_update_components(*, rename_catroot2: bool = False) -> None:
    """Mitigate 0x80070002 / 0x80240034 / 0x80070422 / stuck WU downloads."""
    log("Resetting Windows Update components (safe soft reset)...", "STEP")
    services = ["wuauserv", "bits", "cryptsvc", "dosvc", "UsoSvc"]
    for svc in services:
        _run(["net", "stop", svc])
    # Clear download caches (not the whole DataStore if locked)
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for rel in (
        windir / "SoftwareDistribution" / "Download",
        windir / "SoftwareDistribution" / "DataStore" / "Logs",
    ):
        if not rel.exists():
            continue
        try:
            for child in list(rel.iterdir())[:2000]:
                try:
                    if child.is_file():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                except Exception:
                    pass
            log(f"Cleared {rel}", "OK")
        except Exception as e:
            log(f"WU cleanup {rel.name}: {e}", "INFO")

    if rename_catroot2:
        rel = windir / "System32" / "catroot2"
        if rel.exists():
            try:
                bak = rel.with_name("catroot2.bak-magic")
                if bak.exists():
                    shutil.rmtree(bak, ignore_errors=True)
                rel.rename(bak)
                log("Renamed catroot2 -> catroot2.bak-magic", "OK")
            except Exception as e:
                log(f"catroot2 rename skipped: {e}", "INFO")
    else:
        log("Skip catroot2 rename (stability)", "OK")

    # Ensure services are set to auto/manual and start
    for svc, start in (("wuauserv", "demand"), ("bits", "delayed-auto"), ("cryptsvc", "auto")):
        _run(["sc", "config", svc, f"start= {start}"])
    for svc in services:
        _run(["net", "start", svc])
    log("Windows Update services restarted", "OK")


def ensure_critical_services() -> None:
    """0x80070422 and friends - WU/BITS/TrustedInstaller must run."""
    for svc in ("wuauserv", "bits", "TrustedInstaller", "RpcSs", "EventLog", "DeviceInstall"):
        q = _run(["sc", "query", svc])
        if "RUNNING" not in q.upper():
            log(f"Starting service {svc}", "WARN")
            _run(["sc", "start", svc])


def check_volume_dirty() -> None:
    """Dirty bit can cause 0x8007001F / SafeOS disk errors."""
    drive = os.environ.get("SystemDrive", "C:")
    out = _run(["fsutil", "dirty", "query", drive])
    if re.search(r"is dirty|Volume .* is dirty", out, re.I):
        log(
            f"{drive} has DIRTY bit set - schedule chkdsk before upgrade (reboot may be required)",
            "WARN",
        )
        # Non-interactive schedule
        _run(["chkdsk", drive, "/F"])
        log("chkdsk /F scheduled (completes on next reboot if volume in use)", "WARN")
    else:
        log(f"Volume dirty check OK ({drive})", "OK")


def disable_offline_files_temporarily() -> None:
    """Offline Files / CSC filters sometimes break MIG / SafeOS."""
    out = _run(["sc", "query", "CscService"])
    if "RUNNING" in out.upper():
        log("Stopping Offline Files (CscService) for upgrade stability", "WARN")
        _run(["sc", "stop", "CscService"])


def warn_secondary_fixed_disks() -> None:
    """0x80070002-0x20009: disconnect non-target disks when Setup looks for files."""
    out = _run(["wmic", "diskdrive", "get", "Index,Model,Size,MediaType,InterfaceType"])
    lines = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.lower().startswith("index")]
    if len(lines) > 1:
        log(
            f"Multiple physical disks detected ({len(lines)}) - if Setup fails with 0x80070002, "
            "temporarily disconnect non-system disks/USB enclosures",
            "WARN",
        )


def warn_long_profile_paths() -> None:
    """InstallPathTooLong / deep profile paths break MIG."""
    users = Path(os.environ.get("SystemDrive", "C:")) / "Users"
    if not users.is_dir():
        return
    long_hits = 0
    for root, dirs, files in os.walk(users):
        # Limit walk cost
        if long_hits >= 5:
            break
        if len(root) > 200:
            log(f"Very long path under Users (MIG risk): {root[:180]}...", "WARN")
            long_hits += 1
            dirs.clear()
            continue
        # shallow only first two levels for speed if deep
        depth = root[len(str(users)) :].count(os.sep)
        if depth > 4:
            dirs.clear()
    if not long_hits:
        log("No extreme Users path lengths detected", "OK")


def stop_extra_blocker_services() -> None:
    """More EDR/backup/filter services than the base list."""
    out = _run(["sc", "query", "type=", "service", "state=", "all"])
    names = re.findall(r"SERVICE_NAME:\s+(\S+)", out)
    pat = re.compile(
        r"crowdstrike|csagent|csfalconservice|sentinel|cbdefense|cylance|"
        r"symantec|sep|sisidservice|sisips|"
        r"shadowprotect|storagecraft|replica|"
        r"teamviewer|anydesk|"
        r"docker|com\.docker|"
        r"igfx|jhi_service|lms",
        re.I,
    )
    for svc in names:
        if pat.search(svc):
            log(f"Stopping extra blocker service {svc}", "WARN")
            _run(["sc", "stop", svc])


def cleanup_delivery_optimization() -> None:
    do_path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "ServiceProfiles" / "NetworkService" / "AppData" / "Local" / "Microsoft" / "Windows" / "DeliveryOptimization" / "Cache"
    if do_path.is_dir():
        n = 0
        for f in list(do_path.rglob("*"))[:500]:
            if f.is_file():
                try:
                    f.unlink()
                    n += 1
                except Exception:
                    pass
        if n:
            log(f"Cleared {n} Delivery Optimization cache files", "OK")


def expand_prior_error_scan() -> None:
    """Scan more logs for expanded error codes."""
    paths = [
        Path(r"C:\$WINDOWS.~BT\Sources\Panther\setuperr.log"),
        Path(r"C:\$WINDOWS.~BT\Sources\Rollback\setuperr.log"),
        Path(r"C:\$WINDOWS.~BT\Sources\Rollback\setupact.err"),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Logs" / "MoSetup" / "BlueBox.log",
    ]
    for p in paths:
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[-300_000:]
            hits = sorted(set(re.findall(EXTRA_ERROR_PATTERNS, text, re.I)))
            if hits:
                log(f"Extra prior errors in {p.name}: {', '.join(hits[:15])}", "WARN")
        except Exception:
            pass


def apply_extra_error_fixes(*, soft_wu_reset: bool = True) -> None:
    log("=== Extra error fixes (SetupDiag / MS Support catalog) ===", "STEP")
    expand_prior_error_scan()
    detect_safe_or_audit_mode()
    detect_vhd_boot()
    detect_duplicate_user_profiles()
    scan_compatdata_blockers()
    warn_secondary_fixed_disks()
    warn_long_profile_paths()
    check_volume_dirty()
    disable_offline_files_temporarily()
    stop_extra_blocker_services()
    ensure_critical_services()
    cleanup_delivery_optimization()
    if soft_wu_reset:
        reset_windows_update_components(rename_catroot2=False)
    else:
        log("Skip WU soft reset on resume (stability)", "OK")
    log("Extra error fixes done.", "OK")
