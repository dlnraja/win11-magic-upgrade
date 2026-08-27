"""ISO mount via virtdisk.dll (Win8+/Win10) - no PowerShell Mount-DiskImage."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import string
import time
from pathlib import Path

from .logutil import log

virtdisk = ctypes.windll.virtdisk
kernel32 = ctypes.windll.kernel32

VIRTUAL_STORAGE_TYPE_DEVICE_ISO = 1
VIRTUAL_DISK_ACCESS_ATTACH_RO = 0x00010000
VIRTUAL_DISK_ACCESS_READ = 0x000d0000
ATTACH_VIRTUAL_DISK_FLAG_READ_ONLY = 0x00000001
ATTACH_VIRTUAL_DISK_FLAG_PERMANENT_LIFETIME = 0x00000004
OPEN_VIRTUAL_DISK_FLAG_NONE = 0
DETACH_VIRTUAL_DISK_FLAG_NONE = 0

# Win32: Cannot create a file when that file already exists.
ERROR_ALREADY_EXISTS = 183
# Attach when already attached / sharing
ERROR_SHARING_VIOLATION = 32
DRIVE_CDROM = 5
DRIVE_REMOTE = 4


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# VIRTUAL_STORAGE_TYPE_VENDOR_MICROSOFT
VENDOR_MS = GUID(
    0xEC984AEC,
    0xA0F9,
    0x47E9,
    (ctypes.c_ubyte * 8)(0x90, 0x1F, 0x71, 0x41, 0x5A, 0x66, 0x34, 0x5B),
)


class VIRTUAL_STORAGE_TYPE(ctypes.Structure):
    _fields_ = [("DeviceId", wt.DWORD), ("VendorId", GUID)]


class OPEN_VIRTUAL_DISK_PARAMETERS(ctypes.Structure):
    class _Version1(ctypes.Structure):
        _fields_ = [("RWDepth", wt.ULONG)]

    _fields_ = [("Version", wt.DWORD), ("Version1", _Version1)]


class ATTACH_VIRTUAL_DISK_PARAMETERS(ctypes.Structure):
    class _Version1(ctypes.Structure):
        _fields_ = [("Reserved", wt.ULONG)]

    _fields_ = [("Version", wt.DWORD), ("Version1", _Version1)]


_handles: dict[str, wt.HANDLE] = {}


def _drives() -> set[str]:
    mask = kernel32.GetLogicalDrives()
    return {chr(ord("A") + i) for i in range(26) if mask & (1 << i)}


def _is_setup_root(candidate: str) -> bool:
    p = Path(candidate)
    try:
        return (p / "sources" / "setupprep.exe").is_file() or (p / "setup.exe").is_file()
    except OSError:
        return False


def _pick_setup_root(letters: set[str] | None = None) -> str | None:
    """Pick a drive letter that looks like a Windows Setup ISO."""
    pool = letters if letters is not None else set(string.ascii_uppercase)
    # Prefer optical / newly attached volumes, then any letter with setup.exe
    ranked: list[tuple[int, str]] = []
    for letter in sorted(pool):
        candidate = f"{letter}:\\"
        if not _is_setup_root(candidate):
            continue
        try:
            dtype = int(kernel32.GetDriveTypeW(candidate))
        except Exception:
            dtype = 0
        # Lower score = better. CDROM first, then removable/fixed with setup.
        score = 0 if dtype == DRIVE_CDROM else (1 if dtype != DRIVE_REMOTE else 9)
        ranked.append((score, candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda t: (t[0], t[1]))
    return ranked[0][1]


def find_existing_setup_mount() -> str | None:
    """Return root of an already-mounted Windows Setup ISO, if any."""
    return _pick_setup_root()


def _win32_message(code: int) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    n = kernel32.FormatMessageW(
        0x00001000,  # FORMAT_MESSAGE_FROM_SYSTEM
        None,
        code,
        0,
        buf,
        len(buf),
        None,
    )
    if n:
        return buf.value.strip()
    return f"Win32 error {code}"


def _open_iso(iso_path: str) -> wt.HANDLE:
    storage = VIRTUAL_STORAGE_TYPE(VIRTUAL_STORAGE_TYPE_DEVICE_ISO, VENDOR_MS)
    open_params = OPEN_VIRTUAL_DISK_PARAMETERS()
    open_params.Version = 1
    open_params.Version1.RWDepth = 1
    handle = wt.HANDLE()
    path_w = ctypes.c_wchar_p(iso_path)
    res = virtdisk.OpenVirtualDisk(
        ctypes.byref(storage),
        path_w,
        VIRTUAL_DISK_ACCESS_READ | VIRTUAL_DISK_ACCESS_ATTACH_RO,
        OPEN_VIRTUAL_DISK_FLAG_NONE,
        ctypes.byref(open_params),
        ctypes.byref(handle),
    )
    if res != 0:
        raise OSError(res, f"OpenVirtualDisk failed: {res} ({_win32_message(res)}) for {iso_path}")
    return handle


def _wait_for_setup_root(before: set[str], timeout_s: float = 12.0) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        after = _drives() - before
        root = _pick_setup_root(after) or _pick_setup_root(set(string.ascii_uppercase) - before)
        if root:
            return root
        # Already-mounted case: letter may have been present before attach
        root = _pick_setup_root()
        if root:
            return root
        time.sleep(0.25)
    return _pick_setup_root()


def mount_iso(iso_path: str | Path) -> str:
    """Mount ISO read-only; return root like 'E:\\'.

    ERROR_ALREADY_EXISTS (183) means the ISO is already attached — treat as OK
    and reuse the existing drive letter (common on re-run / Explorer double-mount).
    """
    iso_path = str(Path(iso_path).resolve())

    # Fast path: already mounted somewhere with Setup files
    existing = find_existing_setup_mount()
    if existing:
        # If this ISO was already attached in this process, keep going
        if iso_path in _handles:
            log(f"ISO already mounted in-session at {existing}", "OK")
            return existing

    before = _drives()
    handle = _open_iso(iso_path)

    attach_params = ATTACH_VIRTUAL_DISK_PARAMETERS()
    attach_params.Version = 1
    res = virtdisk.AttachVirtualDisk(
        handle,
        None,
        ATTACH_VIRTUAL_DISK_FLAG_READ_ONLY | ATTACH_VIRTUAL_DISK_FLAG_PERMANENT_LIFETIME,
        0,
        ctypes.byref(attach_params),
        None,
    )

    if res == ERROR_ALREADY_EXISTS:
        log(
            "AttachVirtualDisk: ERROR_ALREADY_EXISTS (183) — ISO already mounted; reusing volume",
            "INFO",
        )
    elif res == ERROR_SHARING_VIOLATION:
        log(
            f"AttachVirtualDisk: sharing violation ({res}); looking for existing Setup mount",
            "WARN",
        )
        kernel32.CloseHandle(handle)
        root = find_existing_setup_mount()
        if root:
            log(f"Reusing already-mounted Setup ISO at {root}", "OK")
            return root
        raise OSError(
            res,
            f"AttachVirtualDisk failed: {res} ({_win32_message(res)}). "
            "Close Explorer ISO mounts / other tools using this ISO, then retry.",
        )
    elif res != 0:
        kernel32.CloseHandle(handle)
        # Last chance: volume already present from a previous attach
        root = find_existing_setup_mount()
        if root:
            log(
                f"AttachVirtualDisk failed ({res}: {_win32_message(res)}) "
                f"but Setup volume found at {root} — reusing",
                "WARN",
            )
            return root
        raise OSError(res, f"AttachVirtualDisk failed: {res} ({_win32_message(res)})")

    _handles[iso_path] = handle
    root = _wait_for_setup_root(before)
    if not root:
        # Detach and clear so a retry can succeed
        try:
            virtdisk.DetachVirtualDisk(handle, DETACH_VIRTUAL_DISK_FLAG_NONE, 0)
        except Exception:
            pass
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass
        _handles.pop(iso_path, None)
        raise RuntimeError(
            "ISO mount OK but setup.exe / setupprep.exe drive letter not found "
            "(error often follows a stuck 183 mount — eject the ISO in Explorer and retry)"
        )

    has_prep = (Path(root) / "sources" / "setupprep.exe").exists()
    log(
        f"ISO mounted at {root} (virtdisk.dll, setupprep={'yes' if has_prep else 'no'})",
        "OK",
    )
    return root


def dismount_iso(iso_path: str | Path) -> None:
    iso_path = str(Path(iso_path).resolve())
    handle = _handles.pop(iso_path, None)
    owned = handle is not None
    try:
        if handle is None:
            handle = _open_iso(iso_path)
        virtdisk.DetachVirtualDisk(handle, DETACH_VIRTUAL_DISK_FLAG_NONE, 0)
        log(f"ISO dismounted: {iso_path}", "INFO")
    except Exception as e:
        log(f"ISO dismount note for {iso_path}: {e}", "WARN")
    finally:
        if handle:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
        if not owned:
            _handles.pop(iso_path, None)
