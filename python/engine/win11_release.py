"""Detect the newest Windows 11 feature update from official Microsoft pages.

Sources (no API key, no paid service):

  * Windows release health (learn.microsoft.com) — GA version, build, cumulative
  * The public Windows 11 software-download page — product edition id + version name
  * Previously saved state, then a built-in floor if both pages fail

26H1 (build 28000) is a higher build number than 26H2 (build 26300) but Microsoft
documents it as a release for new devices, not an in-place feature update.
Selection follows the version label (26H2 > 26H1 > 25H2), not the raw build,
and drops versions the page says are not offered in place. A PC already on that
device-scoped branch stays there and only tracks its own cumulative.

The built-in floor is the last consumer security baseline known when this file
shipped (monthly B release, or a later out-of-band security update). Optional
D-week previews are parsed and ignored unless ``MAGIC_WIN11_PREVIEW=1``.
A successful parse of a newer in-place version replaces the floor. A failed or
truncated page never lowers the target below the floor or a newer cached result.
A cached cumulative that is older than the floor on the same version does not
hide the floor.
"""
from __future__ import annotations

import os
import re
import ssl
import urllib.request
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable

RELEASE_HEALTH_URL = (
    "https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information"
)
DOWNLOAD_PAGE_URL = "https://www.microsoft.com/en-us/software-download/windows11"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Win11MagicUpgrade"
)

# Last consumer security baseline verified against release health (2026-11-02).
# 26H2 October B is 26300.9711 (KB5127648). The 2026-10-26 OOB is 26300.9725
# (KB5134133) and is the floor. The same page's 26300.9818 row is the optional
# 2026-10 D preview (KB5127936) and is not the floor.
# 26H1 / 28000 is intentionally not the floor: it is not an in-place target.
FALLBACK_WIN11_VERSION = "26H2"
FALLBACK_WIN11_BUILD = 26300
FALLBACK_WIN11_UBR = 9725
PREVIEW_ENV = "MAGIC_WIN11_PREVIEW"
FALLBACK_EXCLUDED = ("26H1",)
# Current x64 consumer id, then older ids used only if the page and the first id fail.
FALLBACK_EDITION_IDS = (3813, 3816, 3321, 3324)

_EXCLUSION_PHRASE = re.compile(
    r"not designed as a feature update|not offered as an in-place update|scoped to support new devices",
    re.I,
)
_VERSION_RE = re.compile(r"\b(\d{2}H[12])\b", re.I)
_BUILD_RE = re.compile(r"\b(\d{5})\.(\d{3,5})\b")


@dataclass(frozen=True)
class ReleaseTarget:
    version: str
    major: int
    ubr: int
    source: str
    excluded: tuple[str, ...] = ()
    edition_ids: tuple[int, ...] = ()
    page_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def floor_target() -> ReleaseTarget:
    base = ReleaseTarget(
        version=FALLBACK_WIN11_VERSION,
        major=FALLBACK_WIN11_BUILD,
        ubr=FALLBACK_WIN11_UBR,
        source="builtin-floor",
        excluded=FALLBACK_EXCLUDED,
        edition_ids=FALLBACK_EDITION_IDS,
    )
    try:
        from .self_update import pin_win11_floor

        pin = pin_win11_floor()
    except Exception:
        pin = None
    if not pin:
        return base
    version, build, ubr = pin
    newer = version == base.version and (build > base.major or (build == base.major and ubr > base.ubr))
    if not newer:
        return base
    return replace(base, major=build, ubr=ubr, source="pin")


def version_key(version: str) -> tuple[int, int]:
    """(year, half). 26H2 > 26H1 > 25H2. Not comparable to a build number."""
    m = re.fullmatch(r"(\d{2})H([12])", (version or "").upper())
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


def strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script[\s\S]*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style[\s\S]*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", "\n", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    return re.sub(r"[ \t]+", " ", text)


