"""Segmented image downloads with a fake HTTP server. No network."""
from __future__ import annotations

import hashlib
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
if "engine" not in sys.modules:
    pkg = types.ModuleType("engine")
    pkg.__path__ = [str(ROOT / "python" / "engine")]  # type: ignore[attr-defined]
    pkg.__package__ = "engine"
    sys.modules["engine"] = pkg

from engine.segment_download import (  # noqa: E402
    download_image,
    official_sha256,
    plan_segments,
    redact_url,
    resolve_workers,
)


class _Headers(dict):
    def get(self, key, default=None):
        for name, value in self.items():
            if name.lower() == str(key).lower():
                return value
        return default


class _Body:
    def __init__(self, status: int, headers: dict, body: bytes, cut: list[bool] | None):
        self.status = status
        self.headers = _Headers(headers)
        self._body = body
        self._off = 0
        self._cut = cut

    def getcode(self) -> int:
        return self.status

    def read(self, size: int = -1) -> bytes:
        if self._cut and self._cut[0] and self._off >= 20:
            self._cut[0] = False
            raise ConnectionError("cut")
        if size is None or size < 0:
            size = len(self._body) - self._off
        chunk = self._body[self._off : self._off + size]
        self._off += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


class _Opener:
    def __init__(self, payload: bytes, *, range_ok: bool = True, cut: list[bool] | None = None):
        self.payload = payload
        self.range_ok = range_ok
        self.cut = cut
        self.ranges: list[str | None] = []

    def __call__(self, request, timeout=None):
        header = request.headers.get("Range")
        self.ranges.append(header)
        if not self.range_ok or not header:
            return _Body(200, {"Content-Length": str(len(self.payload)), "ETag": "v1"}, self.payload, None)
        match = re.match(r"bytes=(\d+)-(\d*)$", header)
        start = int(match.group(1)) if match else 0
        end = int(match.group(2)) if match and match.group(2) else len(self.payload) - 1
        end = min(end, len(self.payload) - 1)
        body = self.payload[start : end + 1]
        return _Body(
            206,
            {
                "Content-Range": f"bytes {start}-{end}/{len(self.payload)}",
                "Content-Length": str(len(body)),
                "ETag": "v1",
            },
            body,
            self.cut,
        )


def test_plan_redact_and_worker_bounds() -> None:
    spans = plan_segments(1000, 4)
    assert spans[0] == (0, 249)
    assert spans[-1][1] == 999
    covered = []
    for start, end in spans:
        covered.extend(range(start, end + 1))
    assert covered == list(range(1000))
    clean = redact_url("https://user:secret@cdn.example/Win11_x64.iso?t=SECRETTOKEN")
    assert clean == "https://cdn.example/Win11_x64.iso?[redacted]"
    assert "SECRETTOKEN" not in clean
    assert "secret" not in clean
    assert official_sha256({"Sha256": "ab" * 32}) == "ab" * 32
    assert official_sha256({"Uri": "https://example"}) == ""
    assert resolve_workers(None, 100, True) == 1
    assert resolve_workers(None, 8 * 1024 * 1024, False) == 1
    assert resolve_workers(99, 8 * 1024 * 1024, True) == 8
    assert resolve_workers(None, 8 * 1024 * 1024, True) == 6


def test_segments_resume_fallback_and_hash() -> None:
    root = Path(__file__).resolve().parent / "_segment_dl"
    if root.exists():
        for item in sorted(root.rglob("*"), reverse=True):
            if item.is_file():
                item.unlink()
            else:
                item.rmdir()
    root.mkdir(exist_ok=True)
    payload = bytes((index * 17) % 256 for index in range(4000))
    logs: list[str] = []

    def _log(message: str, level: str = "INFO") -> None:
        logs.append(message)

    progress: list = []
    opener = _Opener(payload)
    dest = root / "win.iso"
    url = "https://cdn.example/Win11.iso?t=SECRETTOKEN"
    path = download_image(
        url,
        dest,
        workers=4,
        opener=opener,
        log=_log,
        on_progress=progress.append,
        sleep=lambda _seconds: None,
        chunk_size=64,
        retries=3,
        label="win.iso",
    )
    assert path.read_bytes() == payload
    assert any("[" in item.detail and "/" in item.detail for item in progress)
    assert any(len(item.segments) == 4 for item in progress)
    assert not (dest.name + ".parts.json" and (root / "win.iso.parts.json").exists())
    assert any("?[redacted]" in line for line in logs)
    assert "SECRETTOKEN" not in "\n".join(logs)

    cut = [True]
    cut_opener = _Opener(payload, cut=cut)
    partial = root / "partial.iso"
    try:
        download_image(
            url,
            partial,
            workers=4,
            opener=cut_opener,
            log=_log,
            sleep=lambda _seconds: None,
            chunk_size=8,
            retries=1,
        )
        raise AssertionError("cut download should fail")
    except ConnectionError:
        pass
    assert (root / "partial.iso.parts.json").is_file()
    before = len(cut_opener.ranges)
    download_image(
        url,
        partial,
        workers=4,
        opener=cut_opener,
        log=_log,
        sleep=lambda _seconds: None,
        chunk_size=8,
        retries=3,
    )
    assert partial.read_bytes() == payload
    resumed = [item for item in cut_opener.ranges[before:] if item and item != "bytes=0-0"]
    assert any(int(item.split("=", 1)[1].split("-", 1)[0]) > 0 for item in resumed)

    whole = _Opener(payload, range_ok=False)
    single = root / "single.iso"
    download_image(
        url,
        single,
        workers=4,
        opener=whole,
        log=_log,
        sleep=lambda _seconds: None,
        chunk_size=32,
        retries=2,
    )
    assert single.read_bytes() == payload
    assert any("one stream" in line.lower() for line in logs)

    good = b"good-image-bytes"
    bad = b"bad-image-bytes!"
    assert len(good) == len(bad)
    switched = _Opener(bad)

    def _flip(message: str, level: str = "INFO") -> None:
        logs.append(message)
        if "mismatch" in message.lower():
            switched.payload = good

    hashed = root / "hashed.iso"
    expected = hashlib.sha256(good).hexdigest()
    download_image(
        "https://cdn.example/disk.esd?sig=SECRETTOKEN",
        hashed,
        expected_sha256=expected,
        workers=2,
        opener=switched,
        log=_flip,
        sleep=lambda _seconds: None,
        chunk_size=4,
        retries=2,
        hash_attempts=3,
    )
    assert hashed.read_bytes() == good
    broken = _Opener(bad)
    refused = root / "refused.iso"
    try:
        download_image(
            "https://cdn.example/disk.esd?sig=SECRETTOKEN",
            refused,
            expected_sha256=expected,
            workers=2,
            opener=broken,
            log=_log,
            sleep=lambda _seconds: None,
            chunk_size=4,
            retries=1,
            hash_attempts=2,
        )
        raise AssertionError("mismatch should raise")
    except RuntimeError as exc:
        assert "mismatch" in str(exc).lower()
    assert not refused.exists()

    delays: list[float] = []
    flaky = _Opener(payload, cut=[True])
    retried = root / "retry.iso"
    download_image(
        url,
        retried,
        workers=4,
        opener=flaky,
        log=_log,
        sleep=delays.append,
        chunk_size=8,
        retries=3,
    )
    assert retried.read_bytes() == payload
    assert delays and delays[0] == 0.5

    for item in sorted(root.rglob("*"), reverse=True):
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            item.rmdir()
    root.rmdir()


if __name__ == "__main__":
    test_plan_redact_and_worker_bounds()
    test_segments_resume_fallback_and_hash()
    print("segment download tests OK")
