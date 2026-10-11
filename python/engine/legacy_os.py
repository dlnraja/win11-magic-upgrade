"""
Legacy Windows host support (Vista / 7 / 8 / 8.1) and Media Center Key Edition.

Research-backed notes (forums + Microsoft docs):
  - Win7 SP1 / Win8 / Win8.1: in-place upgrade to Win10 22H2 keeps apps/files.
  - Win8.1 Pro + Media Center: Setup blocks "different edition" — fix with
    sources\\ei.cfg + pid.txt (force Professional) and setupprep.exe.
  - Windows DVD Player free offer (KB3106246 era): only after *in-place* upgrade
    from Win7 Home Premium/Pro/Ultimate or Win8/8.1 Pro with Media Center.
    Clean install voids eligibility. Offer was time-limited; we still do inplace,
    persist eligibility markers across reboots, and nudge WU/Store after Win10.
  - Win8 → Win8.1 hop: optional when MAGIC_WIN81_ISO (or local path) is set;
    otherwise Win8 goes direct to Win10 22H2 (CDN still serves that ISO).
  - Vista: no supported in-place jump; best-effort Win10 22H2.
  - Registry: AllowOSUpgrade, Setup\\Compact (8.x), MoSetup on Win10+ hops.
"""
from __future__ import annotations

import os
import subprocess
import winreg
from pathlib import Path
from typing import Any

from .logutil import load_state, log, save_state

# NT kernel build numbers (CurrentBuildNumber)
BUILD_VISTA = (6000, 6001, 6002)
BUILD_WIN7 = (7600, 7601)
BUILD_WIN8 = (9200,)
BUILD_WIN81 = (9600, 9601)
BUILD_WIN10_MIN = 10240

# Generic Win10 Pro install key (edition selection only — not activation)
WIN10_PRO_GENERIC_PID = "VK7JG-NPHTM-C97JM-9MPGT-3V66T"

# Edition IDs / product strings that indicate Media Center Key Edition
_MC_EDITION_IDS = frozenset(
    {
        "professionalwmc",
        "professionalmediacenter",
        "coremediacenter",
        "coreconnectedmediacenter",
        "embeddedindustry",  # rare WMC-adjacent; warn only via exotic
    }
)

_DVD_PLAYER_PFN = "Microsoft.WindowsDVDPlayer_8wekyb3d8bbwe"
_DVD_PLAYER_NAME = "Microsoft.WindowsDVDPlayer"


def os_family_from_build(build: int) -> str:
    if build >= 22000:
        return "win11"
    if build >= BUILD_WIN10_MIN:
        return "win10"
    if build in BUILD_WIN81 or (9600 <= build < BUILD_WIN10_MIN):
        return "win81"
    if build in BUILD_WIN8:
        return "win8"
    if build in BUILD_WIN7 or (7600 <= build < 9200):
        return "win7"
    if build in BUILD_VISTA or (6000 <= build < 7600):
        return "vista"
    return "unknown"


def _media_center_files_present() -> bool:
    """True if classic Windows Media Center bits are still installed."""
    roots = (
        Path(os.environ.get("SystemRoot", r"C:\Windows")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    )
    markers = (
        ("ehome", "ehshell.exe"),
        ("ehome", "mcx2prov.exe"),
        ("Windows Media Center",),
    )
    for root in roots:
        for parts in markers:
            p = root.joinpath(*parts)
            if p.exists():
                return True
    return False


def _media_center_registry_present() -> bool:
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Media Center"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Media Center\Service Config"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Media Center"),
    )
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path):
                return True
        except OSError:
            continue
    return False


def detect_media_center(
    product_name: str,
    edition_id: str,
    *,
    build: int | None = None,
) -> bool:
    """
    Detect Media Center Key Edition / WMC pack.

    Matches edition ID / product name always. Installed Media Center files/registry
    are only probed on pre-Win10 hosts (avoids false positives from leftover keys
    on modern systems). After the Win10 hop, use state.json eligibility instead.
    """
    eid = (edition_id or "").strip().lower().replace(" ", "")
    blob = f"{product_name} {edition_id}".lower()
    if eid in _MC_EDITION_IDS or eid.endswith("wmc") or "mediacenter" in eid:
        return True
    if "media center" in blob or "mediacenter" in blob.replace(" ", ""):
        return True
    if "with media center" in blob:
        return True
    # Disk/registry probe only on legacy OS — Win10+ relies on persisted state
    if build is not None and int(build) >= BUILD_WIN10_MIN:
        return False
    if build is None:
        # Unknown build: still allow probe (called from unit tests / helpers)
        pass
    try:
        if _media_center_files_present() or _media_center_registry_present():
            return True
    except Exception:
        pass
    return False


