"""Repair before reboot from this machine's own logs and BCD backups.

Collects prior runs under %LOCALAPPDATA%\\Win11MagicUpgrade, validates every
BCD backup with a bounded ``bcdedit /store <file> /enum all``, and restores
the last known-good pre-run store when the current store is broken or was
rewritten by a heal. Partitions are never deleted.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .logutil import STATE_DIR
from .sanitize import sanitize_text

_STAMP = re.compile(r"(20\d{6}T\d{6}Z)")
_LOG_TS = re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
_BCD_MUTATION = re.compile(
    r"bcdboot|BFSVC Error|BcdOpenStore|Boot Manager repaired|INTELLIGENT BOOT REPAIR|bcdedit\s+/import|bcdedit\s+/set\s+\{bootmgr\}",
    re.I,
)
_OPEN_FAIL = re.compile(
    r"BcdOpenStore|could not be opened|ne peut pas .*ouvrir|impossible d.ouvrir|The boot configuration data store",
    re.I,
)
_LETTER = re.compile(r"\b([A-Z]):")
_VOLUME = re.compile(r"HarddiskVolume(\d+)", re.I)
_ASSIGNED = re.compile(
    r"(?:ESP via diskpart volume \d+\s*(?:→|->)\s*([A-Z]):|"
    r"ESP mounted via \w+\s*(?:→|->)\s*([A-Z]):|"
    r"assign letter=([A-Z])|"
    r"mountvol\s+([A-Z]):\s*/s)",
    re.I,
)

MAX_STORE_ENUMS = 12
STORE_TIMEOUT_S = 20
IMPORT_TIMEOUT_S = 60

Runner = Callable[[list[str], int], tuple[int, str]]


@dataclass
class BcdBackup:
    path: str
    kind: str
    stamp: str = ""
    logged_at: str = ""
    when: datetime | None = None
    valid: bool = False
    enum_code: int | None = None
    parsed: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def _parse_when(text: str) -> datetime | None:
    stamp = _STAMP.search(text or "")
    if stamp:
        try:
            return datetime.strptime(stamp.group(1), "%Y%m%dT%H%M%SZ")
        except ValueError:
            return None
    logged = _LOG_TS.search(text or "")
    if not logged:
        return None
    raw = logged.group("ts").replace("T", " ")
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def inventory_bcd_backups(root: Path) -> list[BcdBackup]:
    """Every export, partition sidecar, and ESP file copy of a BCD store."""
    found: dict[str, BcdBackup] = {}

    def add(path: Path, kind: str) -> None:
        if not path.is_file():
            return
        key = str(path.resolve()) if path.exists() else str(path)
        stamp = ""
        match = _STAMP.search(str(path))
        if match:
            stamp = match.group(1)
        item = BcdBackup(path=str(path), kind=kind, stamp=stamp, when=_parse_when(str(path)))
        found[key.lower()] = item

    exports = root / "bcd-backups"
    if exports.is_dir():
        for path in exports.iterdir():
            if path.is_file() and path.name.lower().startswith("bcd-export"):
                add(path, "export")
    parts = root / "partition-backups"
    if parts.is_dir():
        for path in parts.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(parts).as_posix().lower()
            if path.name.lower() == "bcd-export":
                add(path, "partition-sidecar")
            elif rel.endswith("efi/microsoft/boot/bcd") or rel.endswith("boot/bcd"):
                add(path, "esp-file-copy")
    snaps = root / "boot-snapshots"
    if snaps.is_dir():
        for path in snaps.rglob("BCD"):
            if path.is_file():
                add(path, "snapshot")
    return sorted(found.values(), key=lambda item: item.when or datetime.min, reverse=True)


def attach_export_times(backups: list[BcdBackup], log_text: str) -> None:
    """Use the log clock (for example 18:38:05) when the export line names the file."""
    for line in (log_text or "").splitlines():
        if "bcd backup exported" not in line.lower():
            continue
        when = _parse_when(line)
        if when is None:
            continue
        for item in backups:
            name = Path(item.path).name.lower()
            # A bare "BCD" file must not match the words "BCD backup".
            if not name or not any(ch.isdigit() for ch in name):
                continue
            if name in line.lower():
                item.logged_at = when.strftime("%Y-%m-%d %H:%M:%S")
                item.when = when


def parse_store_enum(text: str) -> dict[str, Any]:
    """Pull {bootmgr} and the Windows loader out of ``bcdedit /enum`` text."""
    if _OPEN_FAIL.search(text or ""):
        return {"ok": False, "error": "open_failed", "bootmgr_device": "", "objects": []}
    objects: list[dict[str, Any]] = []
    title = ""
    current: dict[str, Any] | None = None
    for line in (text or "").splitlines():
        stripped = line.strip()
        if re.fullmatch(r"-{3,}", stripped):
            current = {"title": title, "fields": {}}
            objects.append(current)
            title = ""
            continue
        if not stripped:
            continue
        matched = re.match(r"^(.+?)\s{2,}(\S.*)$", line.rstrip())
        if matched and current is not None:
            current["fields"][matched.group(1).strip().lower()] = matched.group(2).strip()
        else:
            title = stripped
    bootmgr = ""
    loader: dict[str, str] = {}
    for obj in objects:
        fields = obj["fields"]
        ident = fields.get("identifier", "")
        if ident == "{bootmgr}" or "boot manager" in obj["title"].lower():
            bootmgr = fields.get("device", bootmgr)
        if ident in ("{default}", "{current}") or "boot loader" in obj["title"].lower():
            if ident == "{default}" or not loader:
                loader = fields
    path = loader.get("path", "")
    return {
        "ok": True,
        "error": "",
        "bootmgr_device": bootmgr,
        "device": loader.get("device", ""),
        "osdevice": loader.get("osdevice", ""),
        "path": path,
        "systemroot": loader.get("systemroot", ""),
        "identifier": loader.get("identifier", ""),
        "objects": objects,
    }


def store_is_known_good(parsed: dict[str, Any], *, uefi: bool = True) -> bool:
    if not parsed or not parsed.get("ok"):
        return False
    path = str(parsed.get("path") or "")
    if uefi and "winload.efi" not in path.lower():
        return False
    if not uefi and "winload" not in path.lower():
        return False
    if not parsed.get("bootmgr_device"):
        return False
    if not parsed.get("systemroot"):
        return False
    if not (parsed.get("osdevice") or parsed.get("device")):
        return False
    return True


def store_signature(parsed: dict[str, Any]) -> dict[str, str]:
    return {
        "bootmgr_device": str(parsed.get("bootmgr_device") or ""),
        "device": str(parsed.get("device") or ""),
        "osdevice": str(parsed.get("osdevice") or ""),
        "path": str(parsed.get("path") or ""),
        "systemroot": str(parsed.get("systemroot") or ""),
    }


def diff_stores(current: dict[str, Any], backup: dict[str, Any]) -> list[str]:
    left = store_signature(current)
    right = store_signature(backup)
    changes = []
    for key in left:
        if left[key] != right[key]:
            changes.append(f"{key}: backup={right[key]!s} current={left[key]!s}")
    return changes


def first_bcd_mutation(log_text: str) -> datetime | None:
    earliest: datetime | None = None
    for line in (log_text or "").splitlines():
        if not _BCD_MUTATION.search(line):
            continue
        when = _parse_when(line)
        if when is None:
            continue
        if earliest is None or when < earliest:
            earliest = when
    return earliest


def choose_known_good(backups: list[BcdBackup], heal_at: datetime | None) -> BcdBackup | None:
    """Latest valid backup taken at or before the first BCD rewrite."""
    pre = []
    for item in backups:
        if not item.valid or item.when is None:
            continue
        if heal_at is not None and item.when > heal_at:
            continue
        pre.append(item)
    if not pre:
        return None
    pre.sort(key=lambda item: item.when or datetime.min)
    return pre[-1]


def analyze_history(log_text: str) -> dict[str, Any]:
    """What earlier runs changed: shrink, new ESP, fonts, BCD, leftover letters."""
    text = log_text or ""
    letters: list[str] = []
    for match in _ASSIGNED.finditer(text):
        letter = next(group for group in match.groups() if group)
        upper = letter.upper()
        if upper not in letters and upper not in {"C"}:
            letters.append(upper)
    shrink = bool(re.search(r"shrink C:|Creating new \d+ MB EFI|Creating new \d+ MB System", text, re.I))
    new_esp = bool(re.search(r"Creating new \d+ MB EFI|Boot partition created|partition 6", text, re.I))
    fonts = bool(re.search(r"Deleted \d+ font", text, re.I))
    bcd_edit = bool(_BCD_MUTATION.search(text))
    false_ok = bool(re.search(r"exit 32|BcdOpenStore", text, re.I) and re.search(r"\bOK\b|successfully", text, re.I))
    return {
        "shrunk_c": shrink,
        "created_esp": new_esp,
        "deleted_fonts": fonts,
        "bcd_modified": bcd_edit,
        "bcdboot_false_success": false_ok,
        "letters_assigned": letters,
        "heal_at": first_bcd_mutation(text),
    }


def _device_matches(device: str, esp: dict[str, Any]) -> bool:
    if not device:
        return False
    letter = str(esp.get("letter") or "").upper().rstrip(":")
    if letter and re.search(rf"partition={letter}:", device, re.I):
        return True
    volume = esp.get("volume")
    match = _VOLUME.search(device)
    if volume is not None and match and int(match.group(1)) == int(volume):
        return True
    return False


def choose_active_esp(esps: list[dict[str, Any]], bootmgr_device: str, firmware_text: str) -> dict[str, Any]:
    """The ESP {bootmgr} and the firmware entry already point at. Others stay."""
    firmware_device = ""
    parsed = parse_store_enum(firmware_text or "")
    firmware_device = str(parsed.get("bootmgr_device") or "")
    if not firmware_device:
        for line in (firmware_text or "").splitlines():
            if "device" in line.lower() and "partition" in line.lower():
                firmware_device = line.split(None, 1)[-1].strip()
                break
    boot_hits = [esp for esp in esps if _device_matches(bootmgr_device, esp)]
    firm_hits = [esp for esp in esps if _device_matches(firmware_device, esp)]
    if boot_hits and firm_hits and boot_hits[0] is not firm_hits[0]:
        return {"target": None, "others": esps, "conflict": True, "firmware_device": firmware_device}
    target = (boot_hits or firm_hits or [None])[0]
    others = [esp for esp in esps if esp is not target]
    return {"target": target, "others": others, "conflict": False, "firmware_device": firmware_device}


def locate_font_backup(root: Path) -> Path | None:
    parts = root / "partition-backups"
    if not parts.is_dir():
        return None
    candidates = []
    for folder in parts.rglob("Fonts"):
        if not folder.is_dir():
            continue
        if folder.as_posix().lower().endswith("efi/microsoft/boot/fonts") and any(folder.rglob("*.ttf")):
            candidates.append(folder)
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.as_posix(), reverse=True)[0]


def build_repair_plan(
    *,
    history: dict[str, Any],
    backups: list[BcdBackup],
    current: dict[str, Any],
    current_code: int,
    esps: list[dict[str, Any]],
    firmware_text: str,
    font_backup: Path | None,
) -> dict[str, Any]:
    """Undo only what the logs show. Never delete a partition."""
    heal_at = history.get("heal_at")
    chosen = choose_known_good(backups, heal_at if isinstance(heal_at, datetime) else None)
    current_good = current_code == 0 and store_is_known_good(current)
    changes = diff_stores(current, chosen.parsed) if chosen else []
    need_import = bool(chosen) and (
        not current_good or (bool(history.get("bcd_modified")) and bool(changes))
    )
    selection = choose_active_esp(esps, str(current.get("bootmgr_device") or ""), firmware_text)
    if chosen and chosen.parsed.get("bootmgr_device"):
        # The known-good store names the ESP firmware should keep using.
        preferred = choose_active_esp(esps, str(chosen.parsed.get("bootmgr_device") or ""), firmware_text)
        if preferred.get("target") is not None and not preferred.get("conflict"):
            selection = preferred
    actions: list[str] = ["never_delete_partitions"]
    if history.get("deleted_fonts") and font_backup is not None and selection.get("target"):
        actions.append(f"restore_fonts:{font_backup}")
    for letter in history.get("letters_assigned") or []:
        actions.append(f"remove_leftover_letter:{letter}")
    if need_import and chosen is not None:
        actions.append("export_current_store")
        actions.append(f"bcdedit_import:{chosen.path}")
        if selection.get("target"):
            actions.append("copy_store_to_active_esp")
    elif chosen is None and not current_good:
        actions.append("no_known_good_prerun_bcd")
    if selection.get("conflict"):
        actions.append("firmware_conflict_no_repoint")
    elif selection.get("target") is not None and need_import:
        actions.append("repoint_bootmgr_to_active_esp")
    actions.append("leave_other_esp_untouched")
    # bcdboot is not a substitute for a bad exit. It runs only when the active
    # ESP has no loader at all and a known-good store exists to point at.
    target = selection.get("target") or {}
    if target and not target.get("has_bootmgfw") and chosen is not None:
        actions.append("bcdboot_active_esp_if_loader_missing")
    go_blockers = []
    if selection.get("conflict"):
        go_blockers.append("bootmgr_and_firmware_disagree")
    if not current_good and chosen is None:
        go_blockers.append("no_known_good_bcd")
    if history.get("bcdboot_false_success") and not need_import and not current_good:
        go_blockers.append("bcdboot_exit_not_trusted")
    return {
        "chosen_backup": chosen.path if chosen else "",
        "chosen_kind": chosen.kind if chosen else "",
        "chosen_stamp": chosen.stamp if chosen else "",
        "chosen_logged_at": chosen.logged_at if chosen else "",
        "need_import": need_import,
        "current_good": current_good,
        "store_changes": changes,
        "target_esp": selection.get("target"),
        "other_esps": selection.get("others") or [],
        "conflict": bool(selection.get("conflict")),
        "actions": actions,
        "delete_partitions": False,
        "go_blockers": go_blockers,
    }


def format_summary(report: dict[str, Any]) -> str:
    plan = report.get("plan") or {}
    history = report.get("history") or {}
    verdict = report.get("verdict") or "NO-GO"
    lines = [
        f"REPAIR BEFORE REBOOT: {verdict}",
        "",
        "History reconstructed from this machine's logs:",
        f"  shrink C: {bool(history.get('shrunk_c'))}",
        f"  new ESP created: {bool(history.get('created_esp'))}",
        f"  EFI fonts deleted: {bool(history.get('deleted_fonts'))}",
        f"  BCD modified by a heal: {bool(history.get('bcd_modified'))}",
        f"  letters left assigned: {', '.join(history.get('letters_assigned') or []) or 'none'}",
        "",
        "BCD backups inventoried:",
    ]
    for item in report.get("backups") or []:
        flag = "valid" if item.get("valid") else "not-valid"
        when = item.get("logged_at") or item.get("stamp") or "untimed"
        lines.append(f"  [{flag}] {when} {item.get('kind')} {item.get('path')}")
    lines.append("")
    if plan.get("chosen_backup"):
        lines.append(
            "Chosen last known-good pre-run BCD: "
            f"{plan.get('chosen_logged_at') or plan.get('chosen_stamp') or 'unstamped'} "
            f"({plan.get('chosen_kind')}) {plan.get('chosen_backup')}"
        )
    else:
        lines.append("Chosen last known-good pre-run BCD: none")
    if plan.get("store_changes"):
        lines.append("Diff against the current store:")
        for change in plan["store_changes"]:
            lines.append(f"  {change}")
    lines.append("Partitions will not be deleted. The other ESP is left in place.")
    lines.append("Actions:")
    for action in plan.get("actions") or []:
        lines.append(f"  - {action}")
    if report.get("applied"):
        lines.append("")
        lines.append("Applied:")
        for action in report["applied"]:
            lines.append(f"  - {action}")
    lines.append("")
    if verdict == "GO":
        lines.append("GO for reboot. {bootmgr} device, {default} osdevice/path winload.efi, and systemroot checked out.")
    else:
        lines.append("NO-GO for reboot. Do not restart until the lines above are resolved.")
        for blocker in plan.get("go_blockers") or []:
            lines.append(f"  blocker: {blocker}")
    return "\n".join(lines) + "\n"


def _read_logs(root: Path) -> str:
    chunks: list[str] = []
    for folder in (root / "logs", root / "Panther", root):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.log")) + sorted(folder.glob("*.txt")):
            if path.name.lower() in {"last.txt"}:
                continue
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace")[:400_000])
            except OSError:
                continue
    for name in ("setupact.log", "setuperr.log"):
        path = root / "Panther" / name
        if path.is_file():
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace")[:400_000])
            except OSError:
                pass
    return "\n".join(chunks)


def _default_runner(cmd: list[str], timeout: int) -> tuple[int, str]:
    from .proc_guard import run_bounded

    result = run_bounded(cmd, timeout=timeout, reject_patterns=(r"BcdOpenStore|BFSVC Error",))
    return int(result.code), result.output or ""


def _validate_backup(item: BcdBackup, runner: Runner, uefi: bool) -> None:
    code, text = runner(["bcdedit", "/store", item.path, "/enum", "all"], STORE_TIMEOUT_S)
    item.enum_code = code
    item.parsed = parse_store_enum(text)
    item.valid = code == 0 and store_is_known_good(item.parsed, uefi=uefi)
    if not item.valid:
        item.reason = item.parsed.get("error") or f"exit {code}"


def _copy_fonts(source: Path, esp_root: Path) -> int:
    dest = esp_root / "EFI" / "Microsoft" / "Boot" / "Fonts"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for file in source.rglob("*"):
        if not file.is_file():
            continue
        target = dest / file.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        copied += 1
    return copied


def _write_bundle(root: Path, report: dict[str, Any], log_text: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = root / "repair-before-reboot" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = sanitize_text(format_summary(report))
    (out_dir / "SUMMARY.txt").write_text(summary, encoding="utf-8")
    payload = {
        "verdict": report.get("verdict"),
        "history": {key: value for key, value in (report.get("history") or {}).items() if key != "heal_at"},
        "chosen_backup": (report.get("plan") or {}).get("chosen_backup"),
        "actions": (report.get("plan") or {}).get("actions"),
        "applied": report.get("applied"),
        "delete_partitions": False,
    }
    (out_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    redacted_log = sanitize_text(log_text[-100_000:])
    (out_dir / "logs-redacted.txt").write_text(redacted_log, encoding="utf-8")
    bundle = out_dir / "repair-before-reboot-diagnostic.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(out_dir / "SUMMARY.txt", "SUMMARY.txt")
        archive.write(out_dir / "summary.json", "summary.json")
        archive.write(out_dir / "logs-redacted.txt", "logs-redacted.txt")
    report["bundle"] = str(bundle)
    report["summary_text"] = summary
    return bundle


def repair_before_reboot(
    *,
    root: Path | None = None,
    runner: Runner | None = None,
    apply: bool = True,
    esps: list[dict[str, Any]] | None = None,
    firmware_text: str = "",
    uefi: bool = True,
    unmount_letter: Callable[[str], None] | None = None,
    esp_root_for: Callable[[dict[str, Any]], Path | None] | None = None,
) -> dict[str, Any]:
    """Inventory, validate, restore the pre-run BCD, and print GO or NO-GO."""
    state = root or STATE_DIR
    run = runner or _default_runner
    log_text = _read_logs(state)
    backups = inventory_bcd_backups(state)
    attach_export_times(backups, log_text)
    # Bound the live bcdedit work. Newest exports are first.
    for item in backups[:MAX_STORE_ENUMS]:
        _validate_backup(item, run, uefi)
    current_code, current_text = run(["bcdedit", "/enum", "all"], STORE_TIMEOUT_S)
    current = parse_store_enum(current_text)
    if not firmware_text:
        _firm_code, firmware_text = run(["bcdedit", "/enum", "firmware"], STORE_TIMEOUT_S)
    history = analyze_history(log_text)
    plan = build_repair_plan(
        history=history,
        backups=backups,
        current=current,
        current_code=current_code,
        esps=list(esps or []),
        firmware_text=firmware_text,
        font_backup=locate_font_backup(state),
    )
    applied: list[str] = []
    verify: dict[str, Any] = {}
    if apply and plan.get("need_import") and plan.get("chosen_backup"):
        safety = state / "bcd-backups"
        safety.mkdir(parents=True, exist_ok=True)
        export_path = safety / f"pre-import-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        export_code, _export_out = run(["bcdedit", "/export", str(export_path)], IMPORT_TIMEOUT_S)
        applied.append(f"export_current:{export_code}:{export_path}")
        if export_code == 0:
            import_code, import_out = run(["bcdedit", "/import", plan["chosen_backup"]], IMPORT_TIMEOUT_S)
            applied.append(f"import:{import_code}")
            if import_code != 0 or _OPEN_FAIL.search(import_out or ""):
                plan.setdefault("go_blockers", []).append("import_failed")
            else:
                verify_code, verify_text = run(["bcdedit", "/enum", "all"], STORE_TIMEOUT_S)
                verify = parse_store_enum(verify_text)
                verify["code"] = verify_code
                if verify_code != 0 or not store_is_known_good(verify, uefi=uefi):
                    plan.setdefault("go_blockers", []).append("post_import_verify_failed")
                target = plan.get("target_esp") or {}
                if esp_root_for is not None and target:
                    esp_root = esp_root_for(target)
                    if esp_root is not None:
                        dest = esp_root / "EFI" / "Microsoft" / "Boot" / "BCD"
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if dest.is_file():
                            shutil.copy2(dest, safety / f"esp-bcd-before-import-{dest.stat().st_mtime_ns}")
                        shutil.copy2(plan["chosen_backup"], dest)
                        applied.append(f"copied_store_to:{dest}")
                        store_code, store_text = run(["bcdedit", "/store", str(dest), "/enum", "all"], STORE_TIMEOUT_S)
                        store_parsed = parse_store_enum(store_text)
                        if store_code != 0 or not store_is_known_good(store_parsed, uefi=uefi):
                            plan.setdefault("go_blockers", []).append("esp_store_verify_failed")
        else:
            plan.setdefault("go_blockers", []).append("export_before_import_failed")
    if apply and history.get("deleted_fonts"):
        fonts = locate_font_backup(state)
        target = plan.get("target_esp") or {}
        if fonts is not None and esp_root_for is not None and target:
            esp_root = esp_root_for(target)
            if esp_root is not None:
                copied = _copy_fonts(fonts, esp_root)
                applied.append(f"restored_fonts:{copied}")
    if apply and unmount_letter is not None:
        for letter in history.get("letters_assigned") or []:
            unmount_letter(letter)
            applied.append(f"removed_letter:{letter}")
    blockers = list(plan.get("go_blockers") or [])
    if plan.get("need_import") and not any(item.startswith("import:0") for item in applied):
        if apply:
            blockers.append("import_not_applied")
    verdict = "GO" if apply and not blockers and (plan.get("current_good") or plan.get("need_import")) else "NO-GO"
    if not apply:
        verdict = "NO-GO" if blockers else "REVIEW"
    report = {
        "verdict": verdict,
        "history": {**history, "heal_at": history["heal_at"].isoformat(sep=" ") if history.get("heal_at") else ""},
        "backups": [
            {
                "path": item.path,
                "kind": item.kind,
                "stamp": item.stamp,
                "logged_at": item.logged_at,
                "valid": item.valid,
                "enum_code": item.enum_code,
                "reason": item.reason,
            }
            for item in backups
        ],
        "plan": plan,
        "applied": applied,
        "verify": {key: value for key, value in verify.items() if key != "objects"},
        "delete_partitions": False,
    }
    _write_bundle(state, report, log_text)
    return report


_SERIAL_FIELD = re.compile(r"(?i)(serial(?:\s*number)?\s*[:=]\s*)([A-Za-z0-9._\-]+)")
_ISSUE_REPO = "dlnraja/win11-magic-upgrade"


def upgrade_allowed(verdict: str) -> bool:
    """One-Click continues the upgrade only after a GO verdict."""
    return (verdict or "").strip().upper() == "GO"


def redact_issue_text(text: str) -> str:
    cleaned = sanitize_text(text or "")
    return _SERIAL_FIELD.sub(r"\1<SERIAL>", cleaned)


def verdict_message(verdict: str, zip_path: str) -> str:
    if upgrade_allowed(verdict):
        head = "GO — the boot repair checked out. The upgrade can continue."
    else:
        head = "NO-GO — the upgrade will not continue. Do not reboot until this is resolved."
    shown = redact_issue_text(zip_path or "")
    return head + "\n\nDiagnostic zip (drag this file onto the GitHub issue):\n" + shown


def repair_issue_url(report: dict[str, Any], *, repo: str = _ISSUE_REPO) -> str:
    """Browser issue form. No token is placed in the URL or the exe."""
    from urllib.parse import urlencode

    verdict = str(report.get("verdict") or "NO-GO")
    body = redact_issue_text(str(report.get("summary_text") or ""))
    body += "\n\nDrag this diagnostic zip onto the issue:\n"
    body += redact_issue_text(str(report.get("bundle") or "")) + "\n"
    query = urlencode(
        {
            "title": f"One-Click repair before reboot: {verdict}"[:140],
            "body": body[:5000],
        }
    )
    return f"https://github.com/{repo}/issues/new?{query}"


def open_repair_issue(report: dict[str, Any]) -> str:
    url = repair_issue_url(report)
    if os.environ.get("MAGIC_NO_BROWSER", "").strip().lower() in ("1", "true", "yes"):
        return url
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass
    return url
