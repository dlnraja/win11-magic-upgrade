"""SetupDiag rule name to a user action. Nothing here launches Setup."""
from __future__ import annotations

from typing import Any

# Names from Microsoft's SetupDiag rule list.
RULES: dict[str, dict[str, str]] = {
    "CompatBlockedApplicationDismissable": {
        "action": "rerun-ignorewarning",
        "message": "A dismissable app block was found. Re-run Setup with /compat ignorewarning.",
        "strategy": "compat-ignorewarning",
        "label": "setupdiag-app",
    },
    "CompatBlockedFODDismissable": {
        "action": "remove-fod",
        "message": "A dismissable Feature on Demand block was found. Remove the listed FoDs, then retry.",
        "strategy": "list-fods",
        "label": "setupdiag-fod",
    },
    "HardblockMismatchedLanguage": {
        "action": "match-language",
        "message": "The install language does not match this PC. Use an ISO in the installed language.",
        "strategy": "iso-install-language",
        "label": "setupdiag-language",
    },
    "HardblockDeviceOrDriver": {
        "action": "update-driver",
        "message": "A device or driver hard-blocks Setup. Update or remove that driver, then retry.",
        "strategy": "driver-block",
        "label": "setupdiag-driver",
    },
    "GatedCompatBlock": {
        "action": "explain-hold",
        "message": "A safeguard hold is blocking this upgrade. Update the named driver before any bypass.",
        "strategy": "safeguard-hold",
        "label": "setupdiag-hold",
    },
    "PreReleaseWimMountDriverFound": {
        "action": "reregister-wimmount",
        "message": "wimmount.sys is not the inbox driver. Re-register the inbox WIM mount driver.",
        "strategy": "reregister-wimmount",
        "label": "setupdiag-wimmount",
    },
    "DiskSpaceBlockInDownLevel": {
        "action": "free-space",
        "message": "Setup stopped for lack of disk space before the upgrade started.",
        "strategy": "free-space",
        "label": "setupdiag-disk",
    },
    "DiskSpaceFailure": {
        "action": "free-space",
        "message": "Setup ran out of disk space. Free space on the Windows drive and retry.",
        "strategy": "free-space",
        "label": "setupdiag-disk",
    },
    "BootFailureDetected": {
        "action": "boot-repair",
        "message": "Setup detected a boot failure. Repair boot files before another attempt.",
        "strategy": "boot-repair",
        "label": "setupdiag-boot",
    },
    "UnsignedDriverBootFailure": {
        "action": "remove-unsigned-driver",
        "message": "An unsigned driver failed during boot. Remove it, then retry.",
        "strategy": "unsigned-driver",
        "label": "setupdiag-driver",
    },
    "DriverPackageMissingFileFailure": {
        "action": "repair-driver-package",
        "message": "A driver package is missing a file. Reinstall that driver package.",
        "strategy": "driver-package",
        "label": "setupdiag-driver",
    },
    "FindDebugInfoFromRollbackLog": {
        "action": "read-rollback",
        "message": "Setup rolled back. Read the rollback log named by SetupDiag before retrying.",
        "strategy": "rollback-log",
        "label": "setupdiag-rollback",
    },
}


def lookup_rule(name: str) -> dict[str, str] | None:
    key = (name or "").strip()
    if key in RULES:
        return dict(RULES[key])
    folded = key.lower()
    for rule, row in RULES.items():
        if rule.lower() == folded:
            return dict(row)
    return None


def actions_for_findings(findings: list[str]) -> list[dict[str, Any]]:
    """Match rule names that appear in SetupDiag finding lines."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    blob = "\n".join(findings or [])
    for name, row in RULES.items():
        if name.lower() in blob.lower() and name not in seen:
            seen.add(name)
            item = dict(row)
            item["rule"] = name
            found.append(item)
    return found