def is_legacy_host(build: int, os_family: str) -> bool:
    return os_family in ("vista", "win7", "win8", "win81")


def legacy_label(os_family: str, build: int) -> str:
    labels = {
        "vista": "Windows Vista",
        "win7": "Windows 7",
        "win8": "Windows 8",
        "win81": "Windows 8.1",
    }
    if os_family in labels:
        return labels[os_family]
    return f"Windows (build {build})"


def media_center_eligible_from_state() -> bool:
    """After upgrade, edition becomes Pro — eligibility lives in state.json."""
    try:
        st = load_state()
        return bool(st.get("MediaCenterEligible") or st.get("DvdPlayerOfferEligible"))
    except Exception:
        return False


def media_offers_already_claimed() -> bool:
    try:
        return bool(load_state().get("MediaOffersClaimed"))
    except Exception:
        return False


def _reg_dword(root, path: str, name: str, value: int) -> bool:
    try:
        try:
            k = winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE)
        except OSError:
            k = winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE)
        with k:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))
        return True
    except OSError as e:
        log(f"Registry {path}\\{name}: {e}", "INFO")
        return False


def detect_exotic_edition(edition_id: str, product_name: str) -> list[str]:
    """Warn on editions that often block inplace (forums + SetupDiag)."""
    blob = f"{edition_id} {product_name}".lower()
    warns: list[str] = []
    if any(x in blob for x in ("embedded", "iot", "thinpc", "posready")):
        warns.append("Embedded/IoT/ThinPC edition — Setup may refuse retail Win10/11 media.")
    if "enterprises" in blob.replace(" ", "") or "ltsc" in blob or "ltsb" in blob:
        warns.append("LTSC/LTSB — prefer matching volume ISO; retail 22H2 may block edition change.")
    if edition_id.lower().endswith("n") or " n " in f" {blob} " or "edition n" in blob:
        warns.append("N edition (no Media features) — use matching N ISO language/edition when possible.")
    if "singlelanguage" in blob.replace(" ", "") or "countryspecific" in blob.replace(" ", ""):
        warns.append("Single Language / Country Specific — keep same edition path; Pro ISO may fail.")
    if "corecountryspecific" in blob.replace(" ", ""):
        warns.append("CoreCountrySpecific — inplace to Pro often blocked.")
    return warns


def persist_media_center_eligibility(report: Any) -> dict[str, Any]:
    """
    Persist Media Center / DVD Player eligibility BEFORE the Win10 hop.

    After inplace, EditionID becomes Professional and WMC files are removed —
    state.json is the only durable signal for the post-Win10 offer step.
    """
    fam = getattr(report, "os_family", "") or os_family_from_build(int(report.build))
    mc = bool(
        getattr(report, "has_media_center", False)
        or detect_media_center(
            getattr(report, "product_name", ""), getattr(report, "edition_id", "")
        )
        or media_center_eligible_from_state()
    )
    # Win7 Home Premium / Pro / Ultimate also got free DVD Player (no WMC required)
    pn = str(getattr(report, "product_name", "")).lower()
    eid = str(getattr(report, "edition_id", "")).lower()
    win7_dvd = fam == "win7" and any(
        x in pn or x in eid
        for x in ("homepremium", "home premium", "professional", "ultimate")
    )
    dvd_eligible = mc or win7_dvd or media_center_eligible_from_state()
    if not dvd_eligible and not mc:
        return {"eligible": False}

    payload = {
        "MediaCenterEligible": bool(mc or media_center_eligible_from_state()),
        "DvdPlayerOfferEligible": True,
        "CarryForwardOffers": [
            "WindowsDVDPlayer",
            "KeepAppsAndFiles",
            "DigitalEntitlementFromInplace",
        ],
        "SourceOsFamily": fam,
        "SourceEditionId": getattr(report, "edition_id", ""),
        "SourceProductName": getattr(report, "product_name", ""),
        "SourceBuild": int(getattr(report, "build", 0) or 0),
        "InplaceOnlyRequired": True,
    }
    save_state(payload)
    log(
        "Media Center / DVD Player eligibility saved for post-Win10 claim "
        f"(MC={payload['MediaCenterEligible']}, source={fam} "
        f"{payload['SourceEditionId']})",
        "OK",
    )
    return {"eligible": True, **payload}


