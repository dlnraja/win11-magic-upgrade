"""Timeouts, killed children, and failed pre/post checks."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from engine.proc_guard import (  # noqa: E402
    GateState,
    PostState,
    copy_bounded,
    decode_tool_output,
    download_bounded,
    postcheck,
    precheck,
    run_bounded,
    run_checked,
    timeout_for,
)


class _HangProc:
    def __init__(self) -> None:
        self.pid = 4242
        self.returncode = None
        self.killed = False

    def communicate(self, input=None, timeout=None):  # noqa: A002
        if self.killed:
            return "", ""
        raise subprocess.TimeoutExpired(cmd=["hang"], timeout=timeout or 1)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None) -> int:
        return -9


def test_french_oem_output_is_not_mojibake() -> None:
    french = "Paramètre incorrect."
    oem = french.encode("cp850")
    decoded = decode_tool_output(oem, oem="cp850")
    assert decoded == french
    assert "\ufffd" not in decoded
    assert decode_tool_output(french.encode("utf-8")) == french
    assert decode_tool_output(french) == french


def test_tool_timeouts_are_bounded() -> None:
    assert timeout_for(["bcdboot.exe"]) == 180
    assert timeout_for(["mbr2gpt.exe"]) == 900
    assert timeout_for(["diskpart"]) == 180
    assert timeout_for(["something-else.exe"], 12) == 12
    assert timeout_for(["unknown.exe"]) == 180


def test_precheck_blocks_admin_space_target_bitlocker_secureboot() -> None:
    ok, _ = precheck(GateState(is_admin=None, target_exists=True, bitlocker="off"))
    assert ok
    assert precheck(GateState(is_admin=False))[0] is False
    assert "administrator" in precheck(GateState(is_admin=False))[1]
    assert precheck(GateState(free_bytes=10, need_bytes=100))[0] is False
    assert precheck(GateState(target_exists=False))[0] is False
    assert precheck(GateState(bitlocker="locked"))[0] is False
    blocked = GateState(secure_boot_block=True, secure_boot_reason="dbx rejects the loader")
    assert precheck(blocked)[0] is False
    assert "dbx" in precheck(blocked)[1]


def test_postcheck_requires_files_bcd_and_esp() -> None:
    assert postcheck(PostState(files_written=True, bcd_present=True, esp_ok=True))[0] is True
    assert postcheck(PostState())[0] is True
    ok, reason = postcheck(PostState(files_written=False, bcd_present=False, esp_ok=False))
    assert ok is False
    assert "files" in reason and "BCD" in reason and "ESP" in reason


def test_simulated_hang_kills_child_and_cleans_up() -> None:
    proc = _HangProc()

    def popen(cmd, **kwargs):
        assert kwargs.get("shell") is False
        assert kwargs.get("stdin") is subprocess.DEVNULL
        return proc

    cleaned = {"n": 0}

    def killer(child):
        child.kill()
        return True

    result = run_checked(
        ["bcdboot.exe", r"C:\Windows", "/s", "E:", "/f", "UEFI"],
        timeout=2,
        pre=GateState(is_admin=True, target_exists=True, bitlocker="off"),
        popen=popen,
        killer=killer,
        cleanup=lambda: cleaned.__setitem__("n", cleaned["n"] + 1),
    )
    assert result.timed_out is True
    assert result.killed is True
    assert result.ok is False
    assert result.code == 124
    assert proc.killed is True
    assert cleaned["n"] == 1
    assert "timeout" in result.reason


def test_failed_exit_and_output_do_not_count_as_success() -> None:
    class Done:
        pid = 7
        returncode = 5

        def communicate(self, input=None, timeout=None):  # noqa: A002
            return "DiskPart error: no volume selected", ""

    result = run_bounded(["diskpart"], timeout=5, popen=lambda *a, **k: Done(), reject_patterns=(r"no volume selected",))
    assert result.ok is False
    assert result.code == 5

    class Zero:
        pid = 8
        returncode = 0

        def communicate(self, input=None, timeout=None):  # noqa: A002
            return "ERROR: write failed", ""

    bad = run_bounded(
        ["bcdboot.exe"],
        timeout=5,
        popen=lambda *a, **k: Zero(),
        reject_patterns=(r"ERROR",),
    )
    assert bad.ok is False
    assert "ERROR" in bad.reason


def test_failed_precheck_skips_the_process_and_cleans_up() -> None:
    started = {"n": 0}
    cleaned = {"n": 0}

    def popen(*_a, **_k):
        started["n"] += 1
        raise AssertionError("process should not start")

    result = run_checked(
        ["mbr2gpt.exe", "/convert"],
        pre=GateState(bitlocker="locked", target_exists=True),
        popen=popen,
        cleanup=lambda: cleaned.__setitem__("n", cleaned["n"] + 1),
    )
    assert result.ok is False
    assert "BitLocker" in result.reason
    assert started["n"] == 0
    assert cleaned["n"] == 1


def test_failed_postcheck_cleans_up() -> None:
    class Done:
        pid = 3
        returncode = 0

        def communicate(self, input=None, timeout=None):  # noqa: A002
            return "successfully created", ""

    cleaned = {"n": 0}
    result = run_checked(
        ["bcdboot.exe"],
        timeout=5,
        popen=lambda *a, **k: Done(),
        post=PostState(files_written=True, bcd_present=False, esp_ok=False),
        cleanup=lambda: cleaned.__setitem__("n", cleaned["n"] + 1),
    )
    assert result.ok is False
    assert "BCD" in result.reason
    assert cleaned["n"] == 1


def test_real_sleep_is_killed() -> None:
    started = time.monotonic()
    result = run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    elapsed = time.monotonic() - started
    assert result.timed_out is True
    assert result.killed is True
    assert result.ok is False
    assert result.code == 124
    assert elapsed < 15


def test_real_exit_code_is_validated() -> None:
    result = run_bounded([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=15)
    assert result.code == 3
    assert result.ok is False
    good = run_bounded([sys.executable, "-c", "print('successfully ok')"], timeout=15, require_patterns=(r"successfully",))
    assert good.ok is True
    assert good.code == 0


def test_copy_timeout_and_download_checks() -> None:
    def slow_copy(src, dst):
        time.sleep(5)
        return dst

    hung = copy_bounded(__file__, ROOT / "build" / "_no_such_copy.bin", timeout=1, copier=slow_copy)
    assert hung.timed_out is True
    assert hung.ok is False

    missing = copy_bounded(ROOT / "build" / "missing-file.bin", ROOT / "build" / "out.bin", timeout=5)
    assert missing.ok is False
    assert "source missing" in missing.reason

    class _Resp:
        def read(self, n=-1):
            return b""

        def __iter__(self):
            return iter(())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    # urlopen is used as a context manager that yields a readable body.
    class _Body:
        def __init__(self) -> None:
            self._done = False

        def read(self, n=-1):
            if self._done:
                return b""
            self._done = True
            return b"payload"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    dest = ROOT / "build" / "_dl_test.bin"
    try:
        ok = download_bounded("https://example.invalid/file", dest, timeout=5, opener=lambda *a, **k: _Body())
        assert ok.ok is True
        assert dest.read_bytes() == b"payload"
    finally:
        if dest.exists():
            dest.unlink()
    refused = download_bounded("ftp://example.invalid/x", dest, timeout=5)
    assert refused.ok is False


def test_workflow_jobs_have_timeouts() -> None:
    workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for path in workflows:
        lines = path.read_text(encoding="utf-8").splitlines()
        in_jobs = False
        job = None
        has_timeout = False
        missing: list[str] = []

        def close() -> None:
            nonlocal job, has_timeout
            if job and not has_timeout:
                missing.append(job)
            job = None
            has_timeout = False

        for line in lines:
            if line.startswith("jobs:"):
                in_jobs = True
                continue
            if not in_jobs:
                continue
            if line and not line.startswith(" ") and not line.startswith("#"):
                close()
                break
            if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
                close()
                job = line.strip()
                continue
            if job and "timeout-minutes:" in line:
                has_timeout = True
        close()
        assert missing == [], f"{path.name} jobs without timeout-minutes: {missing}"


if __name__ == "__main__":
    test_french_oem_output_is_not_mojibake()
    test_tool_timeouts_are_bounded()
    test_precheck_blocks_admin_space_target_bitlocker_secureboot()
    test_postcheck_requires_files_bcd_and_esp()
    test_simulated_hang_kills_child_and_cleans_up()
    test_failed_exit_and_output_do_not_count_as_success()
    test_failed_precheck_skips_the_process_and_cleans_up()
    test_failed_postcheck_cleans_up()
    test_real_sleep_is_killed()
    test_real_exit_code_is_validated()
    test_copy_timeout_and_download_checks()
    test_workflow_jobs_have_timeouts()
    print("proc guard OK")
