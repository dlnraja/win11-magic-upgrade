"""Portable .cmd/.bat launchers must be ASCII CRLF, and elevation must use -FilePath."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

from launcher_cmd import start_process_command  # noqa: E402


def _cmd_files() -> list[Path]:
    found = []
    for path in ROOT.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.suffix.lower() in {".cmd", ".bat"} and path.is_file():
            found.append(path)
    return found


def test_no_bom_and_crlf_only() -> None:
    files = _cmd_files()
    assert files, "expected at least one .cmd or .bat"
    for path in files:
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in {path}"
        assert b"\n" not in data.replace(b"\r\n", b""), f"LF without CR in {path}"
        assert all(byte < 128 for byte in data), f"non-ASCII byte in {path}"


def test_elevation_command_quotes_spaces_and_forwards_args() -> None:
    command = start_process_command(
        r"C:\Program Files\Win11 Magic\Win11MagicUpgrade.exe",
        r"C:\Program Files\Win11 Magic",
        ["--cli", "--repair-before-reboot"],
    )
    assert command == (
        "Start-Process -FilePath 'C:\\Program Files\\Win11 Magic\\Win11MagicUpgrade.exe' "
        "-Verb RunAs -WorkingDirectory 'C:\\Program Files\\Win11 Magic' "
        "-ArgumentList '--cli','--repair-before-reboot'"
    )
    assert "-LiteralPath" not in command
    launcher = (ROOT / "Win11MagicUpgrade.cmd").read_bytes().decode("ascii")
    assert "-LiteralPath" not in launcher
    assert "mshta" not in launcher.lower()
    assert ".vbs" not in launcher.lower()
    for token in ("-FilePath", "-Verb RunAs", "-WorkingDirectory", "-ArgumentList"):
        assert token in launcher
    assert "Win11MagicUpgrade.exe" in launcher


def main() -> None:
    test_no_bom_and_crlf_only()
    test_elevation_command_quotes_spaces_and_forwards_args()
    print("test_cmd_launcher ok")


if __name__ == "__main__":
    main()
