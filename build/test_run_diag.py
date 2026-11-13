"""Redaction and end-of-run auto-diag. No network."""
from __future__ import annotations

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

from engine.run_diag import (  # noqa: E402
    IssueClient,
    catalog_match,
    collect_history,
    command_template,
    compact_summary,
    finish_run,
    redact_diag,
    scan_log_text,
    signature_for,
)


def test_redaction_uses_stable_placeholders() -> None:
    guid_a = "b95f9bc1-965f-43ae-81ff-28b0e4909488"
    guid_b = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    vol = lambda guid: "\\\\?\\Volume{" + guid + "}\\"
    raw = (
        r"C:\Users\alice\AppData\bcdboot "
        + vol(guid_a)
        + " /s Z: serial=DEADBEEF "
        + "alice@example.com 192.168.1.20 AA:BB:CC:DD:EE:FF "
        + "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX "
        + vol(guid_a)
        + " again "
        + vol(guid_b)
    )
    clean = redact_diag(raw)
    assert r"C:\Users\alice" not in clean
    assert "%USERPROFILE%" in clean
    assert guid_a not in clean.lower()
    assert clean.count("[VOL-1]") == 2
    assert "[VOL-2]" in clean
    assert "[EMAIL]" in clean
    assert "[IP]" in clean
    assert "[MAC]" in clean
    assert "[PRODUCTKEY]" in clean
    assert "[SERIAL]" in clean
    assert "DEADBEEF" not in clean
    assert signature_for(raw) == signature_for(raw.replace("alice", "bob").replace(guid_a, guid_a))


def test_scan_and_finish_without_credentials() -> None:
    text = r"[ERROR] bcdboot C:\Users\alice\Windows /s \\?\Volume{b95f9bc1-965f-43ae-81ff-28b0e4909488}\ -> exit 123"
    findings = scan_log_text(text)
    assert findings
    assert findings[0].catalog_id == "bcdboot-123"
    assert "alice" not in findings[0].redacted
    assert "[LETTER]" in findings[0].command or "%USERPROFILE%" in findings[0].command
    assert catalog_match("Les arguments spécifiés pour cette commande ne sont pas valides") == "diskpart-args"
    state = Path(__file__).resolve().parent / "_run_diag_tmp"
    result = finish_run(
        mode="test",
        step="diagnose",
        text=text + "\n[WARN] arguments non valides\n",
        client=IssueClient(),
        now=1_000_000.0,
        state_dir=state,
        open_browser=False,
    )
    assert result["findings"] >= 1
    assert result["filed"][0]["action"] == "paste"
    assert "auto-diag" in result["filed"][0]["url"]
    paste = Path(result["filed"][0]["file"])
    assert paste.is_file()
    body = paste.read_text(encoding="utf-8")
    assert "alice" not in body.lower()
    assert "App version:" in body
    for item in state.rglob("*"):
        if item.is_file():
            item.unlink()
    for item in sorted(state.rglob("*"), reverse=True):
        if item.is_dir():
            item.rmdir()
    state.rmdir()


class _Recording(IssueClient):
    def __init__(self) -> None:
        self.comments: list[str] = []
        self.created: list[str] = []

    def has_credentials(self) -> bool:
        return True

    def find(self, signature: str) -> str | None:
        return "https://github.com/dlnraja/win11-magic-upgrade/issues/9" if signature else None

    def comment(self, url: str, body: str) -> bool:
        self.comments.append(url)
        return True

    def create(self, title: str, body: str) -> str | None:
        self.created.append(title)
        return "https://github.com/dlnraja/win11-magic-upgrade/issues/10"


def test_same_signature_comments_instead_of_creating() -> None:
    client = _Recording()
    text = "[ERROR] WinError 3 path introuvable"
    result = finish_run(mode="gui", step="phase-1", text=text, client=client, now=1.0, state_dir=Path("."))
    assert result["filed"][0]["action"] == "comment"
    assert client.created == []
    assert client.comments


