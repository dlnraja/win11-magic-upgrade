"""Load the sourced error catalog and match a log or exit code to one entry."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


def catalog_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "error_catalog.json"


@lru_cache(maxsize=1)
def load_catalog() -> list[dict[str, Any]]:
    payload = json.loads(catalog_path().read_text(encoding="utf-8"))
    entries = payload.get("entries") or []
    return [item for item in entries if isinstance(item, dict) and item.get("id")]


def match_catalog(text: str, exit_code: int = 0) -> list[dict[str, Any]]:
    """Longest matching pattern wins. Later entries do not override a more specific one."""
    blob = f"{exit_code}\n{text or ''}"
    hits: list[tuple[int, dict[str, Any]]] = []
    for entry in load_catalog():
        best = 0
        for pattern in entry.get("patterns") or []:
            if re.search(str(pattern), blob, re.I):
                best = max(best, len(str(pattern)))
        if best:
            hits.append((best, entry))
    hits.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in hits]


def load_tools() -> list[dict[str, Any]]:
    payload = json.loads(catalog_path().read_text(encoding="utf-8"))
    tools = payload.get("tools") or []
    return [item for item in tools if isinstance(item, dict) and item.get("name")]


def repairs_for_ids(names: list[str]) -> list[str]:
    """Repair order from the catalog for these entry ids, each strategy once."""
    by_id = {str(entry.get("id")): entry for entry in load_catalog()}
    plan: list[str] = []
    for name in names:
        entry = by_id.get(name)
        if entry is None:
            continue
        for step in entry.get("repair") or []:
            if step not in plan:
                plan.append(str(step))
    return plan
