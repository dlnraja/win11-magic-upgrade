"""Segmented, resumable image download. Standard library only.

Four to eight HTTP Range connections. A server that ignores Range falls back
to one stream. Each segment retries with backoff. Progress (speed, ETA, bytes,
and per-segment percent) is reported for the GUI and the log. The source URL
is logged with the query string removed. A SHA-256 mismatch deletes the file
and tries again, at most three times.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_UA = "Win11MagicUpgrade"
_HASH_ATTEMPTS = 3
_CHUNK = 256 * 1024


@dataclass
class DownloadProgress:
    done: int
    total: int
    speed_bps: float
    eta_seconds: float | None
    segments: list[tuple[int, int]]
    detail: str


@dataclass
class _Probe:
    ranged: bool
    total: int
    etag: str


@dataclass
class _Job:
    url: str
    dest: Path
    state_path: Path
    state: dict
    opener: Callable
    timeout: int
    retries: int
    chunk_size: int
    sleep: Callable[[float], None]
    log: Callable[[str, str], None]
    stop: threading.Event = field(default_factory=threading.Event)
    fallback: threading.Event = field(default_factory=threading.Event)
    problems: list[BaseException] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def redact_url(url: str) -> str:
    """Drop user info and the query string. The host and file name stay."""
    text = (url or "").strip()
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return "[url]"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return text or "[url]"
    host = parts.hostname
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    query = "?[redacted]" if parts.query else ""
    return f"{parts.scheme}://{host}{path}{query}"


def redact_text(text: str) -> str:
    return re.sub(r"https?://[^\s'\"<>]+", lambda match: redact_url(match.group(0)), text or "")


def official_sha256(record: dict | None) -> str:
    """Return a 64-hex hash from a download record, or an empty string."""
    for key in ("Sha256", "SHA256", "sha256", "Hash", "FileHash"):
        value = str((record or {}).get(key) or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    return ""


def plan_segments(total: int, workers: int) -> list[tuple[int, int]]:
    """Inclusive byte spans that cover ``total`` once."""
    if total <= 0 or workers <= 0:
        return []
    workers = min(workers, total)
    base, extra = divmod(total, workers)
    spans: list[tuple[int, int]] = []
    start = 0
    for index in range(workers):
        length = base + (1 if index < extra else 0)
        if length <= 0:
            break
        end = start + length - 1
        spans.append((start, end))
        start = end + 1
    return spans


def resolve_workers(requested: int | None, total: int, ranged: bool) -> int:
    if not ranged or total <= 0:
        return 1
    if requested is not None:
        return max(1, min(8, int(requested)))
    if total < 4 * 1024 * 1024:
        return 1
    raw = os.environ.get("MAGIC_DOWNLOAD_SEGMENTS", "6")
    try:
        count = int(str(raw).strip())
    except (TypeError, ValueError):
        count = 6
    return max(4, min(8, count))


def segment_percents(segments: list[dict]) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    for item in segments:
        length = int(item["end"]) - int(item["start"]) + 1
        done = min(int(item.get("done") or 0), length)
        pct = 100 if length <= 0 else int(100 * done / length)
        rows.append((done, pct))
    return rows


def _default_log(message: str, level: str = "INFO") -> None:
    try:
        from .logutil import log

        log(redact_text(message), level)
    except Exception:
        print(redact_text(message))


def _default_opener():
    context = ssl.create_default_context()

    def _open(request, timeout=120):
        return urllib.request.urlopen(request, timeout=timeout, context=context)

    return _open


def _headers_get(headers, name: str) -> str:
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    value = getter(name)
    if value is None:
        value = getter(name.lower())
    return str(value or "")


def _status_of(response) -> int:
    return int(getattr(response, "status", None) or response.getcode() or 0)


def _state_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".parts.json")


def _url_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _load_state(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_state(path: Path, state: dict, lock: threading.Lock) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(path)


def _discard(dest: Path) -> None:
    for path in (dest, _state_path(dest)):
        try:
            path.unlink()
        except OSError:
            pass


def _request(url: str, range_header: str | None = None) -> urllib.request.Request:
    headers = {"User-Agent": _UA}
    if range_header:
        headers["Range"] = range_header
    return urllib.request.Request(url, headers=headers)


def _probe(opener, url: str, timeout: int) -> _Probe:
    request = _request(url, "bytes=0-0")
    with opener(request, timeout=timeout) as response:
        status = _status_of(response)
        etag = _headers_get(response.headers, "ETag")
        if status == 206:
            match = re.search(r"/(\d+)\s*$", _headers_get(response.headers, "Content-Range"))
            total = int(match.group(1)) if match else 0
            return _Probe(total > 1, total, etag)
        length = _headers_get(response.headers, "Content-Length")
        total = int(length) if length.isdigit() else 0
        return _Probe(False, total, etag)


def _compatible(saved: dict, probe: _Probe, url: str) -> bool:
    if int(saved.get("total") or 0) != probe.total:
        return False
    saved_tag = str(saved.get("etag") or "")
    if saved_tag and probe.etag and saved_tag != probe.etag:
        return False
    if str(saved.get("url_sha256") or "") == _url_id(url):
        return True
    return probe.total > 0


def _detail(label: str, done: int, total: int, speed: float, eta: float | None, segments: list[dict]) -> str:
    from .progress import format_bytes, format_eta, format_speed

    percents = [str(pct) for _, pct in segment_percents(segments)]
    bits = []
    if percents:
        bits.append("[" + "|".join(percents) + "]")
    if total > 0:
        bits.append(f"{format_bytes(done)} / {format_bytes(total)}")
    else:
        bits.append(format_bytes(done))
    bits.append(format_speed(speed))
    bits.append(f"ETA {format_eta(eta)}")
    prefix = f"{label} " if label else ""
    return prefix + " · ".join(bits)


def _snapshot(label: str, state: dict, started: float, base: int) -> DownloadProgress:
    segments = list(state.get("segments") or [])
    if segments:
        done = sum(min(int(item.get("done") or 0), int(item["end"]) - int(item["start"]) + 1) for item in segments)
        total = int(state.get("total") or 0)
    else:
        done = int(state.get("done") or 0)
        total = int(state.get("total") or 0)
    elapsed = max(time.time() - started, 0.001)
    speed = max(done - base, 0) / elapsed
    remain = max(total - done, 0) if total else 0
    eta = (remain / speed) if speed > 0 and total else None
    return DownloadProgress(
        done=done,
        total=total,
        speed_bps=speed,
        eta_seconds=eta,
        segments=segment_percents(segments),
        detail=_detail(label, done, total, speed, eta, segments),
    )


def _extend(dest: Path, total: int) -> None:
    if total <= 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    mode = "r+b" if dest.exists() else "wb"
    with dest.open(mode) as handle:
        handle.seek(total - 1)
        handle.write(b"\0")


def _run_segment(job: _Job, segment: dict) -> None:
    length = int(segment["end"]) - int(segment["start"]) + 1
    for attempt in range(1, job.retries + 1):
        if job.stop.is_set() or job.fallback.is_set():
            return
        if int(segment.get("done") or 0) >= length:
            return
        start = int(segment["start"]) + int(segment.get("done") or 0)
        try:
            request = _request(job.url, f"bytes={start}-{int(segment['end'])}")
            with job.opener(request, timeout=job.timeout) as response:
                status = _status_of(response)
                if status == 200:
                    job.fallback.set()
                    job.stop.set()
                    return
                if status != 206:
                    raise OSError(f"HTTP {status}")
                with job.dest.open("r+b") as handle:
                    handle.seek(start)
                    while int(segment.get("done") or 0) < length:
                        if job.stop.is_set():
                            return
                        chunk = response.read(job.chunk_size)
                        if not chunk:
                            break
                        room = length - int(segment.get("done") or 0)
                        chunk = chunk[:room]
                        handle.write(chunk)
                        handle.flush()
                        segment["done"] = int(segment.get("done") or 0) + len(chunk)
                        _save_state(job.state_path, job.state, job.lock)
            if int(segment.get("done") or 0) < length:
                raise OSError("short read")
            return
        except Exception as exc:
            _save_state(job.state_path, job.state, job.lock)
            if job.fallback.is_set() or job.stop.is_set():
                return
            if attempt >= job.retries:
                job.problems.append(exc)
                job.stop.set()
                job.log(
                    f"segment {segment['start']}- failed: {redact_text(str(exc))}",
                    "WARN",
                )
                return
            delay = min(8.0, 0.5 * (2 ** (attempt - 1)))
            job.log(
                f"segment {segment['start']}- retry {attempt}/{job.retries} in {delay:.1f}s",
                "WARN",
            )
            job.sleep(delay)


def _single(job: _Job, label: str, on_progress: Callable[[DownloadProgress], None] | None) -> None:
    total = int(job.state.get("total") or 0)
    for attempt in range(1, job.retries + 1):
        done = int(job.state.get("done") or 0)
        if total and done > total:
            done = 0
        range_header = f"bytes={done}-" if done else None
        started = time.time()
        base = done
        try:
            request = _request(job.url, range_header)
            with job.opener(request, timeout=job.timeout) as response:
                status = _status_of(response)
                if done and status == 200:
                    job.log("Server ignored Range. Restarting the single stream.", "WARN")
                    done = 0
                    mode = "wb"
                elif done and status not in (206, 200):
                    raise OSError(f"HTTP {status}")
                else:
                    mode = "ab" if done else "wb"
                if not total:
                    length = _headers_get(response.headers, "Content-Length")
                    if length.isdigit():
                        total = done + int(length) if status == 206 else int(length)
                        job.state["total"] = total
                last_ui = 0.0
                with job.dest.open(mode) as handle:
                    while True:
                        chunk = response.read(job.chunk_size)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        job.state["done"] = done
                        now = time.time()
                        if on_progress and now - last_ui >= 0.4:
                            on_progress(_snapshot(label, job.state, started, base))
                            last_ui = now
                _save_state(job.state_path, job.state, job.lock)
            if total and done + 1 < total:
                raise OSError(f"incomplete {done}/{total}")
            job.state["done"] = done
            return
        except Exception as exc:
            job.state["done"] = dest_size(job.dest) if not total or dest_size(job.dest) <= total else done
            _save_state(job.state_path, job.state, job.lock)
            if attempt >= job.retries:
                raise
            delay = min(8.0, 0.5 * (2 ** (attempt - 1)))
            job.log(f"single stream retry {attempt}/{job.retries}: {redact_text(str(exc))}", "WARN")
            job.sleep(delay)


def dest_size(dest: Path) -> int:
    try:
        return dest.stat().st_size
    except OSError:
        return 0


def _new_segment_state(probe: _Probe, url: str, workers: int, existing: int) -> dict:
    spans = plan_segments(probe.total, workers)
    segments = []
    for start, end in spans:
        length = end - start + 1
        done = min(length, max(0, existing - start)) if existing > start else 0
        segments.append({"start": start, "end": end, "done": done})
    return {
        "v": 1,
        "mode": "segments",
        "total": probe.total,
        "etag": probe.etag,
        "url_sha256": _url_id(url),
        "segments": segments,
    }


def _download_once(
    url: str,
    dest: Path,
    *,
    workers: int | None,
    opener,
    timeout: int,
    retries: int,
    chunk_size: int,
    sleep: Callable[[float], None],
    log: Callable[[str, str], None],
    on_progress: Callable[[DownloadProgress], None] | None,
    label: str,
    force_single: bool = False,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"Download source: {redact_url(url)}", "INFO")
    probe = _probe(opener, url, timeout)
    state_path = _state_path(dest)
    saved = _load_state(state_path)
    if saved and not _compatible(saved, probe, url):
        log("Saved download does not match this file. Starting over.", "WARN")
        _discard(dest)
        saved = None
    existing = dest_size(dest)
    if probe.total and existing > probe.total and not (saved and saved.get("mode") == "segments"):
        _discard(dest)
        existing = 0
        saved = None
    ranged = (not force_single) and probe.ranged and probe.total > 1
    count = resolve_workers(workers, probe.total, ranged)
    if not ranged:
        if saved and saved.get("mode") == "segments":
            log("Range is no longer available. Starting over in one stream.", "WARN")
            _discard(dest)
            existing = 0
            saved = None
        state = saved if saved and saved.get("mode") == "single" else {
            "v": 1,
            "mode": "single",
            "total": probe.total,
            "etag": probe.etag,
            "url_sha256": _url_id(url),
            "done": existing if not probe.total or existing <= probe.total else 0,
            "segments": [],
        }
        if state.get("mode") != "single":
            state = {
                "v": 1,
                "mode": "single",
                "total": probe.total,
                "etag": probe.etag,
                "url_sha256": _url_id(url),
                "done": existing if not probe.total or existing <= probe.total else 0,
                "segments": [],
            }
        _save_state(state_path, state, threading.Lock())
        job = _Job(
            url=url,
            dest=dest,
            state_path=state_path,
            state=state,
            opener=opener,
            timeout=timeout,
            retries=retries,
            chunk_size=chunk_size,
            sleep=sleep,
            log=log,
        )
        log("Range is not available. Using one stream.", "INFO")
        _single(job, label, on_progress)
        if on_progress:
            on_progress(_snapshot(label, state, time.time(), int(state.get("done") or 0)))
        return dest

    if saved and saved.get("mode") == "segments" and saved.get("segments"):
        state = saved
        log(f"Resuming {len(state['segments'])} segments for {dest.name}", "INFO")
    else:
        if probe.total and existing > probe.total:
            existing = 0
            _discard(dest)
        state = _new_segment_state(probe, url, count, existing)
        log(f"Downloading {dest.name} in {len(state['segments'])} segments", "INFO")
    if probe.total:
        _extend(dest, probe.total)
    _save_state(state_path, state, threading.Lock())
    job = _Job(
        url=url,
        dest=dest,
        state_path=state_path,
        state=state,
        opener=opener,
        timeout=timeout,
        retries=retries,
        chunk_size=chunk_size,
        sleep=sleep,
        log=log,
    )
    threads: list[threading.Thread] = []
    for segment in state["segments"]:
        length = int(segment["end"]) - int(segment["start"]) + 1
        if int(segment.get("done") or 0) >= length:
            continue
        thread = threading.Thread(target=_run_segment, args=(job, segment), daemon=True)
        threads.append(thread)
        thread.start()
    started = time.time()
    base = sum(int(item.get("done") or 0) for item in state["segments"])
    while any(thread.is_alive() for thread in threads):
        if on_progress:
            on_progress(_snapshot(label, state, started, base))
        if job.fallback.is_set():
            job.stop.set()
            break
        time.sleep(0.05)
    for thread in threads:
        thread.join()
    if job.fallback.is_set():
        log("A segment was served without Range. Falling back to one stream.", "WARN")
        _discard(dest)
        return _download_once(
            url,
            dest,
            workers=1,
            opener=opener,
            timeout=timeout,
            retries=retries,
            chunk_size=chunk_size,
            sleep=sleep,
            log=log,
            on_progress=on_progress,
            label=label,
            force_single=True,
        )
    if job.problems:
        raise job.problems[0]
    done = sum(int(item.get("done") or 0) for item in state["segments"])
    if probe.total and done != probe.total:
        raise OSError(f"incomplete {done}/{probe.total}")
    if on_progress:
        on_progress(_snapshot(label, state, started, base))
    return dest


def make_ui_callback(label: str) -> Callable[[DownloadProgress], None]:
    """Push live speed, ETA, bytes, and per-segment percents to the GUI and the log."""
    last = {"t": 0.0}

    def _callback(progress: DownloadProgress) -> None:
        from .progress import report_progress

        percent = (100.0 * progress.done / progress.total) if progress.total else None
        report_progress(
            phase=f"Download {label}",
            percent=percent,
            detail=progress.detail,
            bytes_done=progress.done,
            bytes_total=progress.total,
            speed_bps=progress.speed_bps,
            eta_seconds=progress.eta_seconds,
            indeterminate=not progress.total,
        )
        now = time.time()
        finished = bool(progress.total) and progress.done >= progress.total and not progress.detail.startswith("Verifying")
        if finished and not last.get("done"):
            _default_log(progress.detail, "INFO")
            last["done"] = True
            last["t"] = now
        elif not finished and now - last["t"] >= 2:
            _default_log(progress.detail, "INFO")
            last["t"] = now

    return _callback


def sha256_file(path: Path, on_bytes: Callable[[int, int], None] | None = None) -> str:
    digest = hashlib.sha256()
    total = dest_size(path)
    done = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            done += len(chunk)
            if on_bytes and (done == total or done % (8 * 1024 * 1024) < len(chunk)):
                on_bytes(done, total)
    return digest.hexdigest()


def download_image(
    url: str,
    dest: str | Path,
    *,
    expected_sha256: str | None = None,
    workers: int | None = None,
    opener=None,
    log: Callable[[str, str], None] | None = None,
    on_progress: Callable[[DownloadProgress], None] | None = None,
    retries: int = 5,
    hash_attempts: int = _HASH_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    chunk_size: int = _CHUNK,
    timeout: int = 120,
    label: str = "",
) -> Path:
    """Download ``url`` to ``dest``. Hash retries stay inside 1..3 attempts."""
    target = Path(dest)
    logger = log or _default_log
    fetch = opener or _default_opener()
    expected = (expected_sha256 or os.environ.get("MAGIC_ISO_SHA256") or "").strip().lower()
    if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
        expected = ""
    attempts = max(1, min(int(hash_attempts), _HASH_ATTEMPTS))
    name = label or target.name
    if target.is_file() and expected and not _state_path(target).exists():
        have = sha256_file(target)
        if have == expected:
            logger(f"SHA-256 matches, using {target.name}", "OK")
            return target
    last = ""
    for attempt in range(1, attempts + 1):
        _download_once(
            url,
            target,
            workers=workers,
            opener=fetch,
            timeout=timeout,
            retries=retries,
            chunk_size=chunk_size,
            sleep=sleep,
            log=logger,
            on_progress=on_progress,
            label=name,
        )
        logger(f"Verifying SHA-256 for {target.name}", "STEP")

        def _hash_progress(done: int, total: int) -> None:
            if on_progress is None:
                return
            pct = int(100 * done / total) if total else 0
            on_progress(
                DownloadProgress(
                    done=done,
                    total=total,
                    speed_bps=0.0,
                    eta_seconds=None,
                    segments=[],
                    detail=f"Verifying SHA-256 {pct}%",
                )
            )

        digest = sha256_file(target, on_bytes=_hash_progress)
        logger(f"SHA-256 {digest}", "INFO")
        if not expected or digest == expected:
            try:
                _state_path(target).unlink()
            except OSError:
                pass
            return target
        last = digest
        logger(
            f"SHA-256 mismatch ({attempt}/{attempts}). Deleting and downloading again.",
            "WARN",
        )
        _discard(target)
    raise RuntimeError(f"SHA-256 mismatch after {attempts} downloads (got {last})")
