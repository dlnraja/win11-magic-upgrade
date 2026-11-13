"""Mandatory checks after any MBR or EFI change.

A failed check rolls back from the pre-op backup, runs the checks again, and
returns NO-GO. The operation is not left in place on a failed check.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from .logutil import log

_ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
_MBR_BOOT_TYPES = {"07", "7", "0x07", "06", "6", "0x06", "0c", "0x0c", "ef", "0xef"}


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


def gate_after_boot_work(*, scopes: tuple[str, ...], root: str | None = None) -> bool:
    """Windows-only gate. Tests pass evidence straight to mandatory_post_op_verify."""
    if os.name != "nt":
        return True
    report = mandatory_post_op_verify(evidence=collect_post_op_evidence(scopes=scopes, root=root), scopes=scopes)
    return report.get("verdict") == "GO"


def mandatory_post_op_verify(
    *,
    evidence: dict[str, Any] | None = None,
    scopes: tuple[str, ...] = ("uefi",),
    rollback: Callable[[str], bool] | None = None,
    reread: Callable[[], dict[str, Any]] | None = None,
    backup: str = "pre-op",
) -> dict[str, Any]:
    """Run the checks. On failure, roll back, re-check, and return NO-GO."""
    current = evidence if evidence is not None else collect_post_op_evidence(scopes=scopes)
    checks = evaluate_post_op(current, scopes=scopes)
    _log_checks("post-op", checks)
    if all(item.get("ok") for item in checks):
        return {"verdict": "GO", "rollback": False, "restored": True, "checks": checks, "after_rollback": []}
    log("post-op verification failed. Rolling back from the pre-op backup.", "ERROR")
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
    }