def apply_legacy_host_registry(report: Any) -> dict[str, int]:
    """
    Host-side registry prep so old Windows allows upgrade to Win10 media.
    Safe DWORD/MULTI_SZ only — no secrets.
    """
    summary = {"keys_set": 0, "media_center": 0}
    fam = getattr(report, "os_family", "") or os_family_from_build(int(report.build))
    if not is_legacy_host(int(report.build), fam):
        # Still persist eligibility if somehow flagged on Win10 mid-chain
        if media_center_eligible_from_state() or getattr(report, "has_media_center", False):
            persist_media_center_eligibility(report)
        return summary

    log(f"=== Legacy host registry prep ({legacy_label(fam, report.build)}) ===", "STEP")

    pairs = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade",
            "AllowOSUpgrade",
            1,
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade",
            "ReservationsAllowed",
            1,
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
            "AllowOSUpgrade",
            1,
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate",
            "SusClientIdValidation",
            0,
        ),
    ]
    # Win8 / 8.1 — Setup Compact missing blocks some Win10 upgrades (Sysnative forums)
    if fam in ("win8", "win81"):
        pairs.append((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\Setup", "Compact", 1))
        # Help Media Center / Pro Pack hosts keep upgrade path open
        pairs.append(
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade",
                "AllowOSUpgradeUnlocked",
                1,
            )
        )

    for root, path, name, val in pairs:
        if _reg_dword(root, path, name, val):
            summary["keys_set"] += 1
            log(f"Set {path}\\{name}={val}", "OK")

    mc = getattr(report, "has_media_center", False) or detect_media_center(
        getattr(report, "product_name", ""), getattr(report, "edition_id", "")
    )
    if mc:
        summary["media_center"] = 1
        for path, name in (
            (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade", "AllowOSUpgrade"),
            (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\OSUpgrade",
                "CompatibleAppsUpgraded",
            ),
        ):
            if _reg_dword(winreg.HKEY_LOCAL_MACHINE, path, name, 1):
                summary["keys_set"] += 1
        log(
            "Media Center Key Edition — will patch Setup media (ei.cfg + pid.txt) "
            "and claim DVD Player offer after Win10 hop (inplace only)",
            "WARN",
        )
        persist_media_center_eligibility(report)
    elif fam == "win7":
        persist_media_center_eligibility(report)

    for w in detect_exotic_edition(
        getattr(report, "edition_id", ""), getattr(report, "product_name", "")
    ):
        log(w, "WARN")

    return summary


def write_media_center_edition_bypass(sources: Path, *, edition: str = "Professional") -> bool:
    """
    Win8/8.1 Pro + Media Center → Win10 Pro in-place:
    sources\\ei.cfg + sources\\pid.txt on installation media.
    """
    sources.mkdir(parents=True, exist_ok=True)
    ei = sources / "ei.cfg"
    pid = sources / "pid.txt"
    ei_body = "\r\n".join(
        [
            "[EditionID]",
            edition,
            "",
            "[Channel]",
            "Retail",
            "",
            "[VL]",
            "0",
            "",
        ]
    )
    pid_body = "\r\n".join(
        [
            "[PID]",
            f"Value={WIN10_PRO_GENERIC_PID}",
            "",
        ]
    )
    try:
        ei.write_text(ei_body, encoding="ascii")
        pid.write_text(pid_body, encoding="ascii")
        log(f"Media Center bypass: wrote {ei.name} + {pid.name} (→ {edition})", "OK")
        return True
    except OSError as e:
        log(f"Media Center media patch failed: {e}", "WARN")
        return False


def prepare_legacy_setup_media(
    iso_root: str | Path,
    report: Any,
    *,
    win: str,
) -> Path:
    """
    Writable staging for legacy hops: Media Center ei.cfg, SetupConfig, Appraiser neutralize.

    ei.cfg/pid.txt only on Win10 hops (target Professional). Win8.1 intermediate
    hops must keep Media Center edition intact.
    """
    from .media_bypass import neutralize_appraiser_on_media, stage_writable_setup, write_media_setupconfig

    root = Path(iso_root)
    fam = getattr(report, "os_family", "") or os_family_from_build(int(report.build))
    mc = (
        getattr(report, "has_media_center", False)
        or detect_media_center(
            getattr(report, "product_name", ""), getattr(report, "edition_id", "")
        )
        or media_center_eligible_from_state()
    )
    needs_stage = mc or fam in ("vista", "win7", "win8", "win81") or win in ("11", "10")

    if needs_stage:
        try:
            root = stage_writable_setup(root, force=False)
        except Exception as e:
            log(f"Legacy media stage fallback to mount: {e}", "WARN")

    sources = root / "sources"
    # Force Pro only when jumping to Win10 (Media Center editions block keep-apps)
    if win == "10" and (
        mc
        or (
            fam in ("win8", "win81")
            and "professional" in str(getattr(report, "edition_id", "")).lower()
        )
    ):
        write_media_center_edition_bypass(sources)

    if fam in ("vista", "win7", "win8", "win81") or win in ("10", "81"):
        write_media_setupconfig(root)
        neutralize_appraiser_on_media(root)

    if win == "11":
        from .media_bypass import prepare_setup_root

        try:
            root = prepare_setup_root(root, win11=True)
        except Exception as e:
            log(f"Win11 media stage fallback: {e}", "WARN")

    return root


def find_optional_win81_iso() -> Path | None:
    """
    Optional Windows 8.1 ISO for Win8 → 8.1 hop (not on Microsoft CDN).

    Sources: MAGIC_WIN81_ISO env, state Win81Iso, or common drop folders.
    """
    candidates: list[Path] = []
    env = (os.environ.get("MAGIC_WIN81_ISO") or "").strip().strip('"')
    if env:
        candidates.append(Path(env))
    try:
        st = load_state()
        p = st.get("Win81Iso") or st.get("Win8_1Iso")
        if p:
            candidates.append(Path(str(p)))
    except Exception:
        pass
    localapp = Path(os.environ.get("LOCALAPPDATA", "")) / "Win11MagicUpgrade" / "iso"
    desk = Path.home() / "Desktop"
    for folder in (localapp, desk, Path(r"C:\ISO"), Path(r"D:\ISO")):
        if not folder.is_dir():
            continue
        for pattern in ("*8.1*.iso", "*81*.iso", "*win81*.iso", "*Windows8.1*.iso"):
            candidates.extend(sorted(folder.glob(pattern)))
    for c in candidates:
        try:
            if c.is_file() and c.stat().st_size > 1_000_000_000:
                return c.resolve()
        except OSError:
            continue
    return None


def dvd_player_installed() -> bool:
    """Detect Microsoft Windows DVD Player AppX without PowerShell."""
    roots = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WindowsApps",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SystemApps",
    )
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                name = child.name.lower()
                if name.startswith("microsoft.windowsdvdplayer"):
                    return True
        except OSError:
            continue
    # Package repository (per-user / machine)
    repo = (
        r"SOFTWARE\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion"
        r"\AppModel\Repository\Packages"
    )
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, repo) as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                if sub.lower().startswith("microsoft.windowsdvdplayer"):
                    return True
                i += 1
    except OSError:
        pass
    return False


