"""Mandatory checks after any MBR or EFI change.

A failed check rolls back from the pre-op backup, runs the checks again, and
returns NO-GO. The operation is not left in place on a failed check.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .logutil import STATE_DIR, log

_ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
_MBR_BOOT_TYPES = {"07", "7", "0x07", "06", "6", "0x06", "0c", "0x0c", "ef", "0xef"}
MAX_REGEN_ATTEMPTS = 4
REGEN_BUDGET_S = 180
STRATEGY_TIMEOUT_S = 90
_LEDGER_NAME = "post-op-regen.json"
_REGEN: dict[str, tuple[str, ...]] = {
    "drive-map": ("rescan-map",),
    "esp-type": ("bcdboot-uefi", "bcdboot-uefi-clean"),
    "mbr-active": ("mark-active", "bootsect-nt60-mbr"),
    "bootsect-signature": ("bootsect-nt60-mbr",),
    "file:bootmgfw.efi": ("copy-bootmgfw", "bcdboot-uefi", "bcdboot-uefi-clean"),
    "file:bootmgr": ("copy-bootmgr", "bcdboot-bios", "bcdboot-all"),
    "file:BCD": ("bcdedit-import", "bcdedit-createstore", "bcdboot-all"),
    "bcd-enum": ("fix-device-refs", "bcdedit-import", "bcdedit-createstore"),
    "firmware-bootmgr": ("bcdboot-uefi", "bcdboot-uefi-clean"),
}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def boot_sector_signature_ok(sector: bytes | None) -> bool:
    """BIOS boot code ends with the 0x55AA signature bootsect writes."""
    if not sector or len(sector) < 512:
        return False
    return sector[510:512] == b"\x55\xaa"


def _log_checks(prefix: str, checks: list[dict[str, Any]]) -> None:
    for check in checks:
        state = "OK" if check.get("ok") else "FAIL"
        log(
            f"{prefix} {check.get('name')}: {state} {check.get('detail') or ''}".rstrip(),
            "OK" if check.get("ok") else "ERROR",
        )


def _file_check(files: dict[str, Any], name: str, expected: dict[str, str]) -> dict[str, Any]:
    record = files.get(name) or {}
    present = bool(record.get("present"))
    digest = str(record.get("sha256") or "")
    wanted = str(expected.get(name) or "")
    if not present:
        return {"name": f"file:{name}", "ok": False, "detail": "missing"}
    if len(digest) != 64:
        return {"name": f"file:{name}", "ok": False, "detail": "hash missing"}
    if wanted and digest.lower() != wanted.lower():
        return {"name": f"file:{name}", "ok": False, "detail": "hash mismatch"}
    return {"name": f"file:{name}", "ok": True, "detail": digest[:12]}


def _device_known(device: str, known: list[str]) -> bool:
    text = (device or "").strip()
    if not text:
        return False
    if not known:
        return text.lower().startswith("partition=") or "harddisk" in text.lower()
    folded = text.lower()
    return any(item.lower() in folded or folded in item.lower() for item in known if item)


def evaluate_post_op(evidence: dict[str, Any], *, scopes: tuple[str, ...] = ("uefi",)) -> list[dict[str, Any]]:
    """Each check is logged by the caller. scopes is uefi, mbr, or both."""
    want = {item.lower() for item in scopes}
    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "name": "drive-map",
            "ok": bool(evidence.get("map_ok")),
            "detail": str(evidence.get("style") or "unscanned"),
        }
    )
    part = evidence.get("partition") or {}
    if "uefi" in want:
        gpt = str(part.get("gpt_type") or "").lower()
        esp = _ESP_GUID in gpt or bool(part.get("is_system"))
        checks.append({"name": "esp-type", "ok": bool(part.get("present")) and esp, "detail": gpt or "no esp"})
    if "mbr" in want:
        kind = str(part.get("mbr_type") or "").lower()
        active = bool(part.get("is_active")) and kind in _MBR_BOOT_TYPES
        checks.append(
            {
                "name": "mbr-active",
                "ok": bool(part.get("present")) and active,
                "detail": f"type={kind or '-'} active={bool(part.get('is_active'))}",
            }
        )
        sector = evidence.get("boot_sector")
        signed = boot_sector_signature_ok(sector if isinstance(sector, (bytes, bytearray)) else None)
        checks.append({"name": "bootsect-signature", "ok": signed, "detail": "55AA" if signed else "missing"})
    files = evidence.get("files") or {}
    expected = evidence.get("expected_hashes") or {}
    needed: list[str] = []
    if "uefi" in want:
        needed.extend(["bootmgfw.efi", "BCD"])
    if "mbr" in want:
        needed.extend(["bootmgr", "BCD"])
    seen: set[str] = set()
    for name in needed:
        if name in seen:
            continue
        seen.add(name)
        checks.append(_file_check(files, name, expected))
    from .pre_reboot import parse_store_enum, store_is_known_good

    parsed = parse_store_enum(str(evidence.get("bcd_text") or ""))
    known = [str(item) for item in (evidence.get("known_devices") or [])]
    if "mbr" in want and "uefi" in want:
        bcd_ok = store_is_known_good(parsed, uefi=True) or store_is_known_good(parsed, uefi=False)
    elif "mbr" in want:
        bcd_ok = store_is_known_good(parsed, uefi=False)
    else:
        bcd_ok = store_is_known_good(parsed, uefi=True)
    pointed = _device_known(str(parsed.get("bootmgr_device") or ""), known) and _device_known(
        str(parsed.get("osdevice") or parsed.get("device") or ""), known
    )
    checks.append(
        {
            "name": "bcd-enum",
            "ok": bool(parsed.get("ok")) and bcd_ok and pointed,
            "detail": str(parsed.get("path") or parsed.get("error") or "no loader"),
        }
    )
    if "uefi" in want:
        firmware = str(evidence.get("firmware_text") or "")
        found = "windows boot manager" in firmware.lower()
        checks.append({"name": "firmware-bootmgr", "ok": found, "detail": "Windows Boot Manager" if found else "missing"})
    return checks


def _default_rollback() -> bool:
    try:
        from .boot_safe import restore_bcd_from_last, restore_esp_critical_files

        files = restore_esp_critical_files()
        store = restore_bcd_from_last()
        return bool(files or store)
    except Exception as exc:
        log(f"pre-op rollback failed: {exc}", "ERROR")
        return False


def _hash_file(path: Any) -> dict[str, Any]:
    from pathlib import Path

    file_path = Path(path)
    if not file_path.is_file():
        return {"present": False, "sha256": ""}
    return {"present": True, "sha256": sha256_hex(file_path.read_bytes())}


def _files_from_root(root: str) -> dict[str, Any]:
    from pathlib import Path

    base = Path(root)
    files = {
        "bootmgfw.efi": _hash_file(base / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi"),
        "bootmgr": _hash_file(base / "bootmgr"),
        "BCD": _hash_file(base / "EFI" / "Microsoft" / "Boot" / "BCD"),
    }
    bios_bcd = base / "Boot" / "BCD"
    if bios_bcd.is_file() and not files["BCD"]["present"]:
        files["BCD"] = _hash_file(bios_bcd)
    return files


def _boot_sector(root: str) -> bytes:
    letter = root.strip().rstrip("\\").rstrip(":")
    if len(letter) != 1 or not letter.isalpha():
        return b""
    try:
        with open("\\\\.\\" + letter + ":", "rb") as handle:
            return handle.read(512)
    except OSError:
        return b""


def collect_post_op_evidence(*, scopes: tuple[str, ...] = ("uefi",), root: str | None = None) -> dict[str, Any]:
    """Best-effort live read. Missing tools produce a failed check, not a guess."""
    evidence: dict[str, Any] = {
        "map_ok": False,
        "style": "UNKNOWN",
        "partition": {},
        "files": _files_from_root(root) if root else {},
        "bcd_text": "",
        "firmware_text": "",
        "known_devices": [],
        "boot_sector": _boot_sector(root) if root else b"",
    }
    if os.name != "nt":
        return evidence
    try:
        from .esp_access import scan_windows_map

        drive_map = scan_windows_map()
        evidence["map_ok"] = drive_map is not None
        evidence["style"] = str(getattr(drive_map, "firmware", "") or "")
        want = {item.lower() for item in scopes}
        for vol in getattr(drive_map, "volumes", ()) or ():
            gpt = str(getattr(vol, "gpt_type", "") or "")
            if "uefi" in want and _ESP_GUID in gpt.lower():
                evidence["partition"] = {
                    "present": True,
                    "gpt_type": gpt,
                    "is_system": bool(getattr(vol, "is_system", False)),
                    "is_active": bool(getattr(vol, "is_active", False)),
                    "mbr_type": str(getattr(vol, "mbr_type", "") or ""),
                }
                if getattr(vol, "letter", None):
                    evidence["known_devices"].append(f"partition={vol.letter}:")
                break
            if "mbr" in want and bool(getattr(vol, "is_active", False)):
                evidence["partition"] = {
                    "present": True,
                    "gpt_type": gpt,
                    "is_system": bool(getattr(vol, "is_system", False)),
                    "is_active": True,
                    "mbr_type": str(getattr(vol, "mbr_type", "") or ""),
                }
                break
    except Exception as exc:
        log(f"post-op drive map rescan failed: {exc}", "ERROR")
    try:
        from .proc_guard import run_bounded

        enum = run_bounded(["bcdedit", "/enum", "all"], timeout=90)
        evidence["bcd_text"] = enum.output or ""
        firmware = run_bounded(["bcdedit", "/enum", "firmware"], timeout=60)
        evidence["firmware_text"] = firmware.output or ""
    except Exception as exc:
        log(f"post-op bcdedit failed: {exc}", "ERROR")
    return evidence


def attempt_regeneration(
    strategy: str,
    *,
    windows: str | None = None,
    target: str = "S",
    locale: str = "en-us",
    backup: str = "",
) -> bool:
    """Run one regeneration command. A timeout or a non-zero exit does not verify."""
    windows_root = windows or os.environ.get("SystemRoot", r"C:\Windows")
    if strategy in ("copy-bootmgfw", "copy-bootmgr"):
        return _copy_loader(strategy, windows_root, target)
    if strategy == "rescan-map":
        from .diskpart_safe import run_diskpart

        ok, _out = run_diskpart("rescan\nexit", timeout=STRATEGY_TIMEOUT_S)
        return ok
    command = strategy_command(strategy, windows=windows_root, target=target, locale=locale, backup=backup)
    if not command or os.name != "nt" or command[0] == "diskpart":
        return False
    from .proc_guard import run_bounded

    result = run_bounded(command, timeout=STRATEGY_TIMEOUT_S)
    return result.code == 0 and not result.timed_out


def _copy_loader(strategy: str, windows_root: str, target: str) -> bool:
    import shutil
    from pathlib import Path

    root = Path(windows_root)
    letter = target.strip().rstrip("\\").rstrip(":")[:1]
    if strategy == "copy-bootmgfw":
        sources = (root / "Boot" / "EFI" / "bootmgfw.efi",)
        dest = Path(f"{letter}:\\EFI\\Microsoft\\Boot\\bootmgfw.efi")
    else:
        sources = (root / "Boot" / "PCAT" / "bootmgr",)
        dest = Path(f"{letter}:\\bootmgr")
    source = next((path for path in sources if path.is_file()), None)
    if source is None:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest.is_file()


def gate_after_boot_work(*, scopes: tuple[str, ...], root: str | None = None) -> bool:
    """Windows-only gate. Tests pass evidence straight to mandatory_post_op_verify."""
    if os.name != "nt":
        return True
    letter = (root or "S").strip().rstrip("\\").rstrip(":")[:1] or "S"
    report = mandatory_post_op_verify(
        evidence=collect_post_op_evidence(scopes=scopes, root=root),
        scopes=scopes,
        attempt=lambda strategy: attempt_regeneration(strategy, target=letter),
    )
    return report.get("verdict") == "GO"


def evidence_fingerprint(evidence: dict[str, Any], checks: list[dict[str, Any]]) -> str:
    """Stable id for a failed read. The same boot state must not be repaired twice."""
    failed = [str(item.get("name")) for item in checks if not item.get("ok")]
    part = evidence.get("partition") or {}
    files = evidence.get("files") or {}
    file_bits = []
    for name in sorted(files):
        record = files.get(name) or {}
        file_bits.append(
            f"{name}:{int(bool(record.get('present')))}:{str(record.get('sha256') or '')[:12]}"
        )
    payload = "|".join(
        [
            ",".join(failed),
            str(evidence.get("style") or ""),
            str(part.get("gpt_type") or ""),
            str(part.get("mbr_type") or ""),
            str(bool(part.get("is_active"))),
            *file_bits,
            str(evidence.get("bcd_text") or "")[:180],
            str(evidence.get("firmware_text") or "")[:80],
        ]
    )
    return sha256_hex(payload.encode("utf-8"))


def empty_regen_ledger() -> dict[str, Any]:
    return {"attempts": 0, "tried": [], "fingerprints": [], "started": None, "exhausted": False}


def load_regen_ledger(path: Path | None = None) -> dict[str, Any]:
    ledger_path = path or (STATE_DIR / _LEDGER_NAME)
    if not ledger_path.is_file():
        return empty_regen_ledger()
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_regen_ledger()
    ledger = empty_regen_ledger()
    ledger.update({key: data.get(key, ledger[key]) for key in ledger})
    ledger["attempts"] = int(ledger.get("attempts") or 0)
    ledger["tried"] = [str(item) for item in (ledger.get("tried") or [])]
    ledger["fingerprints"] = [str(item) for item in (ledger.get("fingerprints") or [])]
    return ledger


def save_regen_ledger(ledger: dict[str, Any], path: Path | None = None) -> None:
    ledger_path = path or (STATE_DIR / _LEDGER_NAME)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def regen_block_reason(
    ledger: dict[str, Any],
    *,
    strategy: str | None,
    fingerprint: str,
    now: float,
    budget_s: float = REGEN_BUDGET_S,
    max_attempts: int = MAX_REGEN_ATTEMPTS,
) -> str:
    """Why this attempt must not run. Empty means it is still allowed."""
    if ledger.get("exhausted"):
        return "persisted-exhausted"
    if int(ledger.get("attempts") or 0) >= max_attempts:
        return "max-attempts"
    started = ledger.get("started")
    if started is not None and now - float(started) > budget_s:
        return "budget"
    if strategy and strategy in (ledger.get("tried") or []):
        return "strategy-already-tried"
    if fingerprint and fingerprint in (ledger.get("fingerprints") or []):
        return "repeated-state"
    return ""


def regen_is_exhausted(path: Path | None = None, *, now: float | None = None) -> bool:
    """True when a previous process already spent the regeneration budget."""
    ledger = load_regen_ledger(path)
    clock = time.time() if now is None else now
    return bool(
        regen_block_reason(ledger, strategy=None, fingerprint="", now=clock, max_attempts=MAX_REGEN_ATTEMPTS)
    )


def regeneration_plan(checks: list[dict[str, Any]]) -> list[str]:
    """Ordered strategies for the checks that failed. Capped so a loop cannot grow."""
    plan: list[str] = []
    for check in checks:
        if check.get("ok"):
            continue
        for strategy in _REGEN.get(str(check.get("name") or ""), ()):
            if strategy not in plan:
                plan.append(strategy)
            if len(plan) >= MAX_REGEN_ATTEMPTS:
                return plan
    return plan


def strategy_command(
    strategy: str,
    *,
    windows: str = r"C:\Windows",
    target: str = "S",
    locale: str = "en-us",
    backup: str = "",
    device: str = "partition=C:",
) -> list[str]:
    """The exact command a strategy runs. The letter passed to /s has no extra colon."""
    letter = target.strip().rstrip("\\").rstrip(":")[:1] or "S"
    bcdboot = str(windows).rstrip("\\") + r"\System32\bcdboot.exe"
    bcdedit = str(windows).rstrip("\\") + r"\System32\bcdedit.exe"
    bootsect = str(windows).rstrip("\\") + r"\System32\bootsect.exe"
    if strategy == "bcdboot-uefi":
        return [bcdboot, windows, "/s", f"{letter}:", "/f", "UEFI", "/l", locale]
    if strategy == "bcdboot-uefi-clean":
        return [bcdboot, windows, "/s", f"{letter}:", "/f", "UEFI", "/l", locale, "/c"]
    if strategy == "bcdboot-bios":
        return [bcdboot, windows, "/s", f"{letter}:", "/f", "BIOS", "/l", locale]
    if strategy == "bcdboot-all":
        return [bcdboot, windows, "/s", f"{letter}:", "/f", "ALL", "/l", locale]
    if strategy == "bcdedit-import":
        return [bcdedit, "/import", backup or "pre-op-bcd"]
    if strategy == "bcdedit-createstore":
        return [bcdedit, "/createstore", backup or "new-bcd"]
    if strategy == "fix-device-refs":
        return [bcdedit, "/set", "{default}", "osdevice", device]
    if strategy == "bootsect-nt60-mbr":
        return [bootsect, "/nt60", f"{letter}:", "/mbr"]
    if strategy == "copy-bootmgfw":
        return ["copy", "bootmgfw.efi", f"{letter}:\\EFI\\Microsoft\\Boot\\bootmgfw.efi"]
    if strategy == "copy-bootmgr":
        return ["copy", "bootmgr", f"{letter}:\\bootmgr"]
    if strategy == "mark-active":
        return ["diskpart", "active"]
    if strategy == "rescan-map":
        return ["diskpart", "rescan"]
    return []


def mandatory_post_op_verify(
    *,
    evidence: dict[str, Any] | None = None,
    scopes: tuple[str, ...] = ("uefi",),
    rollback: Callable[[str], bool] | None = None,
    reread: Callable[[], dict[str, Any]] | None = None,
    attempt: Callable[[str], bool] | None = None,
    backup: str = "pre-op",
    ledger_path: Path | None = None,
    now: float | None = None,
    budget_s: float = REGEN_BUDGET_S,
    max_attempts: int = MAX_REGEN_ATTEMPTS,
) -> dict[str, Any]:
    """Run the checks. Each repair runs once, inside a persisted budget, or the backup is restored."""
    current = evidence if evidence is not None else collect_post_op_evidence(scopes=scopes)
    checks = evaluate_post_op(current, scopes=scopes)
    _log_checks("post-op", checks)
    if all(item.get("ok") for item in checks):
        return {
            "verdict": "GO",
            "rollback": False,
            "restored": True,
            "checks": checks,
            "after_rollback": [],
            "decision": [],
        }
    clock = time.time() if now is None else now
    ledger = load_regen_ledger(ledger_path)
    if ledger.get("started") is None:
        ledger["started"] = clock
    fingerprint = evidence_fingerprint(current, checks)
    plan = regeneration_plan(checks)
    failed = [str(item.get("name")) for item in checks if not item.get("ok")]
    log(f"post-op decision: failed={','.join(failed)} plan={','.join(plan)}", "WARN")
    decision: list[dict[str, Any]] = []
    stop = regen_block_reason(
        ledger, strategy=None, fingerprint=fingerprint, now=clock, budget_s=budget_s, max_attempts=max_attempts
    )
    if stop:
        log(f"post-op regeneration stopped before any repair: {stop}", "ERROR")
        decision.append({"strategy": "", "ran": False, "verified": False, "stop": stop})
    else:
        for index, strategy in enumerate(plan, start=1):
            if now is None:
                clock = time.time()
            if index > max_attempts or int(ledger.get("attempts") or 0) >= max_attempts:
                log("post-op regeneration stopped: max-attempts", "ERROR")
                decision.append({"strategy": strategy, "ran": False, "verified": False, "stop": "max-attempts"})
                break
            blocked = regen_block_reason(
                ledger,
                strategy=strategy,
                fingerprint="",
                now=clock,
                budget_s=budget_s,
                max_attempts=max_attempts,
            )
            if blocked:
                log(f"post-op regeneration stopped: {blocked}", "ERROR")
                decision.append({"strategy": strategy, "ran": False, "verified": False, "stop": blocked})
                break
            ledger["attempts"] = int(ledger.get("attempts") or 0) + 1
            ledger["tried"] = list(ledger.get("tried") or []) + [strategy]
            ledger["fingerprints"] = list(ledger.get("fingerprints") or []) + [fingerprint]
            save_regen_ledger(ledger, ledger_path)
            log(f"post-op attempt {ledger['attempts']}/{max_attempts} {strategy}", "INFO")
            try:
                ran = bool(attempt(strategy)) if attempt is not None else False
            except Exception as exc:
                log(f"post-op attempt {strategy} failed: {exc}", "ERROR")
                ran = False
            verified = False
            if ran and reread is not None:
                current = reread()
                checks = evaluate_post_op(current, scopes=scopes)
                _log_checks(f"post-op after {strategy}", checks)
                verified = all(item.get("ok") for item in checks)
                new_fp = evidence_fingerprint(current, checks)
                if not verified and new_fp in (ledger.get("fingerprints") or []):
                    log("post-op regeneration stopped: repeated-state", "ERROR")
                    decision.append({"strategy": strategy, "ran": ran, "verified": False, "stop": "repeated-state"})
                    break
                fingerprint = new_fp
            decision.append({"strategy": strategy, "ran": ran, "verified": verified})
            if verified:
                log(f"post-op attempt {strategy} verified", "OK")
                save_regen_ledger(empty_regen_ledger(), ledger_path)
                return {
                    "verdict": "GO",
                    "rollback": False,
                    "restored": True,
                    "checks": checks,
                    "after_rollback": [],
                    "decision": decision,
                }
            log(f"post-op attempt {strategy} did not verify", "WARN")
    ledger["exhausted"] = True
    save_regen_ledger(ledger, ledger_path)
    log("post-op regeneration exhausted. Rolling back to the last known-good backup.", "ERROR")
    try:
        rolled = bool(rollback(backup)) if rollback is not None else _default_rollback()
    except Exception as exc:
        log(f"pre-op rollback failed: {exc}", "ERROR")
        rolled = False
    if reread is not None:
        again_ev = reread()
    elif evidence is None:
        again_ev = collect_post_op_evidence(scopes=scopes)
    else:
        again_ev = current
    again = evaluate_post_op(again_ev, scopes=scopes)
    _log_checks("post-op after rollback", again)
    return {
        "verdict": "NO-GO",
        "rollback": True,
        "rollback_called": rolled,
        "restored": all(item.get("ok") for item in again),
        "checks": checks,
        "after_rollback": again,
        "decision": decision,
    }
