"""
Hybrid IA32 UEFI path for 64-bit capable CPUs.

Firmware loads only IA32 EFI apps. Native Win11 x64 winload.efi cannot run.
Hybrid chain (community-proven approach):

  IA32 UEFI  ->  CSMWrap (csmwrapia32.efi)  ->  SeaBIOS (CSM)  ->  BIOS bootmgr  ->  Win x64

CSMWrap is downloaded from the official GitHub release (not bundled) and staged
on the ESP. Default deploy is NON-DESTRUCTIVE (side-by-side). Activation that
replaces EFI\\Boot\\bootia32.efi is opt-in / only when BIOS boot files exist.
"""
from __future__ import annotations

import json
import os
import shutil
import ssl
import urllib.request
from pathlib import Path

from .logutil import STATE_DIR, log

CSMWRAP_API = "https://api.github.com/repos/CSMWrap/CSMWrap/releases/latest"
CSMWRAP_ASSET = "csmwrapia32.efi"
USER_AGENT = "Win11MagicUpgrade/1.2 (hybrid-ia32; +https://github.com/dlnraja/win11-magic-upgrade)"


def _http_json(url: str) -> dict:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _http_download(url: str, dest: Path) -> None:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, context=ctx, timeout=180) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)


def download_csmwrap_ia32(cache_dir: Path | None = None) -> Path:
    """Fetch latest csmwrapia32.efi from CSMWrap releases into local cache."""
    cache_dir = cache_dir or (STATE_DIR / "hybrid")
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / CSMWRAP_ASSET
    meta = cache_dir / "csmwrap.json"
    log("Downloading CSMWrap IA32 (hybrid UEFI->BIOS bridge)...", "STEP")
    rel = _http_json(CSMWRAP_API)
    tag = rel.get("tag_name", "?")
    asset = None
    for a in rel.get("assets") or []:
        if str(a.get("name", "")).lower() == CSMWRAP_ASSET:
            asset = a
            break
    if not asset:
        raise RuntimeError(f"{CSMWRAP_ASSET} not found in CSMWrap release {tag}")
    url = asset["browser_download_url"]
    _http_download(url, dest)
    meta.write_text(json.dumps({"tag": tag, "url": url, "size": dest.stat().st_size}, indent=2), encoding="utf-8")
    log(f"CSMWrap {tag} cached ({dest.stat().st_size} bytes)", "OK")
    return dest


def _write_hybrid_readme(esp: Path, activated: bool) -> None:
    text = f"""Win11 Magic Upgrade — Hybrid IA32 UEFI
=====================================
Activated_as_default_bootia32: {activated}

Chain:
  32-bit UEFI firmware
    -> CSMWrap (csmwrapia32.efi)   [this package]
    -> SeaBIOS compatibility layer
    -> Windows BIOS Boot Manager
    -> Windows 10/11 64-bit

Requirements:
  1) Disable Secure Boot in firmware setup
  2) Prefer MBR disk layout for legacy boot (GPT may work on some devices)
  3) Windows x64 must have BIOS boot files (bcdboot /f BIOS)

Files:
  EFI\\MagicUpgrade\\csmwrapia32.efi     staged hybrid loader
  EFI\\Boot\\bootia32.stock.efi          backup of OEM/Microsoft loader (if activated)
  EFI\\Boot\\bootia32.efi                active IA32 default (stock OR CSMWrap)

Keep-files path on 32-bit Windows: upgrade to Win10 22H2 x86 first.
Win11 x64 on IA32 UEFI cannot be an inplace upgrade from x86 — use clean
install media after hybrid is prepared, then boot via CSMWrap.

CSMWrap project: https://github.com/CSMWrap/CSMWrap
"""
    (esp / "EFI" / "MagicUpgrade" / "HYBRID-README.txt").write_text(text, encoding="utf-8")


def deploy_hybrid_ia32(*, activate: bool = False) -> dict:
    """
    Stage CSMWrap on ESP. If activate=True, replace EFI\\Boot\\bootia32.efi
    after backing up the stock loader (requires Secure Boot off at next boot).
    """
    from .sysreserved import mount_esp, unmount_letter

    result = {"ok": False, "activated": False, "path": None, "tag": None}
    letter = mount_esp()
    if not letter:
        log("Cannot mount ESP for hybrid deploy", "ERROR")
        return result

    try:
        esp = Path(letter + "\\")
        efi_boot = esp / "EFI" / "Boot"
        magic = esp / "EFI" / "MagicUpgrade"
        efi_boot.mkdir(parents=True, exist_ok=True)
        magic.mkdir(parents=True, exist_ok=True)

        src = download_csmwrap_ia32()
        staged = magic / CSMWRAP_ASSET
        shutil.copy2(src, staged)
        # Also keep a copy next to BOOT for firmware browsers
        shutil.copy2(src, efi_boot / "csmwrapia32.efi")
        result["path"] = str(staged)

        try:
            meta = json.loads((STATE_DIR / "hybrid" / "csmwrap.json").read_text(encoding="utf-8"))
            result["tag"] = meta.get("tag")
        except Exception:
            pass

        stock = efi_boot / "bootia32.efi"
        stock_bak = efi_boot / "bootia32.stock.efi"

        if activate:
            if stock.exists() and not stock_bak.exists():
                shutil.copy2(stock, stock_bak)
                log("Backed up stock bootia32.efi -> bootia32.stock.efi", "OK")
            shutil.copy2(src, stock)
            result["activated"] = True
            log(
                "Activated hybrid: EFI\\Boot\\bootia32.efi = CSMWrap. "
                "Disable Secure Boot. Next boot uses SeaBIOS (legacy) path.",
                "WARN",
            )
        else:
            # Non-destructive: leave stock bootia32; drop selectable sibling
            sibling = efi_boot / "bootia32.magic.efi"
            shutil.copy2(src, sibling)
            log(
                "Hybrid staged (non-destructive). Select CSMWrap / bootia32.magic from firmware boot menu, "
                "or re-run with activation after BIOS bootmgr is ready.",
                "OK",
            )

        _write_hybrid_readme(esp, result["activated"])
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "hybrid-esp.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        result["ok"] = True
        return result
    except Exception as e:
        log(f"Hybrid deploy failed: {e}", "ERROR")
        result["error"] = str(e)
        return result
    finally:
        unmount_letter(letter)


def prepare_bios_boot_files() -> bool:
    """Install BIOS-style bootmgr from running Windows (needed after CSMWrap->SeaBIOS)."""
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    bcdboot = Path(sys_root) / "System32" / "bcdboot.exe"
    if not bcdboot.exists():
        return False
    log("Installing BIOS Boot Manager files (for CSMWrap/SeaBIOS handoff)...", "STEP")
    import subprocess

    r = subprocess.run(
        [str(bcdboot), sys_root, "/f", "BIOS"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    log(f"bcdboot /f BIOS -> {r.returncode}: {out[:240]}")
    return r.returncode == 0 or "successfully" in out.lower()


def apply_hybrid_ia32_path(*, activate: bool = False, prepare_bios: bool = True) -> dict:
    """Full intelligent hybrid preparation for IA32 UEFI + x64 CPU."""
    log("=== Hybrid IA32 UEFI path (CSMWrap -> SeaBIOS -> Win x64) ===", "STEP")
    log("Disable Secure Boot in firmware before using the hybrid loader.", "WARN")
    if prepare_bios:
        # Safe on x86 OS too: adds BIOS boot files alongside UEFI
        prepare_bios_boot_files()
    return deploy_hybrid_ia32(activate=activate)
