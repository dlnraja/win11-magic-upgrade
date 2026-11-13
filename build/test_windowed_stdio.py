"""Windowed PyInstaller has no console streams. Issue links stay short."""
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

import stdio_safe  # noqa: E402
from engine.issue_url import (  # noqa: E402
    file_report,
    prefilled_issue_url,
    run_in_background,
    short_issue_body,
)
from engine.run_diag import _prefilled_url  # noqa: E402


def test_none_streams_do_not_crash_help() -> None:
    saved = (sys.stdin, sys.stdout, sys.stderr)
    sys.stdin = None
    sys.stdout = None
    sys.stderr = None
    try:
        assert stdio_safe.stream_isatty(None) is False
        assert stdio_safe.stream_isatty(object()) is False
        stdio_safe.ensure_stdio()
        assert sys.stdout is not None and sys.stderr is not None
        assert stdio_safe.stream_isatty(sys.stdout) is False
        assert stdio_safe.stream_isatty(sys.stdin) is False
        sys.stdin = None
        sys.stdout = None
        sys.stderr = None
        if "tkinter" not in sys.modules:
            tk = types.ModuleType("tkinter")
            tk.Tk = type("Tk", (), {})
            tk.Toplevel = type("Toplevel", (), {})
            tk.StringVar = type("StringVar", (), {})
            tk.Frame = type("Frame", (), {})
            tk.Label = type("Label", (), {})
            tk.Button = type("Button", (), {})
            tk.Entry = type("Entry", (), {})
            for sub_name in ("messagebox", "scrolledtext", "ttk"):
                sub = types.ModuleType("tkinter." + sub_name)
                sys.modules["tkinter." + sub_name] = sub
                setattr(tk, sub_name, sub)
            sys.modules["tkinter"] = tk
        import magic_upgrade

        old_argv = sys.argv
        sys.argv = ["magic_upgrade.py", "--help"]
        try:
            magic_upgrade.main()
        except SystemExit as exc:
            assert exc.code in (0, None)
        finally:
            sys.argv = old_argv
        assert sys.stdout is not None
        assert sys.stderr is not None
        assert "sys.stdin.isatty" not in Path(magic_upgrade.__file__).read_text(encoding="utf-8")
    finally:
        sys.stdin, sys.stdout, sys.stderr = saved


def test_issue_url_stays_under_6000() -> None:
    body = short_issue_body(
        fingerprint="9b0ecb43f82e7cd2",
        version="1.56.11",
        error="AttributeError: 'NoneType' object has no attribute 'isatty'",
        context="windowed startup",
        report_path=r"C:\Users\alice\AppData\Local\Win11MagicUpgrade\autodiag\paste-9b0ecb43f82e7cd2.md",
    )
    huge = body + ("\n" + ("x" * 20000))
    url = prefilled_issue_url("dlnraja/win11-magic-upgrade", "[auto-diag] level 9b0ecb43f82e7cd2", huge, labels="auto-diag")
    assert len(url) <= 6000
    assert "9b0ecb43f82e7cd2" in url
    assert "Attach+the+saved+redacted+report" in url or "Attach the saved redacted report" in url
    short = _prefilled_url("title", "y" * 50000)
    assert len(short) <= 6000


def test_token_posts_secret_gist_and_capped_issue() -> None:
    calls: list[tuple[str, dict]] = []

    def post(url: str, payload: dict, token: str, timeout: int = 20) -> tuple[int, str]:
        calls.append((url, payload))
        assert token == "sek"
        assert timeout == 20
        if url.endswith("/gists"):
            assert payload["public"] is False
            return 201, '{"html_url":"https://gist.github.com/example/abc"}'
        assert len(payload["body"]) <= 60000
        assert "https://gist.github.com/example/abc" in payload["body"]
        return 201, '{"html_url":"https://github.com/dlnraja/win11-magic-upgrade/issues/9"}'

    def gh_run(args: list[str], timeout: int = 20) -> tuple[int, str]:
        raise AssertionError(args)

    filed = file_report(title="diag", body="e" * 80000, token="sek", http_post=post, gh_run=gh_run)
    assert filed is not None
    assert filed["url"].endswith("/issues/9")
    assert filed["gist"].endswith("/abc")
    assert len(calls) == 2


def test_without_auth_does_not_file() -> None:
    assert file_report(title="diag", body="e", token="", gh_run=lambda args, timeout=20: (1, "")) is None


def test_background_report_does_not_raise() -> None:
    seen: list[int] = []

    def boom() -> None:
        seen.append(1)
        raise RuntimeError("diag failed")

    run_in_background(boom, name="test-diag")
    for _ in range(20):
        if seen:
            break
        import time

        time.sleep(0.05)
    assert seen == [1]


if __name__ == "__main__":
    test_none_streams_do_not_crash_help()
    test_issue_url_stays_under_6000()
    test_token_posts_secret_gist_and_capped_issue()
    test_without_auth_does_not_file()
    test_background_report_does_not_raise()
    print("windowed stdio tests OK")
