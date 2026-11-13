"""
Safe diskpart helpers — EN/FR aware, verify selection before mutate.

Fixes classic failures:
  - "No volume selected" / "Aucun volume n'a été sélectionné"
  - "No disk selected" / "Aucun disque n'a été sélectionné"
  - Defaulting system disk to 0 when parse fails (wrong-disk risk)
"""
from __future__ import annotations

import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .logutil import log

NO_VOLUME_RE = re.compile(
    r"No volume selected|"
    r"Aucun volume n.?\s*a\s*[eé]t[eé]\s*s[eé]lectionn[eé]",
    re.I,
)
NO_DISK_RE = re.compile(
    r"No disk selected|"
    r"Aucun disque n.?\s*a\s*[eé]t[eé]\s*s[eé]lectionn[eé]",
    re.I,
)
ERROR_RE = re.compile(
    r"\b(error|failed|denied|échec|echec|erreur|refus|impossible|"
    r"not enough|insuffisant|access is denied|acc[eè]s refus)\b",
    re.I,
)
SUCCESS_SHRINK_RE = re.compile(
    r"successfully|complete|shrunk|r[eé]ussi|termin[eé]|r[eé]duit",
    re.I,
)

# Type column EN + FR (diskpart localized)
TYPE_SYSTEM_RE = re.compile(
    r"System|Syst[eè]me|Reserved|R[eé]serv[eé]|Hidden|Cach[eé]|EFI|ESP|Boot|"
    r"Partition de d[eé]marrage|R[eé]serv[eé]e au syst[eè]me",
    re.I,
)
TYPE_EFI_HINT_RE = re.compile(
    r"System|Syst[eè]me|Hidden|Cach[eé]|EFI|ESP|FAT32",
    re.I,
)


@dataclass
class VolumeInfo:
    index: int
    letter: str | None
    label: str
    fs: str
    type_col: str
    size_mb: int | None
    raw: str


def _run(cmd: list[str], input_text: str | None = None, timeout: int = 300) -> tuple[int, str]:
    from .proc_guard import run_bounded

    result = run_bounded(cmd, timeout=timeout, input_text=input_text)
    return result.code, result.output or result.reason


def oem_encoding() -> str:
    """Console OEM code page. French diskpart reads this, not UTF-8."""
    if os.name == "nt":
        try:
            import ctypes

            page = int(ctypes.windll.kernel32.GetOEMCP())
            if page > 0:
                return f"cp{page}"
        except Exception:
            pass
    return "cp850"


