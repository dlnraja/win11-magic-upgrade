"""
ProfileList audit for DuplicateUserProfileFailure (SetupDiag BD7B3109…).

Research: MS SetupDiag + Q&A 0x000004E5 multiple SIDs → same ProfileImagePath.
Safe mode: detect + report; optional rename of clearly orphan .bak keys when
MAGIC_FIX_PROFILE_BAK=1 (never delete live profiles).
"""
from __future__ import annotations

import os
import winreg
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from .logutil import STATE_DIR, log, save_state

PROFILE_LIST = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"


@dataclass
class ProfileAudit:
    entries: int = 0
    bak_keys: list[str] = field(default_factory=list)
    duplicate_paths: dict[str, list[str]] = field(default_factory=dict)
    missing_folders: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fixed_bak: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_profile_list(*, apply_bak_rename: bool | None = None) -> ProfileAudit:
    audit = ProfileAudit()
    if apply_bak_rename is None:
        apply_bak_rename = os.environ.get("MAGIC_FIX_PROFILE_BAK", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
    path_to_sids: dict[str, list[str]] = defaultdict(list)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PROFILE_LIST) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break
                audit.entries += 1
                if sub.lower().endswith(".bak"):
                    audit.bak_keys.append(sub)
                try:
                    with winreg.OpenKey(root, sub) as k:
                        try:
                            img, _ = winreg.QueryValueEx(k, "ProfileImagePath")
                            img = str(img or "")
                        except OSError:
                            img = ""
                except OSError:
                    continue
                if img:
                    expanded = os.path.expandvars(img)
                    path_to_sids[expanded.lower()].append(sub)
                    if not os.path.isdir(expanded) and not sub.startswith("S-1-5-18"):
                        # Skip well-known system if missing
                        if "windows\\system32\\config\\systemprofile" not in expanded.lower():
                            audit.missing_folders.append(f"{sub} → {expanded}")
    except OSError as e:
        audit.notes.append(f"ProfileList open failed: {e}")
        save_state({"ProfileAudit": audit.as_dict()})
        return audit

    for path, sids in path_to_sids.items():
        if len(sids) > 1:
            audit.duplicate_paths[path] = sids

    if audit.bak_keys:
        log(
            f"ProfileList .bak keys ({len(audit.bak_keys)}): "
            + ", ".join(audit.bak_keys[:6]),
            "WARN",
        )
        audit.notes.append(
            "SetupDiag DuplicateUserProfileFailure often involves .bak SID keys. "
            "Backup user data before removing unused accounts."
        )
    if audit.duplicate_paths:
        for path, sids in list(audit.duplicate_paths.items())[:8]:
            log(f"Duplicate ProfileImagePath {path}: {', '.join(sids)}", "WARN")
        audit.notes.append(
            "Multiple SIDs share one profile path (0x000004E5 risk). "
            "Create a fresh local admin, migrate files, remove unused account — do not copy NTUSER.DAT."
        )
    if audit.missing_folders:
        log(f"Profile folders missing: {len(audit.missing_folders)}", "WARN")

    # Optional: rename stale .bak keys that point to missing folders (safer than delete)
    if apply_bak_rename and audit.bak_keys:
        for sid in list(audit.bak_keys):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PROFILE_LIST + "\\" + sid) as k:
                    try:
                        img, _ = winreg.QueryValueEx(k, "ProfileImagePath")
                        img = os.path.expandvars(str(img or ""))
                    except OSError:
                        img = ""
                if img and not os.path.isdir(img):
                    new_name = sid + ".magic-disabled"
                    # Reg rename via copy+delete not available easily — set flag value instead
                    with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        PROFILE_LIST + "\\" + sid,
                        0,
                        winreg.KEY_SET_VALUE,
                    ) as k:
                        winreg.SetValueEx(k, "MagicUpgradeDisabled", 0, winreg.REG_DWORD, 1)
                    audit.fixed_bak.append(sid)
                    log(f"Marked stale .bak profile (missing folder): {sid}", "OK")
            except OSError as e:
                audit.notes.append(f"bak mark failed {sid}: {e}")

    try:
        import json

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "profile-audit.json").write_text(
            json.dumps(audit.as_dict(), indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    save_state({"ProfileAudit": audit.as_dict()})
    if not audit.bak_keys and not audit.duplicate_paths:
        log("ProfileList audit: no duplicate/.bak issues detected", "OK")
    return audit
