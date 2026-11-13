"""Bounded, non-interactive external commands.

diskpart, mountvol, PowerShell storage cmdlets, bcdboot, bcdedit, mbr2gpt,
manage-bde, DISM, downloads, and file copies go through here. A timeout kills
the child. A failed pre-check or post-check rolls back via the caller's
cleanup callback. Nothing in this module opens a modal dialog.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

# Tool name (lower) -> seconds. Unknown tools still get a finite default.
TOOL_TIMEOUTS = {
    "diskpart": 180,
    "diskpart.exe": 180,
    "mountvol": 60,
    "mountvol.exe": 60,
    "powershell": 120,
    "powershell.exe": 120,
    "pwsh": 120,
    "pwsh.exe": 120,
    "bcdboot": 180,
    "bcdboot.exe": 180,
    "bcdedit": 90,
    "bcdedit.exe": 90,
    "mbr2gpt": 900,
    "mbr2gpt.exe": 900,
    "manage-bde": 180,
    "manage-bde.exe": 180,
    "dism": 900,
    "dism.exe": 900,
    "reagentc": 180,
    "reagentc.exe": 180,
    "setup.exe": 30,
    "setupprep.exe": 30,
}

DEFAULT_TIMEOUT = 180

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


@dataclass(frozen=True)
class ExtResult:
    code: int
    output: str
    ok: bool
    timed_out: bool = False
    killed: bool = False
    reason: str = ""


@dataclass(frozen=True)
class GateState:
    """Facts gathered before a boot-critical write. None means not known yet."""

    is_admin: bool | None = None
    free_bytes: int | None = None
    need_bytes: int = 0
    target_exists: bool | None = None
    bitlocker: str = "unknown"
    secure_boot: str = "unknown"
    secure_boot_block: bool = False
    secure_boot_reason: str = ""


@dataclass(frozen=True)
class PostState:
    files_written: bool | None = None
    bcd_present: bool | None = None
    esp_ok: bool | None = None


def timeout_for(cmd: list[str], explicit: int | None = None) -> int:
    if explicit is not None and explicit > 0:
        return int(explicit)
    if not cmd:
        return DEFAULT_TIMEOUT
    name = Path(str(cmd[0])).name.lower()
    return int(TOOL_TIMEOUTS.get(name, DEFAULT_TIMEOUT))


def precheck(state: GateState) -> tuple[bool, str]:
    """Admin, space, target, BitLocker, and Secure Boot. Unknown admin is allowed."""
    if state.is_admin is False:
        return False, "pre-check failed: not running as administrator"
    if state.need_bytes > 0 and state.free_bytes is not None and state.free_bytes < state.need_bytes:
        return False, (
            f"pre-check failed: free space {state.free_bytes} bytes is below {state.need_bytes}"
        )
    if state.target_exists is False:
        return False, "pre-check failed: target does not exist"
    if (state.bitlocker or "").lower() in ("locked", "on_locked"):
        return False, "pre-check failed: BitLocker volume is locked"
    if state.secure_boot_block:
        return False, state.secure_boot_reason or "pre-check failed: Secure Boot would reject this write"
    return True, ""


def postcheck(state: PostState) -> tuple[bool, str]:
    problems: list[str] = []
    if state.files_written is False:
        problems.append("expected files were not written")
    if state.bcd_present is False:
        problems.append("BCD entry is missing")
    if state.esp_ok is False:
        problems.append("ESP no longer matches the drive map")
    if problems:
        return False, "post-check failed: " + "; ".join(problems)
    return True, ""


def probe_admin() -> bool | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def _kill_tree(proc: subprocess.Popen) -> bool:
    """Stop the process and anything it spawned. Returns True if a kill was issued."""
    killed = False
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=_NO_WINDOW,
                stdin=subprocess.DEVNULL,
            )
            killed = True
        except Exception:
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            killed = True
        except Exception:
            pass
    try:
        proc.kill()
        killed = True
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    return killed


def oem_code_page() -> str:
    """Console OEM code page. French Windows mountvol writes cp850, not UTF-8."""
    if os.name != "nt":
        return "cp850"
    try:
        import ctypes

        return f"cp{int(ctypes.windll.kernel32.GetOEMCP())}"
    except Exception:
        return "cp850"


def decode_tool_output(data: str | bytes | None, *, oem: str | None = None) -> str:
    """Decode a native tool's pipe. Valid UTF-8 is kept; otherwise use the OEM page.

    ``Paramètre`` encoded as cp850 must not become ``Param�tre``.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(oem or oem_code_page(), errors="replace")


