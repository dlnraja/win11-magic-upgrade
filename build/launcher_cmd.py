"""PowerShell 5.1 elevation command for the portable launcher.

Windows PowerShell 5.1 has no Start-Process -LiteralPath. -FilePath is the
parameter that exists there. Paths are single-quoted so spaces stay intact.
"""
from __future__ import annotations


def ps_quote(value: str) -> str:
    return "'" + (value or "").replace("'", "''") + "'"


def start_process_command(target: str, workdir: str, args: list[str] | None = None) -> str:
    """Command string passed to powershell.exe -Command."""
    parts = [
        "Start-Process",
        "-FilePath",
        ps_quote(target),
        "-Verb",
        "RunAs",
        "-WorkingDirectory",
        ps_quote(workdir),
    ]
    if args:
        parts.append("-ArgumentList")
        parts.append(",".join(ps_quote(arg) for arg in args))
    command = " ".join(parts)
    if "-LiteralPath" in command:
        raise RuntimeError("Start-Process -LiteralPath is not valid on Windows PowerShell 5.1")
    return command