def parse_excluded_versions(text: str) -> set[str]:
    """Versions the page says are not an in-place feature update for existing PCs.

    The version attached to a warning is the nearest ``NNH1``/``NNH2`` token
    before that warning. A later token such as "from 24H2" sits after the
    phrase and is not excluded.
    """
    excluded: set[str] = set()
    blob = text or ""
    for phrase in _EXCLUSION_PHRASE.finditer(blob):
        window = blob[max(0, phrase.start() - 400) : phrase.start()]
        versions = list(_VERSION_RE.finditer(window))
        if not versions:
            continue
        excluded.add(versions[-1].group(1).upper())
    return excluded


def _cell_text(cell_html: str) -> str:
    text = re.sub(r"(?is)<[^>]+>", " ", cell_html or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def html_tables(html: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for table in re.finditer(r"(?is)<table[\s\S]*?</table>", html or ""):
        rows: list[list[str]] = []
        for tr in re.finditer(r"(?is)<tr[\s\S]*?</tr>", table.group(0)):
            cells = [
                _cell_text(c.group(1))
                for c in re.finditer(r"(?is)<t[hd][^>]*>([\s\S]*?)</t[hd]>", tr.group(0))
            ]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


@dataclass(frozen=True)
class ServicingRow:
    version: str
    major: int
    ubr: int
    servicing: str
    # ga | security | oob | preview | ltsc
    update_class: str = "ga"


def classify_update(servicing: str, update_type: str = "") -> str:
    """Map a release-health row to ga, security, oob, preview, or ltsc.

    Microsoft's history tables label the week: B is the monthly security
    release, OOB is an out-of-band security update, and D is the optional
    non-security preview. The summary table puts that same label in
    "Latest update" while "Servicing option" still says General Availability.
    """
    svc = (servicing or "").lower()
    kind = (update_type or "").strip().lower()
    if "ltsc" in svc or "ltsc" in kind:
        return "ltsc"
    if re.search(r"(?:^|[\s._-])d$", kind) or "preview" in kind:
        return "preview"
    if "oob" in kind or "out-of-band" in kind or "out of band" in kind:
        return "oob"
    if re.search(r"(?:^|[\s._-])b$", kind) or "baseline" in kind or "security" in kind:
        return "security"
    return "ga"


def _column(header: list[str], *needles: str) -> int | None:
    for index, cell in enumerate(header):
        low = cell.lower()
        if all(needle in low for needle in needles):
            return index
    return None


def _version_before(html: str, start: int) -> str:
    window = html[max(0, start - 2500) : start]
    text = re.sub(r"(?is)<[^>]+>", " ", window)
    found = list(_VERSION_RE.finditer(text))
    return found[-1].group(1).upper() if found else ""


def _row_build(row: list[str], build_idx: int | None) -> re.Match[str] | None:
    if build_idx is not None and build_idx < len(row):
        match = _BUILD_RE.search(row[build_idx] or "")
        if match:
            return match
    return _BUILD_RE.search(" ".join(row))


def parse_servicing_rows(html: str) -> list[ServicingRow]:
    """Rows from current-version tables and from per-version history tables.

    History tables have no Version column. The version is the nearest
    ``NNH1`` / ``NNH2`` heading before the table. Update class keeps a D
    preview from replacing the B/OOB security baseline of the same version.
    """
    found: list[ServicingRow] = []
    for start, rows in _html_tables_at(html):
        header = rows[0]
        joined = " | ".join(header).lower()
        if "build" not in joined:
            continue
        has_version = "version" in joined
        has_history = "update type" in joined or "latest update" in joined
        if not has_version and not has_history:
            continue
        ver_idx = _column(header, "version") if has_version else None
        if ver_idx is not None and "revision" in header[ver_idx].lower():
            ver_idx = next(
                (
                    i
                    for i, cell in enumerate(header)
                    if "version" in cell.lower() and "revision" not in cell.lower()
                ),
                ver_idx,
            )
        svc_idx = _column(header, "servicing")
        type_idx = _column(header, "update type")
        if type_idx is None:
            type_idx = _column(header, "latest update")
        build_idx = _column(header, "latest build")
        if build_idx is None:
            build_idx = _column(header, "build")
        heading = "" if has_version else _version_before(html, start)
        for row in rows[1:]:
            build = _row_build(row, build_idx)
            if not build:
                continue
            if ver_idx is not None and ver_idx < len(row):
                ver = _VERSION_RE.search(row[ver_idx] or "")
            else:
                ver = _VERSION_RE.search(heading)
            if not ver:
                continue
            servicing = row[svc_idx] if svc_idx is not None and svc_idx < len(row) else ""
            update_type = row[type_idx] if type_idx is not None and type_idx < len(row) else ""
            found.append(
                ServicingRow(
                    version=ver.group(1).upper(),
                    major=int(build.group(1)),
                    ubr=int(build.group(2)),
                    servicing=servicing or update_type,
                    update_class=classify_update(servicing, update_type),
                )
            )
    return _dedupe_rows(found)


def _html_tables_at(html: str) -> list[tuple[int, list[list[str]]]]:
    found: list[tuple[int, list[list[str]]]] = []
    for table in re.finditer(r"(?is)<table[\s\S]*?</table>", html or ""):
        rows: list[list[str]] = []
        for tr in re.finditer(r"(?is)<tr[\s\S]*?</tr>", table.group(0)):
            cells = [
                _cell_text(c.group(1))
                for c in re.finditer(r"(?is)<t[hd][^>]*>([\s\S]*?)</t[hd]>", tr.group(0))
            ]
            if any(cells):
                rows.append(cells)
        if rows:
            found.append((table.start(), rows))
    return found


def _dedupe_rows(rows: list[ServicingRow]) -> list[ServicingRow]:
    best: dict[tuple[str, str], ServicingRow] = {}
    for row in rows:
        key = (row.version, row.update_class or "ga")
        cur = best.get(key)
        if cur is None or (row.major, row.ubr) > (cur.major, cur.ubr):
            best[key] = row
    return list(best.values())


def parse_download_page_version(html: str) -> str:
    text = strip_html(html or "")
    match = re.search(r"version\s+(\d{2}H[12])", text, re.I)
    if match:
        return match.group(1).upper()
    found = _VERSION_RE.search(text)
    return found.group(1).upper() if found else ""


def parse_edition_ids(html: str) -> list[int]:
    """Product edition ids from the software-download <select id=product-edition>."""
    if not html:
        return []
    block = re.search(
        r'(?is)<select[^>]*id=["\']product-edition["\'][^>]*>([\s\S]*?)</select>',
        html,
    )
    blob = block.group(1) if block else ""
    ids: list[int] = []
    for value in re.findall(r'(?is)<option[^>]*value=["\'](\d+)["\']', blob):
        number = int(value)
        if number not in ids:
            ids.append(number)
    return ids


def merge_edition_ids(parsed: list[int] | None) -> tuple[int, ...]:
    ids: list[int] = []
    for number in list(parsed or []) + list(FALLBACK_EDITION_IDS):
        if number not in ids:
            ids.append(number)
    return tuple(ids)


_BASELINE_CLASSES = frozenset({"ga", "security", "oob"})


def _preview_allowed(flag: bool | None) -> bool:
    if flag is not None:
        return bool(flag)
    return os.environ.get(PREVIEW_ENV, "").strip().lower() in ("1", "true", "yes")


def _best_row(rows: list[ServicingRow]) -> ServicingRow | None:
    if not rows:
        return None
    return max(rows, key=lambda row: (version_key(row.version), row.major, row.ubr))


def select_latest(
    rows: list[ServicingRow],
    excluded: set[str],
    host_build: int = 0,
    *,
    allow_preview: bool | None = None,
) -> ReleaseTarget | None:
    """Pick the latest in-place security baseline, or the host's own branch.

    B (monthly security) and OOB rows are eligible. D-week optional previews
    are not, unless ``allow_preview`` / ``MAGIC_WIN11_PREVIEW`` is set.
    A newer version that exists only as a preview does not cause an older
    feature update to be chosen; the caller keeps the floor instead.
    """
    preview = _preview_allowed(allow_preview)
    classes = set(_BASELINE_CLASSES)
    if preview:
        classes.add("preview")
    consumer = [row for row in rows if row.update_class != "ltsc"]
    excluded_up = {item.upper() for item in excluded}
    if host_build:
        on_branch = [
            row
            for row in consumer
            if row.version.upper() in excluded_up and row.major == int(host_build)
        ]
        if on_branch:
            chosen = _best_row([row for row in on_branch if row.update_class in classes])
            if chosen is None:
                return ReleaseTarget(
                    version=on_branch[0].version,
                    major=int(host_build),
                    ubr=0,
                    source="host-branch",
                    excluded=tuple(sorted(excluded_up)),
                )
            return ReleaseTarget(
                version=chosen.version,
                major=chosen.major,
                ubr=chosen.ubr,
                source="host-branch",
                excluded=tuple(sorted(excluded_up)),
            )
    eligible = [
        row
        for row in consumer
        if row.version.upper() not in excluded_up and row.update_class in classes
    ]
    best = _best_row(eligible)
    if best is None:
        return None
    if not preview:
        newer_preview = [
            row
            for row in consumer
            if row.version.upper() not in excluded_up
            and version_key(row.version) > version_key(best.version)
        ]
        if newer_preview:
            return None
    return ReleaseTarget(
        version=best.version,
        major=best.major,
        ubr=best.ubr,
        source="release-health",
        excluded=tuple(sorted(excluded_up)),
    )


def merge_release(
    *,
    live: ReleaseTarget | None,
    cache: ReleaseTarget | None,
    floor: ReleaseTarget,
    parsed_versions: set[str],
) -> ReleaseTarget:
    """Prefer a confident live result. Never adopt a truncated page over the floor."""
    if live is not None and live.source == "host-branch":
        return live
    if live is not None:
        truncated = version_key(live.version) < version_key(floor.version) and (
            floor.version.upper() not in {v.upper() for v in parsed_versions}
        )
        if not truncated:
            return live
    if cache is not None and _cache_beats_floor(cache, floor):
        return replace(cache, source="cache")
    return floor


def _cache_beats_floor(cache: ReleaseTarget, floor: ReleaseTarget) -> bool:
    """A same-version cache wins only when its cumulative is at least the floor."""
    if version_key(cache.version) != version_key(floor.version):
        return version_key(cache.version) > version_key(floor.version)
    if cache.major != floor.major:
        return cache.major > floor.major
    return cache.ubr >= floor.ubr


def _fetch_url(url: str, timeout: int = 5) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _safe_fetch(fetch: Callable[[str], str], url: str) -> str:
    try:
        return fetch(url) or ""
    except Exception:
        return ""


def cache_from_state(state: dict[str, Any] | None = None) -> ReleaseTarget | None:
    if state is None:
        from .logutil import load_state

        state = load_state()
    try:
        major = int(state.get("LatestWin11Build") or 0)
        version = str(state.get("LatestWin11Label") or "")
        ubr = int(state.get("LatestWin11Ubr") or 0)
    except (TypeError, ValueError):
        return None
    if major < 22000 or not version:
        return None
    ids = state.get("LatestWin11EditionIds") or ()
    try:
        edition_ids = tuple(int(x) for x in ids)
    except (TypeError, ValueError):
        edition_ids = ()
    excluded = state.get("LatestWin11Excluded") or ()
    return ReleaseTarget(
        version=version.upper(),
        major=major,
        ubr=max(ubr, 0),
        source="cache",
        excluded=tuple(str(x).upper() for x in excluded),
        edition_ids=edition_ids,
        page_version=str(state.get("LatestWin11PageVersion") or ""),
    )


def remember_release(target: ReleaseTarget) -> None:
    from .logutil import log, save_state

    save_state(
        {
            "LatestWin11Build": int(target.major),
            "LatestWin11Ubr": int(target.ubr),
            "LatestWin11Label": target.version,
            "LatestWin11Source": target.source,
            "LatestWin11Excluded": list(target.excluded),
            "LatestWin11EditionIds": list(target.edition_ids),
            "LatestWin11PageVersion": target.page_version,
        }
    )
    log(
        f"Latest Windows 11 target: {target.version} "
        f"{target.major}.{target.ubr or 0} ({target.source})",
        "OK",
    )


def refresh_latest_win11(
    *,
    host_build: int = 0,
    fetch_text: Callable[[str], str] | None = None,
    save: bool = True,
    state: dict[str, Any] | None = None,
    allow_preview: bool | None = None,
) -> ReleaseTarget:
    """Query Microsoft, merge with cache and the floor, optionally persist."""
    fetch = fetch_text or _fetch_url
    health = _safe_fetch(fetch, RELEASE_HEALTH_URL)
    page = _safe_fetch(fetch, DOWNLOAD_PAGE_URL)
    rows = parse_servicing_rows(health) if health else []
    excluded = parse_excluded_versions(strip_html(health)) if health else set()
    if not excluded:
        excluded = set(FALLBACK_EXCLUDED)
    live = select_latest(rows, excluded, host_build, allow_preview=allow_preview) if rows else None
    parsed_versions = {r.version for r in rows}
    cache = cache_from_state(state)
    chosen = merge_release(
        live=live,
        cache=cache,
        floor=floor_target(),
        parsed_versions=parsed_versions,
    )
    page_version = parse_download_page_version(page) if page else ""
    editions = merge_edition_ids(parse_edition_ids(page) if page else list(chosen.edition_ids))
    chosen = replace(
        chosen,
        page_version=page_version or chosen.page_version,
        edition_ids=editions,
        excluded=tuple(sorted(set(chosen.excluded) | set(excluded))),
    )
    if save:
        try:
            remember_release(chosen)
        except Exception:
            pass
    return chosen


def latest_win11_ubr_from_state() -> int:
    cached = cache_from_state()
    floor = floor_target()
    if cached and cached.ubr > 0:
        if cached.version.upper() == floor.version.upper() and cached.major == floor.major:
            return max(cached.ubr, floor.ubr)
        return cached.ubr
    if cached is None or cached.major == floor.major:
        return floor.ubr
    return 0


def win11_edition_ids() -> list[int]:
    cached = cache_from_state()
    if cached and cached.edition_ids:
        return list(merge_edition_ids(list(cached.edition_ids)))
    return list(FALLBACK_EDITION_IDS)


def nudge_cumulative_update(target_key: str) -> dict[str, Any]:
    """Ask the built-in Windows Update client to scan. No PowerShell."""
    import subprocess
    from pathlib import Path

    from .logutil import log, save_state

    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "UsoClient.exe"
    result: dict[str, Any] = {"ok": False, "target": target_key}
    from .offline import explain_network, probe_host

    try:
        probe_host("windowsupdate.microsoft.com")
    except Exception as exc:
        log(f"Windows Update skipped: {explain_network(exc)}", "WARN")
        result["reason"] = "offline"
        result["detail"] = explain_network(exc)
        return result
    if not system.is_file():
        log("UsoClient.exe is not on this OS — cumulative update left to Windows Update.", "WARN")
        result["reason"] = "no-usoclient"
        save_state({"CumulativeNudgedFor": target_key})
        return result
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for verb in ("StartScan", "StartDownload"):
        try:
            subprocess.run(
                [str(system), verb],
                capture_output=True,
                timeout=60,
                creationflags=flags,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(f"Windows Update skipped: {verb} did not respond ({exc})", "WARN")
            result["reason"] = "offline"
            result["detail"] = str(exc)
            return result
    result["ok"] = True
    save_state({"CumulativeNudgedFor": target_key})
    log(f"Windows Update nudged for cumulative {target_key}", "OK")
    return result


def parse_build_from_filename(name: str) -> int:
    """Major build embedded in an ISO file name, or 0."""
    match = re.search(r"\b(2[2-9]\d{3})(?:\.\d+)?\b", name or "")
    return int(match.group(1)) if match else 0