def _join_output(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    return (decode_tool_output(stdout) + "\n" + decode_tool_output(stderr)).strip()


PS_UTF8_PREFIX = (
    "$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
    "try { chcp 65001 > $null } catch {}; "
)


def powershell_command(script: str) -> list[str]:
    """Run PowerShell with UTF-8 pipes so French messages are not mojibake."""
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        PS_UTF8_PREFIX + script,
    ]


def _popen_kwargs(input_text: str | None) -> dict:
    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        "shell": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _NO_WINDOW | _NEW_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def validate_output(
    output: str,
    *,
    require_patterns: tuple[str, ...] = (),
    reject_patterns: tuple[str, ...] = (),
) -> tuple[bool, str]:
    text = output or ""
    for pattern in require_patterns:
        if not re.search(pattern, text, re.I):
            return False, f"output missing {pattern}"
    for pattern in reject_patterns:
        if re.search(pattern, text, re.I):
            # A success line wins over a generic error word in a path.
            if re.search(r"successfully|r[eé]ussi|termin[eé]", text, re.I):
                continue
            return False, f"output matched {pattern}"
    return True, ""


def run_bounded(
    cmd: list[str],
    *,
    timeout: int | None = None,
    input_text: str | None = None,
    accept_codes: tuple[int, ...] = (0,),
    require_patterns: tuple[str, ...] = (),
    reject_patterns: tuple[str, ...] = (),
    popen: Callable[..., subprocess.Popen] | None = None,
    killer: Callable[[subprocess.Popen], bool] | None = None,
) -> ExtResult:
    """Run one command. On timeout the child is killed and the result is not ok."""
    if not cmd or not str(cmd[0]).strip():
        return ExtResult(code=1, output="", ok=False, reason="empty command")
    limit = timeout_for(cmd, timeout)
    launcher = popen or subprocess.Popen
    try:
        proc = launcher(list(cmd), **_popen_kwargs(input_text))
    except OSError as exc:
        return ExtResult(code=1, output=str(exc), ok=False, reason=f"failed to start {cmd[0]}: {exc}")
    payload = input_text.encode("utf-8") if isinstance(input_text, str) else input_text
    try:
        stdout, stderr = proc.communicate(input=payload, timeout=limit)
    except subprocess.TimeoutExpired as exc:
        chunks_out = [getattr(exc, "stdout", None)]
        chunks_err = [getattr(exc, "stderr", None)]
        killed = killer(proc) if killer is not None else _kill_tree(proc)
        try:
            extra_out, extra_err = proc.communicate(timeout=5)
            chunks_out.append(extra_out)
            chunks_err.append(extra_err)
        except Exception:
            pass
        output = (
            "".join(decode_tool_output(chunk) for chunk in chunks_out)
            + "\n"
            + "".join(decode_tool_output(chunk) for chunk in chunks_err)
        ).strip()
        reason = f"timeout after {limit}s: {Path(str(cmd[0])).name} killed"
        return ExtResult(code=124, output=output, ok=False, timed_out=True, killed=bool(killed), reason=reason)
    output = _join_output(stdout, stderr)
    code = proc.returncode if proc.returncode is not None else 1
    if code not in accept_codes:
        return ExtResult(
            code=int(code),
            output=output,
            ok=False,
            reason=f"exit {code}: {Path(str(cmd[0])).name}",
        )
    valid, why = validate_output(output, require_patterns=require_patterns, reject_patterns=reject_patterns)
    if not valid:
        return ExtResult(code=int(code), output=output, ok=False, reason=why)
    return ExtResult(code=int(code), output=output, ok=True, reason="")