def diskpart_script_bytes(script: str, *, encoding: str | None = None) -> bytes:
    """ANSI/OEM, CRLF, no BOM. diskpart /s rejects UTF-8 scripts on the real PC."""
    text = (script or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        text = "exit"
    if not text.lower().endswith("exit"):
        text += "\nexit"
    body = text.replace("\n", "\r\n") + "\r\n"
    data = body.encode(encoding or oem_encoding(), errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        data = data[2:]
    return data


def decode_diskpart_output(raw: bytes | str, *, encoding: str | None = None) -> str:
    if isinstance(raw, str):
        return raw
    for name in (encoding or oem_encoding(), "cp850", "cp437", "mbcs", "utf-8"):
        try:
            return raw.decode(name)
        except Exception:
            continue
    return raw.decode("cp850", errors="replace")


def vds_is_running(text: str) -> bool:
    """sc query vds, in English or French. A stopped service is not running."""
    if re.search(r"\bSTOPPED\b|ARR[EÊ]T[EÉ]", text or "", re.I):
        return False
    return bool(re.search(r"RUNNING|EX[EÉ]CUTION", text or "", re.I))


def select_lines(*, disk: int | None = None, partition: int | None = None, volume: int | None = None) -> list[str]:
    """Select by numbers taken from the live map. A drive letter is not a selector."""
    if isinstance(volume, str) or isinstance(disk, str) or isinstance(partition, str):
        raise ValueError("diskpart select needs numbers from the live map, not a letter")
    if disk is not None and partition is not None:
        return [f"select disk {int(disk)}", f"select partition {int(partition)}"]
    if volume is not None:
        return [f"select volume {int(volume)}"]
    raise ValueError("diskpart select needs a disk and partition number, or a volume number")


ARGUMENTS_RE = re.compile(
    r"arguments specified for this command are not valid|"
    r"arguments sp[eé]cifi[eé]s.+ne sont pas valides|"
    r"ne sont pas valides",
    re.I,
)
_GUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_LINE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rem", re.compile(r"rem(\s.*)?$", re.I)),
    ("select disk", re.compile(r"select disk \d+$", re.I)),
    ("select partition", re.compile(r"select partition \d+$", re.I)),
    ("select volume", re.compile(r"select volume \d+$", re.I)),
    ("assign letter", re.compile(r"assign letter=[A-Z]$", re.I)),
    ("remove letter", re.compile(r"remove letter=[A-Z]$", re.I)),
    ("active", re.compile(r"active$", re.I)),
    ("set id", re.compile(rf"set id=(?:{_GUID_RE} override|[0-9a-fA-F]{{2}})$", re.I)),
    ("gpt attributes", re.compile(r"gpt attributes=0x[0-9a-fA-F]{1,16}$", re.I)),
    ("list disk", re.compile(r"list disk$", re.I)),
    ("list partition", re.compile(r"list partition$", re.I)),
    ("list volume", re.compile(r"list volume$", re.I)),
    ("detail disk", re.compile(r"detail disk$", re.I)),
    ("detail partition", re.compile(r"detail partition$", re.I)),
    ("detail volume", re.compile(r"detail volume$", re.I)),
    ("create partition efi", re.compile(r"create partition efi size=\d+$", re.I)),
    ("create partition primary", re.compile(r"create partition primary(?: size=\d+)?$", re.I)),
    ("format", re.compile(r"format (?:quick )?fs=(?:fat32|ntfs|fat)(?: quick)?(?: label=\S+)?$", re.I)),
    ("shrink", re.compile(r"shrink desired=\d+(?: minimum=\d+)?$", re.I)),
    ("extend", re.compile(r"extend(?: size=\d+)?$", re.I)),
    ("rescan", re.compile(r"rescan$", re.I)),
    ("clean", re.compile(r"clean$", re.I)),
    ("convert gpt", re.compile(r"convert gpt$", re.I)),
    ("convert mbr", re.compile(r"convert mbr$", re.I)),
    ("offline disk", re.compile(r"offline disk$", re.I)),
    ("online disk", re.compile(r"online disk$", re.I)),
    ("exit", re.compile(r"exit$", re.I)),
)


def arguments_rejected(out: str) -> bool:
    return bool(ARGUMENTS_RE.search(out or ""))


def failing_command(out: str) -> str:
    """The DISKPART> line that the argument error refers to."""
    last = ""
    for line in (out or "").splitlines():
        match = re.search(r"DISKPART>\s*(.+)$", line, re.I)
        if match:
            last = match.group(1).strip()
        elif arguments_rejected(line) and last:
            return last
    return ""


def command_name(line: str) -> str:
    text = line.strip()
    for name, pattern in _LINE_RULES:
        if pattern.fullmatch(text):
            return name
    return ""


def script_lines(script: str) -> list[str]:
    lines = []
    for raw in (script or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        text = raw.strip()
        if text and text.lower() != "exit":
            lines.append(text)
    return lines


def lint_diskpart_script(script: str, *, style: str = "UNKNOWN") -> list[str]:
    """Reject syntax diskpart answers with 'arguments are not valid'."""
    style_u = (style or "UNKNOWN").strip().upper()
    if style_u in ("LEGACY", "BIOS"):
        style_u = "MBR"
    errors: list[str] = []
    focus = ""
    for line in script_lines(script):
        if re.search(r"\bnoerr\b", line, re.I):
            errors.append(f"noerr hides the failing command: {line}")
            continue
        if re.search(r"letter=[A-Z]:", line, re.I):
            errors.append(f"drive letter must not include a colon: {line}")
            continue
        name = command_name(line)
        if not name:
            errors.append(f"unknown diskpart command: {line}")
            continue
        if name == "select disk":
            focus = "disk"
        elif name == "select partition":
            focus = "partition"
        elif name == "select volume":
            focus = "volume"
        elif name.startswith("create partition"):
            if name == "create partition efi" and style_u == "MBR":
                errors.append("create partition efi is only valid on GPT")
            if focus != "disk":
                errors.append(f"{name} requires a selected disk")
            focus = "created"
        elif name in ("assign letter", "remove letter"):
            if focus not in ("volume", "created"):
                errors.append(f"{name} requires a selected volume, not a partition")
        elif name == "active":
            if style_u != "MBR":
                errors.append("active is only valid on an MBR disk")
            if focus != "partition":
                errors.append("active requires a selected partition")
        elif name == "set id":
            if style_u == "GPT":
                if not re.fullmatch(rf"set id={_GUID_RE} override", line, re.I):
                    errors.append("GPT set id must be: set id=<guid> override")
            elif style_u == "MBR":
                if not re.fullmatch(r"set id=[0-9a-fA-F]{2}", line):
                    errors.append("MBR set id must be a byte such as set id=07")
            else:
                errors.append("set id requires a known GPT or MBR style")
            if focus != "partition":
                errors.append("set id requires a selected partition")
        elif name == "gpt attributes":
            if style_u != "GPT":
                errors.append("gpt attributes is only valid on GPT")
            if focus != "partition":
                errors.append("gpt attributes requires a selected partition")
        elif name in ("shrink", "extend", "format") and focus not in ("volume", "partition", "created", "disk"):
            errors.append(f"{name} requires a selected disk, partition, or volume")
    return errors


def step_scripts(script: str) -> list[tuple[str, str]]:
    """One new command per diskpart process. Selects are repeated so focus still exists."""
    selects: list[str] = []
    steps: list[tuple[str, str]] = []
    for line in script_lines(script):
        name = command_name(line)
        if name in ("select disk", "select partition", "select volume"):
            selects = [item for item in selects if command_name(item) != name]
            selects.append(line)
            body = selects
        elif name in ("list disk", "rescan"):
            body = [line]
        else:
            body = selects + [line]
        steps.append((line, "\n".join(body + ["exit"])))
    return steps


def _create_transaction(script: str) -> bool:
    names = {command_name(line) for line in script_lines(script)}
    return bool(names & {"create partition efi", "create partition primary", "clean", "convert gpt", "convert mbr"})


def diskpart_failed(out: str) -> bool:
    """Nothing selected, or diskpart rejected the command arguments."""
    if not out:
        return False
    return bool(NO_VOLUME_RE.search(out) or NO_DISK_RE.search(out) or arguments_rejected(out))


def _write_script_file(data: bytes) -> Path:
    folder = Path(tempfile.gettempdir()) / "Win11MagicUpgrade"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"dp-{os.getpid()}-{uuid.uuid4().hex}.txt"
    path.write_bytes(data)
    return path


def _run_diskpart_file(argv: list[str], timeout: int) -> tuple[int, str]:
    return _run(argv, timeout=timeout)


def run_diskpart(
    script: str,
    *,
    timeout: int = 180,
    runner: Callable[[list[str], bytes, int], tuple[int, bytes | str]] | None = None,
    encoding: str | None = None,
    wait_vds: Callable[[], None] | None = None,
    style: str = "UNKNOWN",
) -> tuple[bool, str]:
    """
    Lint the script, then run one command per ``diskpart /s`` file.
    An argument error is not retried: the next storage method is used instead.
    """
    problems = lint_diskpart_script(script, style=style)
    if problems:
        text = "\n".join(problems)
        log(f"diskpart script refused: {text}", "ERROR")
        return False, text
    if wait_vds is not None:
        wait_vds()
    else:
        _wait_for_virtual_disk()

    def once(text: str) -> tuple[int, str]:
        data = diskpart_script_bytes(text, encoding=encoding)
        path = _write_script_file(data)
        argv = ["diskpart", "/s", str(path)]
        try:
            if runner is not None:
                code, raw = runner(argv, data, timeout)
            else:
                code, raw = _run_diskpart_file(argv, timeout)
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        if isinstance(raw, bytes):
            raw = decode_diskpart_output(raw, encoding=encoding)
        return int(code), raw or ""

    steps = [(script_lines(script)[-1] if script_lines(script) else script, script)]
    if not _create_transaction(script):
        steps = step_scripts(script)
    last_out = ""
    for command, step in steps:
        code, out = once(step)
        last_out = out
        if code == 124:
            log(f"diskpart TIMEOUT on: {command}", "ERROR")
            return False, out
        if arguments_rejected(out):
            bad = failing_command(out) or command
            log(f"diskpart arguments rejected: {bad}", "ERROR")
            return False, out
        if code == 0 and not diskpart_failed(out):
            continue
        if _create_transaction(script):
            return False, out
        log(f"diskpart command failed: {command}; retrying once after rescan", "WARN")
        code2, out2 = once("rescan\n" + step)
        last_out = out2
        if arguments_rejected(out2) or code2 == 124 or code2 != 0 or diskpart_failed(out2):
            bad = failing_command(out2) or command
            log(f"diskpart command failed: {bad}", "ERROR")
            return False, out2
    return True, last_out


def _wait_for_virtual_disk() -> None:
    if os.name != "nt":
        return
    code, out = _run(["sc", "query", "vds"], timeout=20)
    if code == 0 and vds_is_running(out or ""):
        return
    log("Virtual Disk service is not running; starting it", "INFO")
    _run(["sc", "start", "vds"], timeout=30)
    _run(["sc", "query", "vds"], timeout=20)


def run_methods(op: str, methods: list[tuple[str, Callable[[], bool]]]) -> str | None:
    """Try each method. The first one whose result verifies is the one that is logged."""
    for name, fn in methods:
        try:
            ok = bool(fn())
        except Exception as exc:
            log(f"{op} via {name} failed: {exc}", "WARN")
            continue
        if ok:
            log(f"{op} succeeded via {name}", "OK")
            return name
        log(f"{op} via {name} did not verify", "WARN")
    log(f"{op} failed on every method", "ERROR")
    return None


def powershell_args(script: str) -> list[str]:
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]


def _disk_style(style: str) -> str:
    name = (style or "UNKNOWN").strip().upper()
    if name in ("LEGACY", "BIOS"):
        return "MBR"
    return name


def _bare_letter(letter: str | None) -> str:
    token = (letter or "").strip().upper().rstrip("\\").rstrip(":")
    return token[:1]


def commands_for(
    op: str,
    *,
    disk: int | None = None,
    partition: int | None = None,
    volume: int | None = None,
    letter: str | None = None,
    volume_guid: str | None = None,
    folder: str | None = None,
    gpt_type: str | None = None,
    active: bool | None = None,
    style: str = "UNKNOWN",
    attributes: str | None = None,
) -> dict[str, str]:
    """Command text for each fallback. Diskpart text is empty when the style forbids it."""
    style_u = _disk_style(style)
    letter_s = _bare_letter(letter)
    guid = (volume_guid or "").strip()
    mount = folder or ""
    ps = ""
    cim = ""
    mountvol = ""
    folder_cmd = ""
    diskpart = ""
    partition_focus = ""
    if disk is not None and partition is not None:
        partition_focus = f"select disk {int(disk)}\nselect partition {int(partition)}\n"
    if op == "assign":
        ps = (
            f"Add-PartitionAccessPath -DiskNumber {int(disk or 0)} -PartitionNumber {int(partition or 0)} "
            f"-AccessPath '{letter_s}:' -ErrorAction Stop"
        )
        cim = (
            "$p = Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Partition "
            f"-Filter \"DiskNumber={int(disk or 0)} AND PartitionNumber={int(partition or 0)}\"; "
            f"Invoke-CimMethod -InputObject $p -MethodName AddAccessPath -Arguments @{{AccessPath='{letter_s}:\\'}}"
        )
        mountvol = f"mountvol {letter_s}: {guid}" if guid else ""
        folder_cmd = (
            f"Add-PartitionAccessPath -DiskNumber {int(disk or 0)} -PartitionNumber {int(partition or 0)} "
            f"-AccessPath '{mount}' -ErrorAction Stop"
            if mount
            else ""
        )
        if volume is not None and letter_s:
            diskpart = f"select volume {int(volume)}\nassign letter={letter_s}\nexit"
    elif op == "remove":
        ps = (
            f"Remove-PartitionAccessPath -DiskNumber {int(disk or 0)} -PartitionNumber {int(partition or 0)} "
            f"-AccessPath '{letter_s}:' -ErrorAction Stop"
        )
        cim = (
            "$p = Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Partition "
            f"-Filter \"DiskNumber={int(disk or 0)} AND PartitionNumber={int(partition or 0)}\"; "
            f"Invoke-CimMethod -InputObject $p -MethodName RemoveAccessPath -Arguments @{{AccessPath='{letter_s}:\\'}}"
        )
        mountvol = f"mountvol {letter_s}: /d" if letter_s else ""
        folder_cmd = (
            f"Remove-PartitionAccessPath -DiskNumber {int(disk or 0)} -PartitionNumber {int(partition or 0)} "
            f"-AccessPath '{mount}' -ErrorAction SilentlyContinue"
            if mount
            else ""
        )
        if volume is not None and letter_s:
            diskpart = f"select volume {int(volume)}\nremove letter={letter_s}\nexit"
    elif op == "active":
        flag = "$true" if active is not False else "$false"
        ps = (
            f"Set-Partition -DiskNumber {int(disk or 0)} -PartitionNumber {int(partition or 0)} "
            f"-IsActive {flag} -ErrorAction Stop"
        )
        cim = (
            "$p = Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Partition "
            f"-Filter \"DiskNumber={int(disk or 0)} AND PartitionNumber={int(partition or 0)}\"; "
            "Invoke-CimMethod -InputObject $p -MethodName SetAttributes -Arguments @{IsActive=$true}"
        )
        if style_u == "MBR" and partition_focus:
            diskpart = partition_focus + "active\nexit"
    elif op == "set-id":
        ident = (gpt_type or "").strip().strip("{}")
        ps = (
            f"Set-Partition -DiskNumber {int(disk or 0)} -PartitionNumber {int(partition or 0)} "
            f"-GptType '{{{ident}}}' -ErrorAction Stop"
        )
        cim = (
            "$p = Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Partition "
            f"-Filter \"DiskNumber={int(disk or 0)} AND PartitionNumber={int(partition or 0)}\"; "
            f"Set-CimInstance -InputObject $p -Property @{{GptType='{{{ident}}}'}}"
        )
        if partition_focus and style_u == "GPT" and re.fullmatch(_GUID_RE, ident):
            diskpart = partition_focus + f"set id={ident} override\nexit"
        elif partition_focus and style_u == "MBR" and re.fullmatch(r"[0-9a-fA-F]{2}", ident):
            diskpart = partition_focus + f"set id={ident}\nexit"
    elif op == "gpt-attributes":
        raw = (attributes or "").strip().lower()
        if raw and not raw.startswith("0x"):
            raw = "0x" + raw
        if partition_focus and style_u == "GPT" and re.fullmatch(r"0x[0-9a-f]{1,16}", raw):
            diskpart = partition_focus + f"gpt attributes={raw}\nexit"
    else:
        diskpart = "list disk\nlist volume\nexit" if disk is None else f"select disk {int(disk)}\nlist partition\nlist volume\nexit"
        ps = "Get-Partition | Select-Object DiskNumber,PartitionNumber,DriveLetter,GptType,IsActive | Format-Table | Out-String"
        cim = "Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Partition | Out-String"
        mountvol = "mountvol"
        folder_cmd = ""
    if diskpart and lint_diskpart_script(diskpart, style=style_u):
        diskpart = ""
    return {
        "diskpart": diskpart,
        "powershell": ps,
        "cim": cim,
        "mountvol": mountvol,
        "folder": folder_cmd,
    }


def parse_list_volume(out: str) -> list[VolumeInfo]:
    """
    Parse `list volume` output (EN/FR). Columns roughly:
      Volume ###  Ltr  Label        Fs     Type        Size     Status     Info
    """
    vols: list[VolumeInfo] = []
    for line in out.splitlines():
        if not re.search(r"Volume\s+\d+", line, re.I):
            continue
        # Skip header-ish
        if re.search(r"Volume\s+###", line, re.I):
            continue
        m = re.search(r"Volume\s+(\d+)", line, re.I)
        if not m:
            continue
        idx = int(m.group(1))
        # Letter: after volume number, optional single A-Z column
        letter = None
        lm = re.search(r"Volume\s+\d+\s+([A-Z])\s+", line, re.I)
        if lm:
            letter = lm.group(1).upper()
        else:
            # Sometimes blank letter then label — try tokens
            rest = re.sub(r"^\s*Volume\s+\d+\s+", "", line, flags=re.I)
            toks = rest.split()
            if toks and len(toks[0]) == 1 and toks[0].isalpha():
                letter = toks[0].upper()

        fs = ""
        fsm = re.search(r"\b(NTFS|FAT32|FAT|exFAT|ReFS|CSVFS)\b", line, re.I)
        if fsm:
            fs = fsm.group(1).upper()

        size_mb = None
        sm = re.search(r"(\d+)\s*(GB|MB|Go|Mo)\b", line, re.I)
        if sm:
            size_mb = int(sm.group(1))
            if sm.group(2).upper() in ("GB", "GO"):
                size_mb *= 1024

        # Label: between letter and FS — best-effort
        label = ""
        if fsm:
            before = line[: fsm.start()]
            parts = before.split()
            # drop Volume N and optional letter
            if parts and parts[0].lower() == "volume":
                parts = parts[2:]  # Volume + num
            if parts and len(parts[0]) == 1 and parts[0].isalpha():
                parts = parts[1:]
            label = " ".join(parts).strip()

        type_col = ""
        # Type words often appear after FS
        tm = TYPE_SYSTEM_RE.search(line)
        if tm:
            type_col = tm.group(0)

        vols.append(
            VolumeInfo(
                index=idx,
                letter=letter,
                label=label,
                fs=fs,
                type_col=type_col,
                size_mb=size_mb,
                raw=line.strip(),
            )
        )
    return vols


def list_volumes() -> list[VolumeInfo]:
    ok, out = run_diskpart("list volume\nexit\n")
    if not ok and not out:
        return []
    return parse_list_volume(out)


def find_volume_by_letter(letter: str) -> VolumeInfo | None:
    L = letter.strip().rstrip(":\\").upper()[:1]
    for v in list_volumes():
        if v.letter == L:
            return v
    return None


def ensure_select_volume(vol: int | str) -> tuple[bool, str]:
    """Select a volume by the number from ``list volume``. A letter is refused."""
    token = str(vol).strip()
    if token.rstrip(":\\").isalpha():
        log("diskpart select by letter is refused; use the volume number from the live list", "ERROR")
        return False, "select-by-letter-refused"
    script = "\n".join(select_lines(volume=int(token)) + ["detail volume", "exit"])
    ok, out = run_diskpart(script)
    if not ok or diskpart_failed(out):
        return False, out
    return True, out


def ensure_select_disk(disk_n: int) -> tuple[bool, str]:
    script = f"select disk {int(disk_n)}\ndetail disk\nexit\n"
    ok, out = run_diskpart(script)
    if not ok:
        return False, out
    if NO_DISK_RE.search(out):
        return False, out
    if not re.search(r"Disk|Disque", out, re.I):
        return False, out
    return True, out


def disk_number_from_detail(detail_out: str) -> int | None:
    """Parse Disk #N from `detail volume` (EN Disk / FR Disque / variants)."""
    if not detail_out:
        return None
    patterns = (
        # FR: Disque n° : 0 | Disque #: 0 | Disque : 0 | Disque n°0
        r"Disque\s*(?:n[°ºo.]?\s*)?#?\s*:?\s*(\d+)",
        # EN: Disk #: 0 | Disk # : 0 | Disk: 0
        r"Disk\s*(?:#\s*)?:?\s*(\d+)",
        # list-style: Disk ###  or Disque ###
        r"Dis(?:k|que)\s*#+\s*(\d+)",
        # "on Disk 0" / "sur le disque 0"
        r"(?:on\s+disk|sur\s+le\s+disque)\s+(\d+)",
    )
    for pat in patterns:
        m = re.search(pat, detail_out, re.I)
        if m:
            return int(m.group(1))
    return None


def _disk_number_from_wmic(letter: str) -> int | None:
    """Fallback: Win32_LogicalDiskToPartition (works when diskpart text is exotic / WMIC gone)."""
    L = letter.upper()[:1]
    try:
        from .wmi_compat import diskdrive_index_for_system, wmi_query

        n = diskdrive_index_for_system()
        if n is not None:
            return n
        out = wmi_query(
            "path",
            "Win32_LogicalDiskToPartition",
            "get",
            "Antecedent,Dependent",
            cim_fallback=(
                "Get-CimInstance Win32_LogicalDiskToPartition | "
                "ForEach-Object { $_.Antecedent; $_.Dependent }"
            ),
        )
    except Exception:
        return None
    if not out:
        return None
    # Antecedent ... Disk #0, Partition #2  Dependent ... DeviceID="C:"
    for line in out.splitlines():
        if f'DeviceID="{L}:"' not in line and f"DeviceID='{L}:'" not in line:
            # some locales omit quotes / use DeviceID=C:
            if f"{L}:" not in line.replace(" ", ""):
                continue
            if "LogicalDisk" not in line and "DeviceID" not in line:
                continue
        m = re.search(r"Disk\s*#\s*(\d+)", line, re.I)
        if m:
            return int(m.group(1))
        m = re.search(r"Disk\s+(\d+)", line, re.I)
        if m:
            return int(m.group(1))
    return None


def get_system_disk_number(letter: str | None = None) -> int | None:
    """
    Resolve the disk that hosts SystemDrive. Returns None if ambiguous
    (never invent 0).
    """
    L = (letter or os.environ.get("SystemDrive", "C:")[:1]).upper()[:1]
    n: int | None = None
    detail = ""

    match = find_volume_by_letter(L)
    ok, detail = (False, "")
    if match is not None:
        ok, detail = ensure_select_volume(match.index)
    if ok:
        n = disk_number_from_detail(detail)
        if n is None:
            log(f"Cannot parse Disk # from volume {L}: detail (trying WMIC fallback)", "WARN")
    else:
        log(f"Cannot select system volume {L}: — trying WMIC fallback", "WARN")

    if n is None:
        try:
            n = _disk_number_from_wmic(L)
            if n is not None:
                log(f"System volume {L}: disk #{n} via WMIC fallback", "OK")
        except Exception as e:
            log(f"WMIC disk# fallback: {e}", "WARN")

    if n is None:
        # Last resort: PowerShell Get-Partition (optional; many PCs have it)
        try:
            ps = (
                f"(Get-Partition -DriveLetter {L} -ErrorAction Stop).DiskNumber"
            )
            code, out = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    ps,
                ],
                timeout=45,
            )
            m = re.search(r"(\d+)", (out or "").strip())
            if code == 0 and m:
                n = int(m.group(1))
                log(f"System volume {L}: disk #{n} via Get-Partition fallback", "OK")
        except Exception as e:
            log(f"Get-Partition disk# fallback: {e}", "WARN")

    if n is None:
        log(f"Cannot resolve Disk # for volume {L}:", "WARN")
        return None

    # Cross-check disk select works
    ok2, _ = ensure_select_disk(n)
    if not ok2:
        log(f"Cannot select disk {n} after parsing from {L}:", "WARN")
        return None
    log(f"System volume {L}: is on disk #{n}", "OK")
    return n


