"""ESP and WinRE size guards. Enlarging a partition stays opt-in.

May 2026 cumulative updates fail with 0x800F0922 when the ESP has about 10 MB
free. WinRE servicing wants 250 MB free and otherwise fails as 0x80070643.
"""
from __future__ import annotations

import os
import re

ESP_FREE_FLOOR_MB = 15
WINRE_FREE_FLOOR_MB = 250


def esp_needs_attention(free_mb: float | None) -> bool:
    if free_mb is None:
        return False
    return float(free_mb) < ESP_FREE_FLOOR_MB


def winre_needs_attention(free_mb: float | None) -> bool:
    if free_mb is None:
        return False
    return float(free_mb) < WINRE_FREE_FLOOR_MB


def cbs_has_esp_failure(text: str) -> bool:
    blob = text or ""
    if re.search(r"0x800f0922", blob, re.I):
        return True
    return bool(re.search(r"(EFI|ESP).{0,80}insufficient|insufficient.{0,80}(EFI|ESP)", blob, re.I))


def parse_reagentc_info(text: str) -> dict:
    blob = text or ""
    enabled = bool(re.search(r"Windows RE status:\s*Enabled", blob, re.I))
    location = ""
    match = re.search(r"Windows RE location:\s*(\S+)", blob, re.I)
    if match:
        location = match.group(1)
    disk = part = None
    found = re.search(r"harddisk(\d+)\\partition(\d+)", location, re.I)
    if found:
        disk, part = int(found.group(1)), int(found.group(2))
    failed = bool(re.search(r"unable to update boot configuration data", blob, re.I))
    return {"enabled": enabled, "location": location, "disk": disk, "partition": part, "bcd_update_failed": failed}


def event_4502(text: str) -> bool:
    return bool(re.search(r"\b4502\b", text or "")) and bool(re.search(r"Recovery Environment", text or "", re.I))


def esp_plan(*, free_mb: float | None, cbs_text: str = "") -> dict:
    """Clean first. Enlarge only when MAGIC_ESP_ENLARGE=1."""
    tight = esp_needs_attention(free_mb) or cbs_has_esp_failure(cbs_text)
    enlarge = os.environ.get("MAGIC_ESP_ENLARGE", "").strip().lower() in ("1", "true", "yes")
    actions = []
    if tight:
        actions.append("clean-esp-fonts")
        actions.append("remeasure")
        if enlarge:
            actions.append("opt-in-enlarge")
    return {
        "tight": tight,
        "free_mb": free_mb,
        "floor_mb": ESP_FREE_FLOOR_MB,
        "enlarge": bool(tight and enlarge),
        "actions": actions,
        "code": "0x800F0922" if cbs_has_esp_failure(cbs_text) or tight else "",
    }


def winre_plan(*, free_mb: float | None, reagentc_text: str = "", event_text: str = "") -> dict:
    """Report a small WinRE partition. Resize only when MAGIC_WINRE_RESIZE=1."""
    info = parse_reagentc_info(reagentc_text)
    tight = winre_needs_attention(free_mb) or event_4502(event_text)
    resize = os.environ.get("MAGIC_WINRE_RESIZE", "").strip().lower() in ("1", "true", "yes")
    repair = os.environ.get("MAGIC_REAGENTC_REPAIR", "").strip().lower() in ("1", "true", "yes")
    actions = []
    if tight:
        actions.append("report-winre-250mb")
        actions.append("nogo-winre-update")
        if resize:
            actions.extend(["backup-winre-wim", "opt-in-resize-kb5028997", "reagentc-enable"])
    if info["bcd_update_failed"] and repair:
        actions.extend(["reagentc-disable", "rename-reagent-xml", "reagentc-setreimage", "reagentc-enable"])
    elif info["bcd_update_failed"]:
        actions.append("reagentc-repair-opt-in")
    return {
        "tight": tight,
        "enabled": info["enabled"],
        "location": info["location"],
        "resize": bool(tight and resize),
        "actions": actions,
        "code": "0x80070643" if tight else "",
    }
