"""Redaction and end-of-run auto-diag. No network."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.run_diag import (  # noqa: E402
    IssueClient,
    catalog_match,
    command_template,
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
    test_opt_out()
    print("run diag tests OK")