def _letter_present(letter: str) -> bool:
    return Path(f"{letter.upper()[:1]}:\\").exists()


def _exec_ok(argv: list[str], *, timeout: int, exec_command: Callable[[list[str], int], tuple[int, str]] | None) -> bool:
    if not argv or not argv[-1]:
        return False
    if exec_command is None:
        code, _out = _run(argv, timeout=timeout)
        return code == 0
    code, _out = exec_command(argv, timeout)
    return int(code) == 0


def assign_partition_letter(
    disk: int | None,
    partition: int | None,
    letter: str,
    *,
    volume: int | None = None,
    volume_guid: str | None = None,
    folder: str | None = None,
    verify: Callable[[], bool] | None = None,
    exec_command: Callable[[list[str], int], tuple[int, str]] | None = None,
    runner: Callable[[list[str], bytes, int], tuple[int, bytes | str]] | None = None,
) -> str | None:
    """Assign a letter. Diskpart, then Storage, CIM, mountvol, then a folder mount."""
    loc_letter = letter.upper()[:1]
    check = verify or (lambda: _letter_present(loc_letter))
    cmds = commands_for(
        "assign",
        disk=disk,
        partition=partition,
        volume=volume,
        letter=loc_letter,
        volume_guid=volume_guid,
        folder=folder,
    )

    def via_diskpart() -> bool:
        if not cmds["diskpart"].strip():
            return False
        ok, out = run_diskpart(cmds["diskpart"], runner=runner, wait_vds=(lambda: None) if runner else None)
        return ok and not arguments_rejected(out) and check()

    def via_ps() -> bool:
        if disk is None or partition is None:
            return False
        return _exec_ok(powershell_args(cmds["powershell"]), timeout=60, exec_command=exec_command) and check()

    def via_cim() -> bool:
        if disk is None or partition is None:
            return False
        return _exec_ok(powershell_args(cmds["cim"]), timeout=60, exec_command=exec_command) and check()

    def via_mountvol() -> bool:
        if not volume_guid:
            return False
        return _exec_ok(["mountvol", f"{loc_letter}:", volume_guid], timeout=60, exec_command=exec_command) and check()

    def via_folder() -> bool:
        if disk is None or partition is None or not folder:
            return False
        return _exec_ok(powershell_args(cmds["folder"]), timeout=60, exec_command=exec_command) and Path(folder).exists()

    return run_methods(
        f"assign {loc_letter}",
        [
            ("diskpart", via_diskpart),
            ("powershell", via_ps),
            ("cim", via_cim),
            ("mountvol", via_mountvol),
            ("folder", via_folder),
        ],
    )


