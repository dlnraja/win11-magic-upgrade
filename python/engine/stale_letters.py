"""Remove drive letters left on the ESP, MSR, or recovery partition.

A letter is removed only when the partition type is one of those three.
``C:`` and ordinary data volumes are left alone. ``mountvol X: /D`` is tried
first, then Remove-PartitionAccessPath, then diskpart.
"""
from __future__ import annotations

import os
import re
from dataclasses import replace

from .drive_map import VolumeRecord, normalize_guid
from .logutil import log

ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
MSR = "e3c9e316-0b5c-4db8-817d-f92df00215ae"
RECOVERY = "de94bba4-06d1-4d40-a16a-bfd50179d6ac"
_KINDS = {ESP: "esp", MSR: "msr", RECOVERY: "recovery"}
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def protected_kind(gpt_type: str | None) -> str:
    return _KINDS.get(normalize_guid(gpt_type), "")


def volume_guid_path(value: str | None) -> str | None:
    text = normalize_guid(value)
    if not _GUID.fullmatch(text):
        return None
    return "\\\\?\\Volume{" + text + "}\\"


def _letters_of(volume: VolumeRecord) -> list[str]:
    found: list[str] = []
    raw = (volume.letter or "").strip().upper()
    if raw[:1].isalpha():
        found.append(raw[:1])
    for path in volume.access_paths:
        match = re.match(r"^([A-Za-z]):\\?$", str(path).strip())
        if match:
            found.append(match.group(1).upper())
    return found


def stale_system_letters(volumes, *, windows_letter: str = "C") -> list[VolumeRecord]:
    """Letters on ESP, MSR, or recovery. ``C:`` and the Windows letter stay."""
    keep = {(windows_letter or "C").upper()[:1], "C"}
    chosen: list[VolumeRecord] = []
    seen: set[str] = set()
    for volume in volumes:
        if not protected_kind(volume.gpt_type):
            continue
        for letter in _letters_of(volume):
            if letter in keep or letter in seen:
                continue
            seen.add(letter)
            chosen.append(replace(volume, letter=letter))
    return chosen


def judge_bootmgr(text: str) -> tuple[bool, str]:
    """True when ``bcdedit /enum {bootmgr}`` still describes the firmware boot entry."""
    body = text or ""
    low = body.lower()
    if not body.strip() or "cannot" in low or "introuvable" in low or "specified entry" in low:
        return False, "bootmgr did not resolve"
    if "windows boot manager" not in low and "identifier" not in low and "bootmgr" not in low:
        return False, "bootmgr did not resolve"
    device = ""
    match = re.search(r"(?im)^\s*device\s+(.+)$", body)
    if match:
        device = match.group(1).strip()
    if device:
        return True, f"bootmgr resolves: device {device}"
    return True, "bootmgr resolves"


def release_letter(letter: str, volume: VolumeRecord | None, io) -> str:
    """``mountvol /D``, then Remove-PartitionAccessPath, then diskpart."""
    cleaned = (letter or "").upper()[:1]
    if not cleaned.isalpha() or cleaned == "C":
        return "refused"
    drop = getattr(io, "mountvol_delete", None)
    if drop is not None:
        try:
            if drop(cleaned) is True:
                return "mountvol"
        except Exception:
            pass
    remover = getattr(io, "remove_letter", None)
    if remover is not None and volume is not None and volume.disk >= 0 and volume.partition is not None:
        try:
            if remover(volume, cleaned) is True:
                return "Remove-PartitionAccessPath"
        except Exception:
            pass
    diskpart = getattr(io, "diskpart_remove", None)
    if diskpart is not None and volume is not None:
        try:
            if diskpart(volume, cleaned) is True:
                return "diskpart"
        except Exception:
            pass
    return "failed"


def sweep_stale_letters(
    *,
    when: str = "startup",
    volumes: list[VolumeRecord] | None = None,
    windows_letter: str = "C",
    io=None,
    enum_bootmgr=None,
    logger=None,
) -> dict:
    """Drop stale system letters, then log whether ``{bootmgr}`` still resolves."""
    write = logger or log
    if volumes is None and os.name != "nt":
        return {"when": when, "removed": [], "skipped": "not-windows", "bootmgr_ok": False, "detail": ""}
    try:
        rows = list(volumes) if volumes is not None else _scan_windows_partitions()
    except Exception as exc:
        write(f"Stale letter scan skipped: {exc}", "WARN")
        return {"when": when, "removed": [], "skipped": "scan", "bootmgr_ok": False, "detail": ""}
    actor = io if io is not None else _WindowsLetters()
    removed: list[dict[str, str]] = []
    for item in stale_system_letters(rows, windows_letter=windows_letter):
        method = release_letter(item.letter or "", item, actor)
        kind = protected_kind(item.gpt_type)
        write(f"Stale {kind} letter {item.letter}: removed via {method}", "INFO" if method != "failed" else "WARN")
        removed.append({"letter": item.letter or "", "kind": kind, "method": method})
    try:
        text = enum_bootmgr() if enum_bootmgr is not None else _enum_bootmgr()
    except Exception as exc:
        text = ""
        write(f"bcdedit /enum {{bootmgr}} failed: {exc}", "WARN")
    ok, detail = judge_bootmgr(text)
    if enum_bootmgr is None and os.name != "nt":
        detail = "bootmgr check skipped"
        ok = False
    write(f"bcdedit /enum {{bootmgr}}: {detail}", "OK" if ok else "WARN")
    return {"when": when, "removed": removed, "bootmgr_ok": ok, "detail": detail, "skipped": ""}


def _scan_windows_partitions() -> list[VolumeRecord]:
    from .esp_access import _run

    script = (
        "Get-Partition | ForEach-Object { "
        "$letter = $_.DriveLetter; if (-not $letter) { $letter = '' }; "
        "'{0}|{1}|{2}|{3}' -f $_.DiskNumber, $_.PartitionNumber, $_.GptType, $letter }"
    )
    code, out = _run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout=40,
    )
    if code != 0:
        return []
    rows: list[VolumeRecord] = []
    for line in (out or "").splitlines():
        parts = [piece.strip() for piece in line.split("|")]
        if len(parts) < 4:
            continue
        try:
            disk = int(parts[0])
            partition = int(parts[1])
        except ValueError:
            continue
        letter = parts[3].upper()[:1]
        rows.append(
            VolumeRecord(
                disk=disk,
                partition=partition,
                style="GPT",
                gpt_type=parts[2],
                letter=letter if letter.isalpha() else None,
            )
        )
    return rows


def _enum_bootmgr() -> str:
    if os.name != "nt":
        return ""
    from .esp_access import _run

    _code, out = _run(["bcdedit", "/enum", "{bootmgr}"], timeout=20)
    return out or ""


class _WindowsLetters:
    def mountvol_delete(self, letter: str) -> bool:
        from .esp_access import WindowsVolumeIO

        return bool(WindowsVolumeIO().mountvol_delete(letter))

    def remove_letter(self, volume: VolumeRecord, letter: str) -> bool:
        from .esp_access import WindowsVolumeIO

        return bool(WindowsVolumeIO().remove_letter(volume, letter))

    def diskpart_remove(self, volume: VolumeRecord, letter: str) -> bool:
        from .esp_access import WindowsVolumeIO

        remover = getattr(WindowsVolumeIO(), "diskpart_remove", None)
        return bool(remover(volume, letter)) if remover else False
