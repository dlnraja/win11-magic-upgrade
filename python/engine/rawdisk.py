"""512-byte raw sector IO. Windows raw devices use CreateFile; tests use files.

Writes to \\\\.\\ paths are limited to one sector at offset 0 so a caller cannot
wipe a disk through this helper.
"""
from __future__ import annotations

import os
import re


class RawDiskError(OSError):
    pass


def _raw_device(path: str) -> bool:
    return path.startswith("\\\\.\\") or path.startswith("//./")


def _physical_drive_number(path: str) -> int | None:
    m = re.search(r"PhysicalDrive(\d+)\s*$", path, re.I)
    if not m:
        return None
    return int(m.group(1))


def read_at(path: str, offset: int, size: int) -> bytes:
    if size <= 0:
        raise RawDiskError("empty read")
    if _raw_device(path):
        return _win_read(path, offset, size)
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read(size)
    if len(data) != size:
        raise RawDiskError(f"short read on {path}: {len(data)}/{size}")
    return data


def write_at(path: str, offset: int, data: bytes) -> None:
    if not data:
        raise RawDiskError("empty write")
    if _raw_device(path):
        if offset != 0 or len(data) != 512:
            raise RawDiskError("refusing non-sector write to a raw device")
        _win_write(path, offset, data)
        return
    with open(path, "r+b") as fh:
        fh.seek(offset)
        fh.write(data)
        fh.flush()


def read_disk_sector(disk_number: int) -> bytes | None:
    """Read sector 0 of a physical disk. None when the platform cannot."""
    if os.name != "nt" or disk_number is None or int(disk_number) < 0:
        return None
    try:
        return read_at(rf"\\.\PhysicalDrive{int(disk_number)}", 0, 512)
    except OSError:
        return None


def assert_sector_target(*, path: str, system_disk: int | None, target_disk: int | None) -> None:
    """Refuse a raw write that might land on the system disk."""
    if system_disk is None or int(system_disk) < 0:
        raise RawDiskError("system disk number unknown — refusing raw write")
    drive_n = _physical_drive_number(path)
    if drive_n is None:
        # Volume path (\\.\E:). Caller must already have proved the volume's disk.
        if target_disk is None or int(target_disk) < 0:
            raise RawDiskError("target disk unknown — refusing raw write")
        if int(target_disk) == int(system_disk):
            raise RawDiskError("refusing raw write on the system disk")
        return
    if target_disk is None or int(drive_n) != int(target_disk):
        raise RawDiskError("raw path does not match the confirmed target disk")
    if int(drive_n) == int(system_disk):
        raise RawDiskError("refusing raw write on the system disk")


def _win_read(path: str, offset: int, size: int) -> bytes:
    handle = _win_open(path, write=False)
    try:
        return _win_io(handle, offset, size, None)
    finally:
        _win_close(handle)


def _win_write(path: str, offset: int, data: bytes) -> None:
    handle = _win_open(path, write=True)
    try:
        _win_io(handle, offset, len(data), data)
    finally:
        _win_close(handle)


def _kernel32():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k32.SetFilePointerEx.restype = wintypes.BOOL
    k32.ReadFile.restype = wintypes.BOOL
    k32.WriteFile.restype = wintypes.BOOL
    k32.CloseHandle.restype = wintypes.BOOL
    return k32, wintypes


def _win_open(path: str, *, write: bool):
    import ctypes
    from ctypes import wintypes

    k32, _wt = _kernel32()
    generic_read = 0x80000000
    generic_write = 0x40000000
    access = generic_read | generic_write if write else generic_read
    share = 0x1 | 0x2  # FILE_SHARE_READ | FILE_SHARE_WRITE
    handle = k32.CreateFileW(
        path,
        access,
        share,
        None,
        3,  # OPEN_EXISTING
        0,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle is None or int(handle) == int(invalid or -1):
        err = k32.GetLastError()
        raise RawDiskError(f"CreateFile {path} failed ({err})")
    return handle


def _win_close(handle) -> None:
    k32, _wt = _kernel32()
    k32.CloseHandle(handle)


def _win_io(handle, offset: int, size: int, data: bytes | None) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _Large(ctypes.Structure):
        _fields_ = [("QuadPart", ctypes.c_longlong)]

    k32, _wt = _kernel32()
    moved = _Large()
    if not k32.SetFilePointerEx(handle, _Large(int(offset)), ctypes.byref(moved), 0):
        raise RawDiskError("SetFilePointerEx failed")
    buf = ctypes.create_string_buffer(size)
    if data is not None:
        buf.raw = data
    done = wintypes.DWORD(0)
    if data is None:
        ok = k32.ReadFile(handle, buf, size, ctypes.byref(done), None)
        if not ok or int(done.value) != size:
            raise RawDiskError("ReadFile failed")
        return buf.raw[:size]
    ok = k32.WriteFile(handle, buf, size, ctypes.byref(done), None)
    if not ok or int(done.value) != size:
        raise RawDiskError("WriteFile failed")
    return b""