def assign_letter_to_volume(
    vol_index: int,
    letter: str,
    *,
    disk: int | None = None,
    partition: int | None = None,
    volume_guid: str | None = None,
    folder: str | None = None,
) -> bool:
    return (
        assign_partition_letter(
            disk,
            partition,
            letter,
            volume=int(vol_index),
            volume_guid=volume_guid,
            folder=folder,
        )
        is not None
    )


def remove_partition_letter(
    disk: int | None,
    partition: int | None,
    letter: str | None = None,
    *,
    volume: int | None = None,
    folder: str | None = None,
    verify: Callable[[], bool] | None = None,
    exec_command: Callable[[list[str], int], tuple[int, str]] | None = None,
    runner: Callable[[list[str], bytes, int], tuple[int, bytes | str]] | None = None,
) -> str | None:
    loc_letter = (letter or "").upper()[:1]
    check = verify or (lambda: not loc_letter or not _letter_present(loc_letter))
    cmds = commands_for(
        "remove",
        disk=disk,
        partition=partition,
        volume=volume,
        letter=loc_letter or None,
        folder=folder,
    )

    def via_diskpart() -> bool:
        if not cmds["diskpart"].strip():
            return False
        ok, out = run_diskpart(cmds["diskpart"], runner=runner, wait_vds=(lambda: None) if runner else None)
        return ok and not arguments_rejected(out) and check()

    def via_ps() -> bool:
        if disk is None or partition is None or not loc_letter:
            return False
        return _exec_ok(powershell_args(cmds["powershell"]), timeout=60, exec_command=exec_command) and check()

    def via_cim() -> bool:
        if disk is None or partition is None or not loc_letter:
            return False
        return _exec_ok(powershell_args(cmds["cim"]), timeout=60, exec_command=exec_command) and check()

    def via_mountvol() -> bool:
        if not loc_letter:
            return False
        return _exec_ok(["mountvol", f"{loc_letter}:", "/d"], timeout=60, exec_command=exec_command) and check()

    def via_folder() -> bool:
        if not folder or disk is None or partition is None:
            return False
        ran = _exec_ok(powershell_args(cmds["folder"]), timeout=60, exec_command=exec_command)
        return ran and not Path(folder).exists()

    return run_methods(
        f"remove {loc_letter or volume}",
        [
            ("diskpart", via_diskpart),
            ("powershell", via_ps),
            ("cim", via_cim),
            ("mountvol", via_mountvol),
            ("folder", via_folder),
        ],
    )