def run_checked(
    cmd: list[str],
    *,
    timeout: int | None = None,
    input_text: str | None = None,
    pre: GateState | None = None,
    post: PostState | None = None,
    cleanup: Callable[[], None] | None = None,
    accept_codes: tuple[int, ...] = (0,),
    require_patterns: tuple[str, ...] = (),
    reject_patterns: tuple[str, ...] = (),
    popen: Callable[..., subprocess.Popen] | None = None,
    killer: Callable[[subprocess.Popen], bool] | None = None,
) -> ExtResult:
    """Pre-check, run, post-check. Any failure runs cleanup once."""
    if pre is not None:
        ok, reason = precheck(pre)
        if not ok:
            if cleanup is not None:
                cleanup()
            return ExtResult(code=1, output="", ok=False, reason=reason)
    result = run_bounded(
        cmd,
        timeout=timeout,
        input_text=input_text,
        accept_codes=accept_codes,
        require_patterns=require_patterns,
        reject_patterns=reject_patterns,
        popen=popen,
        killer=killer,
    )
    if not result.ok:
        if cleanup is not None:
            cleanup()
        return result
    if post is not None:
        ok, reason = postcheck(post)
        if not ok:
            if cleanup is not None:
                cleanup()
            return replace(result, ok=False, reason=reason)
    return result


def copy_bounded(
    src: str | Path,
    dst: str | Path,
    *,
    timeout: int = 300,
    copier: Callable[[str, str], object] | None = None,
) -> ExtResult:
    """Copy one file. A hung copy is reported as a timeout and not as success."""
    source = str(src)
    dest = str(dst)
    if not Path(source).is_file():
        return ExtResult(code=1, output="", ok=False, reason=f"pre-check failed: source missing {source}")
    worker = copier or shutil.copy2
    box: dict[str, object] = {}

    def _work() -> None:
        try:
            box["value"] = worker(source, dest)
            box["ok"] = True
        except Exception as exc:
            box["ok"] = False
            box["err"] = str(exc)

    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return ExtResult(
            code=124,
            output="",
            ok=False,
            timed_out=True,
            reason=f"timeout after {timeout}s: copy {Path(source).name}",
        )
    if not box.get("ok"):
        return ExtResult(code=1, output=str(box.get("err") or ""), ok=False, reason="copy failed")
    if not Path(dest).is_file():
        return ExtResult(code=1, output="", ok=False, reason="post-check failed: copy destination missing")
    return ExtResult(code=0, output=str(dest), ok=True)


def download_bounded(url: str, dest: str | Path, *, timeout: int = 180, opener=urlopen) -> ExtResult:
    """Download to a file. The socket timeout is bounded and the body is size-capped by the caller."""
    target = Path(dest)
    if not url or not url.startswith(("https://", "http://")):
        return ExtResult(code=1, output="", ok=False, reason="pre-check failed: download URL is not http(s)")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        request = Request(url, headers={"User-Agent": "Win11MagicUpgrade"})
        with opener(request, timeout=timeout) as response, target.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except Exception as exc:
        try:
            if target.exists() and target.stat().st_size == 0:
                target.unlink()
        except OSError:
            pass
        timed_out = "timed out" in str(exc).lower() or "timeout" in str(exc).lower()
        return ExtResult(
            code=124 if timed_out else 1,
            output=str(exc),
            ok=False,
            timed_out=timed_out,
            reason=f"download failed: {exc}",
        )
    if not target.is_file() or target.stat().st_size <= 0:
        return ExtResult(code=1, output="", ok=False, reason="post-check failed: download is empty")
    return ExtResult(code=0, output=str(target), ok=True)
