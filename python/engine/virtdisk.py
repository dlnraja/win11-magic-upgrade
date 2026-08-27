"""ISO mount via virtdisk.dll (Win8+/Win10) - no PowerShell Mount-DiskImage."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
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


def mount_iso(iso_path: str | Path) -> str:
    """Mount ISO read-only; return root like 'E:\\'."""
    iso_path = str(Path(iso_path).resolve())
    before = _drives()

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
        raise OSError(res, f"OpenVirtualDisk failed: {res} for {iso_path}")

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
    if res != 0:
        kernel32.CloseHandle(handle)
        raise OSError(res, f"AttachVirtualDisk failed: {res}")

    _handles[iso_path] = handle
    # Wait for drive letter
    root = None
    for _ in range(40):
        time.sleep(0.25)
        after = _drives() - before
        for letter in sorted(after):
            candidate = f"{letter}:\\"
            if (Path(candidate) / "setup.exe").exists():
                root = candidate
                break
        if root:
            break
        # Also scan all drives for setup.exe matching mount time
        if not root:
            for letter in string.ascii_uppercase:
                candidate = f"{letter}:\\"
                if (Path(candidate) / "setup.exe").exists() and letter not in before:
                    root = candidate
                    break
        if root:
            break

    if not root:
        # Last resort: any drive with sources\install.wim / esd from this session
        for letter in string.ascii_uppercase:
            candidate = f"{letter}:\\"
            if (Path(candidate) / "setup.exe").exists():
                root = candidate
                break

    if not root:
        raise RuntimeError("ISO mounted but setup.exe drive letter not found")

    log(f"ISO mounted at {root} (virtdisk.dll, no PowerShell)", "OK")
    return root


def dismount_iso(iso_path: str | Path) -> None:
    iso_path = str(Path(iso_path).resolve())
    handle = _handles.pop(iso_path, None)
    if handle:
        virtdisk.DetachVirtualDisk(handle, DETACH_VIRTUAL_DISK_FLAG_NONE, 0)
        kernel32.CloseHandle(handle)
        log(f"ISO dismounted: {iso_path}", "INFO")