def remove_letter_from_volume(
    vol_index: int,
    letter: str | None = None,
    *,
    disk: int | None = None,
    partition: int | None = None,
) -> bool:
    """Remove a drive letter by volume number. A missing letter is success."""
    if letter and not _letter_present(letter):
        log(f"Letter {letter.upper()[:1]}: is already absent", "INFO")
        return True
    return (
        remove_partition_letter(disk, partition, letter, volume=int(vol_index))
        is not None
    )


def mark_partition_active(
    disk: int,
    partition: int,
    *,
    style: str = "MBR",
    verify: Callable[[], bool] | None = None,
    exec_command: Callable[[list[str], int], tuple[int, str]] | None = None,
    runner: Callable[[list[str], bytes, int], tuple[int, bytes | str]] | None = None,
) -> str | None:
    """Set the active flag by disk and partition number. Diskpart active is MBR only."""
    cmds = commands_for("active", disk=disk, partition=partition, active=True, style=style)
    check = verify or (lambda: True)

    def via_diskpart() -> bool:
        if not cmds["diskpart"].strip():
            return False
        ok, out = run_diskpart(
            cmds["diskpart"],
            runner=runner,
            style=style,
            wait_vds=(lambda: None) if runner else None,
        )
        return ok and not arguments_rejected(out) and check()

    def via_ps() -> bool:
        return _exec_ok(powershell_args(cmds["powershell"]), timeout=60, exec_command=exec_command) and check()

    def via_cim() -> bool:
        return _exec_ok(powershell_args(cmds["cim"]), timeout=60, exec_command=exec_command) and check()

    return run_methods(
        f"active disk {disk} partition {partition}",
        [
            ("diskpart", via_diskpart),
            ("powershell", via_ps),
            ("cim", via_cim),
            ("mountvol", lambda: False),
            ("folder", lambda: False),
        ],
    )


