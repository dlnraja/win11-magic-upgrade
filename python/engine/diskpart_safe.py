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
import subprocess
from dataclasses import dataclass
from pathlib import Path

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
    try:
        r = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 1, str(e)


def diskpart_failed(out: str) -> bool:
    if not out:
        return False
    if NO_VOLUME_RE.search(out) or NO_DISK_RE.search(out):
        return True
    # diskpart often returns 0 even on script errors — scan text
    if NO_VOLUME_RE.search(out) or NO_DISK_RE.search(out):
        return True
    return False


def run_diskpart(script: str, *, timeout: int = 300) -> tuple[bool, str]:
    """
    Run a diskpart script. Returns (ok, full_output).
    ok=False if selection/error markers (EN+FR) appear.
    """
    code, out = _run(["diskpart"], input_text=script if script.endswith("\n") else script + "\n", timeout=timeout)
    if code == 124:
        log("diskpart TIMEOUT", "ERROR")
        return False, out
    if diskpart_failed(out):
        # Surface the localized error line
        for line in out.splitlines():
            if NO_VOLUME_RE.search(line) or NO_DISK_RE.search(line) or ERROR_RE.search(line):
                log(f"diskpart: {line.strip()}", "WARN")
                break
        return False, out
    if ERROR_RE.search(out) and not SUCCESS_SHRINK_RE.search(out):
        # Soft: some informational lines contain "error" in paths — keep cautious
        if re.search(
            r"DiskPart.*(error|failed|échec|echec|erreur)|"
            r"(error|failed|échec|echec|erreur).*(DiskPart|volume|disque|disk)",
            out,
            re.I,
        ):
            return False, out
    return True, out


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
        sm = re.search(r"(\d+)\s*(GB|MB)\b", line, re.I)
        if sm:
            size_mb = int(sm.group(1))
            if sm.group(2).upper() == "GB":
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
    """
    Select volume by index (preferred) or letter. Verifies no 'no volume selected'.
    """
    script = f"select volume {vol}\ndetail volume\nexit\n"
    ok, out = run_diskpart(script)
    if not ok:
        return False, out
    if NO_VOLUME_RE.search(out):
        return False, out
    # Must show volume detail markers
    if not re.search(r"Volume|Disk|Disque", out, re.I):
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
    """Parse Disk #N from `detail volume` (EN/FR)."""
    m = re.search(r"Disk(?:e)?\s*#?\s*:?\s*(\d+)", detail_out, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"Disk\s+(\d+)", detail_out, re.I)
    if m:
        return int(m.group(1))
    return None


def get_system_disk_number(letter: str | None = None) -> int | None:
    """
    Resolve the disk that hosts SystemDrive. Returns None if ambiguous
    (never invent 0).
    """
    L = (letter or os.environ.get("SystemDrive", "C:")[:1]).upper()[:1]
    ok, detail = ensure_select_volume(L)
    if not ok:
        log(f"Cannot select system volume {L}: — refuse disk# guess", "WARN")
        return None
    n = disk_number_from_detail(detail)
    if n is None:
        log(f"Cannot parse Disk # from volume {L}: detail", "WARN")
        return None
    # Cross-check disk select works
    ok2, _ = ensure_select_disk(n)
    if not ok2:
        log(f"Cannot select disk {n} after parsing from {L}:", "WARN")
        return None
    log(f"System volume {L}: is on disk #{n}", "OK")
    return n


def assign_letter_to_volume(vol_index: int, letter: str) -> bool:
    L = letter.upper()[:1]
    ok, out = run_diskpart(
        f"select volume {int(vol_index)}\n"
        f"assign letter={L}\n"
        f"detail volume\n"
        f"exit\n"
    )
    if not ok:
        log(f"assign letter {L}: failed for volume {vol_index}: {out[-200:]}", "WARN")
        return False
    if not Path(f"{L}:\\").exists():
        log(f"Letter {L}: not present after assign", "WARN")
        return False
    return True


def remove_letter_from_volume(vol_index: int, letter: str | None = None) -> bool:
    """Remove drive letter by volume index (safe). Optional letter for logging."""
    cmds = [f"select volume {int(vol_index)}"]
    if letter:
        cmds.append(f"remove letter={letter.upper()[:1]}")
    else:
        cmds.append("remove all")
    cmds.append("exit")
    ok, out = run_diskpart("\n".join(cmds) + "\n")
    if not ok:
        # Letter may already be gone — not fatal
        if NO_VOLUME_RE.search(out):
            log(f"remove letter: volume {vol_index} not selectable (already gone?)", "INFO")
            return True
        log(f"remove letter volume {vol_index}: {out[-200:]}", "WARN")
        return False
    return True


def shrink_volume_letter(letter: str, desired_mb: int, minimum_mb: int) -> bool:
    L = letter.upper()[:1]
    ok_sel, _ = ensure_select_volume(L)
    if not ok_sel:
        log(f"shrink aborted: cannot select volume {L}:", "ERROR")
        return False
    ok, out = run_diskpart(
        f"select volume {L}\n"
        f"shrink desired={int(desired_mb)} minimum={int(minimum_mb)}\n"
        f"exit\n"
    )
    log(f"shrink {L}: {(out.splitlines()[-1] if out else 'n/a')}")
    if not ok:
        return False
    if ERROR_RE.search(out) and not SUCCESS_SHRINK_RE.search(out):
        if re.search(r"not enough|insuffisant|failed|échec|echec|error|erreur", out, re.I):
            return False
    return True


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


def free_letter(candidates: tuple[str, ...] = ("Y", "X", "W", "V", "U", "S", "T", "R", "Q")) -> str | None:
    for L in candidates:
        if not Path(f"{L}:\\").exists():
            return L
    return None