def _run_quiet(args: list[str], *, timeout: int = 120) -> int:
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return int(r.returncode)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"Command failed ({args[0]}): {e}", "INFO")
        return -1


def _nudge_windows_update() -> None:
    """Best-effort WU scan so DVD Player / carry-forward updates can land."""
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    uso = system / "UsoClient.exe"
    if uso.is_file():
        for verb in ("StartScan", "StartDownload", "StartInstall"):
            _run_quiet([str(uso), verb], timeout=60)
        log("Windows Update nudged (UsoClient StartScan/Download/Install)", "OK")
        return
    wu = system / "wuauclt.exe"
    if wu.is_file():
        _run_quiet([str(wu), "/detectnow"], timeout=60)
        _run_quiet([str(wu), "/updatenow"], timeout=60)
        log("Windows Update nudged (wuauclt)", "OK")


def _try_winget_dvd_player() -> bool:
    """Optional Store install if winget is present (may still require entitlement)."""
    winget = Path(os.environ.get("LocalAppData", "")) / "Microsoft" / "WindowsApps" / "winget.exe"
    candidates = [
        winget,
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "winget.exe",
    ]
    exe = next((p for p in candidates if p.is_file()), None)
    if exe is None:
        # PATH lookup
        from shutil import which

        w = which("winget")
        if not w:
            return False
        exe = Path(w)
    # Official Microsoft DVD Player Store product id historically used free entitlement
    for pkg in (_DVD_PLAYER_NAME, "9NBLGGH4R35N"):
        code = _run_quiet(
            [
                str(exe),
                "install",
                "--id",
                pkg,
                "-e",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            timeout=300,
        )
        if code == 0:
            log(f"winget installed {pkg}", "OK")
            return True
    return False


def claim_media_center_offers(report: Any) -> dict[str, Any]:
    """
    After Win10 hop: try to surface Windows DVD Player + log carry-forward status.

    Safe / autonomous: never blocks the chain if the (expired) free offer is gone.
    """
    result: dict[str, Any] = {
        "eligible": False,
        "installed": False,
        "wu_nudged": False,
        "winget": False,
        "skipped": False,
    }
    build = int(getattr(report, "build", 0) or 0)
    if build < BUILD_WIN10_MIN:
        log("Media offers deferred — still on pre-Win10 host (Win10 hop first)", "INFO")
        result["skipped"] = True
        return result

    if media_offers_already_claimed() and dvd_player_installed():
        log("Media Center offers already claimed; DVD Player present", "OK")
        result["eligible"] = True
        result["installed"] = True
        result["skipped"] = True
        return result

    st = load_state()
    eligible = bool(
        st.get("DvdPlayerOfferEligible")
        or st.get("MediaCenterEligible")
        or getattr(report, "has_media_center", False)
    )
    result["eligible"] = eligible
    if not eligible:
        log("No Media Center / DVD Player eligibility on record — skip offer step", "INFO")
        save_state({"MediaOffersClaimed": True, "MediaOffersNote": "not_eligible"})
        result["skipped"] = True
        return result

    log("=== Media Center carry-forward offers (Windows DVD Player) ===", "STEP")
    log(
        "In-place upgrade preserved entitlement markers. "
        "Microsoft's free DVD Player offer was time-limited (2015 WU); "
        "we still nudge Update and check for Microsoft.WindowsDVDPlayer.",
        "INFO",
    )

    if dvd_player_installed():
        log("Windows DVD Player already installed", "OK")
        result["installed"] = True
    else:
        _nudge_windows_update()
        result["wu_nudged"] = True
        if _try_winget_dvd_player():
            result["winget"] = True
        result["installed"] = dvd_player_installed()
        if result["installed"]:
            log("Windows DVD Player is now present", "OK")
        else:
            log(
                "DVD Player AppX not detected yet — eligibility markers kept. "
                "Check Settings → Windows Update later, or Microsoft Store "
                f"({_DVD_PLAYER_PFN}). Chain continues to latest Windows.",
                "WARN",
            )

    save_state(
        {
            "MediaOffersClaimed": True,
            "DvdPlayerInstalled": bool(result["installed"]),
            "MediaOffersNote": (
                "installed"
                if result["installed"]
                else "nudged_wu_offer_may_be_expired"
            ),
            "CarryForwardOffers": st.get("CarryForwardOffers")
            or ["WindowsDVDPlayer", "KeepAppsAndFiles"],
        }
    )
    return result


def inplace_notes_for_legacy(report: Any) -> list[str]:
    fam = getattr(report, "os_family", "")
    notes: list[str] = []
    if fam == "vista":
        notes.append(
            "Windows Vista has no Microsoft-supported in-place path to Win10/11; "
            "Setup may offer keep-files upgrade only on some configs — otherwise backup required."
        )
    if fam == "win7":
        notes.append(
            "Windows 7 SP1 → Win10 22H2 in-place (keep apps) → Win11; "
            "Home Premium/Pro/Ultimate also qualify for Windows DVD Player carry-forward."
        )
    if fam == "win8":
        notes.append(
            "Windows 8 → (optional local 8.1 ISO via MAGIC_WIN81_ISO) → Win10 22H2 → Win11 latest. "
            "Direct 8→10 22H2 keep-apps is used when no 8.1 ISO is available."
        )
    if fam == "win81":
        notes.append("Windows 8.1 → Win10 22H2 via setupprep.exe on staged ISO → Win11 latest.")
    if getattr(report, "has_media_center", False) or media_center_eligible_from_state():
        notes.append(
            "Media Center Key Edition: Pro forced via ei.cfg/pid.txt on Win10 media; "
            "inplace only (never clean) so apps/data and DVD Player eligibility markers survive; "
            "after Win10 hop One-Click claims Windows DVD Player before continuing to Win11."
        )
    return notes