def set_partition_id(
    disk: int,
    partition: int,
    gpt_type: str,
    *,
    style: str = "UNKNOWN",
    verify: Callable[[], bool] | None = None,
    exec_command: Callable[[list[str], int], tuple[int, str]] | None = None,
    runner: Callable[[list[str], bytes, int], tuple[int, bytes | str]] | None = None,
) -> str | None:
    cmds = commands_for("set-id", disk=disk, partition=partition, gpt_type=gpt_type, style=style)
    check = verify or (lambda: True)

    def via_diskpart() -> bool:
        if not cmds["diskpart"].strip():
            return False
        ok, out = run_diskpart(
            cmds["diskpart"],
            runner=runner,
            style=style,
            wait_vds=(lambda: None) if runner else None,
        )
        return ok and not arguments_rejected(out) and check()

    def via_ps() -> bool:
        return _exec_ok(powershell_args(cmds["powershell"]), timeout=60, exec_command=exec_command) and check()

    def via_cim() -> bool:
        return _exec_ok(powershell_args(cmds["cim"]), timeout=60, exec_command=exec_command) and check()

    return run_methods(
        f"set id disk {disk} partition {partition}",
        [
            ("diskpart", via_diskpart),
            ("powershell", via_ps),
            ("cim", via_cim),
            ("mountvol", lambda: False),
            ("folder", lambda: False),
        ],
    )