def test_history_is_redacted_capped_and_sent_once() -> None:
    import zipfile

    root = Path(__file__).resolve().parent / "_run_diag_history"
    if root.exists():
        for item in sorted(root.rglob("*"), reverse=True):
            if item.is_file():
                item.unlink()
            else:
                item.rmdir()
    logs = root / "logs"
    magic = root / "MagicUpgrade" / "logs"
    state = root / "state"
    bcd = state / "bcd-backups"
    part = state / "partition-backups" / "gen1"
    for folder in (logs, magic, bcd, part):
        folder.mkdir(parents=True)
    (logs / "upgrade-old.log").write_text(
        "[ERROR] WinError 32 C:\\Users\\alice\\AppData\\bcd\n",
        encoding="utf-8",
    )
    (magic / "magic.log").write_text("[WARN] diskpart arguments non valides\n", encoding="utf-8")
    (state / "postflight-loop.json").write_text(
        '{"attempts": 2, "path": "C:\\\\Users\\\\alice\\\\count"}',
        encoding="utf-8",
    )
    (bcd / "bcd-export-1").write_bytes(b"SECRET-HIVE-BYTES")
    (part / "bcd-export").write_bytes(b"SECRET-HIVE-BYTES-2")
    panther = root / "setuperr.log"
    panther.write_text(("HEADMARKER\n" + ("x" * 12000) + "\n[ERROR] setup introuvable TAILMARKER\n"), encoding="utf-8")
    (root / "SrtTrail.txt").write_text("[ERROR] Startup Repair failed SrtTrail\n", encoding="utf-8")
    items = collect_history(
        log_dirs=[logs, magic],
        state_dir=state,
        excerpts=[("panther", panther), ("srt", root / "SrtTrail.txt")],
        bcd_dirs=[bcd, part.parent],
    )
    joined = "\n".join(item.text for item in items)
    assert "alice" not in joined.lower()
    assert "SECRET-HIVE" not in joined
    assert any(item.kind == "bcd-meta" and "bytes=" in item.text for item in items)
    assert any(item.kind == "magic-log" for item in items)
    assert any(item.kind == "session-log" for item in items)
    panther_item = next(item for item in items if item.kind == "panther")
    assert "TAILMARKER" in panther_item.text
    assert "HEADMARKER" not in panther_item.text
    summary = compact_summary(items + items, cap=500)
    assert len(summary) <= 500
    assert summary.count("sig ") == summary.lower().count("sig ")
    signatures = [line for line in summary.splitlines() if line.strip().startswith("sig ")]
    assert len(signatures) == len(set(signatures))

    uploaded: list[str] = []

    class _Upload(IssueClient):
        def upload_bundle(self, zip_path: Path) -> str | None:
            uploaded.append(str(zip_path))
            return "https://gist.github.com/example/abc"

    state_out = root / "out"
    result = finish_run(
        mode="test",
        step="end",
        text="[ERROR] WinError 5 current failure",
        client=_Upload(),
        now=2_000_000.0,
        state_dir=state_out,
        sources=items,
    )
    assert result["history"]["attached"] is True
    assert result["history"]["upload"] == "https://gist.github.com/example/abc"
    assert result["history"]["new"] >= 1
    bundle = zipfile.ZipFile(result["history"]["bundle"])
    blob = " ".join(bundle.read(name).decode("utf-8", "replace") for name in bundle.namelist())
    assert "SECRET-HIVE" not in blob
    assert "alice" not in blob.lower()
    assert "gist.github.com/example/abc" in Path(result["filed"][0]["file"]).read_text(encoding="utf-8")
    again = finish_run(
        mode="test",
        step="end",
        text="[ERROR] WinError 5 current failure",
        client=_Upload(),
        now=2_000_100.0,
        state_dir=state_out,
        sources=items,
    )
    assert again["history"]["new"] == 0
    assert again["history"]["skipped"] >= 1
    assert len(uploaded) == 1
    for item in sorted(root.rglob("*"), reverse=True):
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            item.rmdir()


def test_opt_out() -> None:
    import os

    os.environ["MAGIC_SKIP_AUTO_DIAG"] = "1"
    try:
        assert finish_run(mode="cli", text="[ERROR] boom")["skipped"] == "opt-out"
    finally:
        os.environ.pop("MAGIC_SKIP_AUTO_DIAG", None)


if __name__ == "__main__":
    test_redaction_uses_stable_placeholders()
    test_scan_and_finish_without_credentials()
    test_same_signature_comments_instead_of_creating()
    test_history_is_redacted_capped_and_sent_once()
    test_opt_out()
    print("run diag tests OK")