def shrink_volume_letter(letter: str, desired_mb: int, minimum_mb: int) -> bool:
    L = letter.upper()[:1]
    match = find_volume_by_letter(L)
    if match is None:
        log(f"shrink aborted: volume {L}: is not in the live list", "ERROR")
        return False
    ok, out = run_diskpart(
        "\n".join(select_lines(volume=match.index) + [f"shrink desired={int(desired_mb)} minimum={int(minimum_mb)}", "exit"])
    )
    log(f"shrink {L}: {(out.splitlines()[-1] if out else 'n/a')}")
    return ok


def find_esp_candidates() -> list[VolumeInfo]:
    """FAT32/ESP-like volumes (EN+FR type columns)."""
    out: list[VolumeInfo] = []
    for v in list_volumes():
        if v.fs and v.fs.upper() not in ("FAT32", "FAT"):
            # ESP is almost always FAT32; skip NTFS here
            if "EFI" not in (v.type_col or "").upper() and "ESP" not in (v.label or "").upper():
                continue
        if v.size_mb is not None and v.size_mb > 2048:
            continue
        line = v.raw
        if TYPE_EFI_HINT_RE.search(line) or TYPE_SYSTEM_RE.search(line):
            out.append(v)
    return out


def find_system_reserved_candidates() -> list[VolumeInfo]:
    """Small NTFS System Reserved / Boot (BIOS/MBR) — EN+FR."""
    out: list[VolumeInfo] = []
    for v in list_volumes():
        if v.fs and v.fs.upper() not in ("NTFS", "FAT32", "FAT"):
            continue
        if v.size_mb is not None and v.size_mb > 2000:
            continue
        if TYPE_SYSTEM_RE.search(v.raw) or re.search(
            r"System Reserved|R[eé]serv[eé]|Boot", v.label or "", re.I
        ):
            out.append(v)
    return out


def free_letter(candidates: tuple[str, ...] | None = None) -> str | None:
    """Any free letter. A caller may pass a short list; the default scans Z..D."""
    from .drive_map import pick_free_letter

    if candidates:
        used = {chr(c) for c in range(ord("A"), ord("Z") + 1)}
        for letter in candidates:
            token = (letter or "").upper()[:1]
            if token and not Path(f"{token}:\\").exists():
                used.discard(token)
        # Prefer the caller's order when they passed one, still skip letters that exist.
        for letter in candidates:
            token = (letter or "").upper()[:1]
            if token and token not in ("A", "B") and not Path(f"{token}:\\").exists():
                return token
        return None
    used = set()
    for code in range(ord("A"), ord("Z") + 1):
        letter = chr(code)
        if Path(f"{letter}:\\").exists():
            used.add(letter)
    return pick_free_letter(used)
