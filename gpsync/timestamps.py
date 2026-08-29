from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def set_file_times(path: Path, taken: datetime | None) -> None:
    """Set mtime (and Windows creation time) to the original capture date."""
    if taken is None:
        return
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=timezone.utc)
    timestamp = taken.timestamp()
    os.utime(path, (timestamp, timestamp))
    if os.name == "nt":
        _set_windows_ctime(path, timestamp)


def _set_windows_ctime(path: Path, timestamp: float) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    FILE_WRITE_ATTRIBUTES = 0x0100
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path),
        FILE_WRITE_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle in {0, wintypes.HANDLE(-1).value}:
        return
    try:
        windows_ts = int((timestamp + 11644473600) * 10_000_000)
        low = windows_ts & 0xFFFFFFFF
        high = windows_ts >> 32
        ctime = FILETIME(low, high)
        kernel32.SetFileTime(handle, ctypes.byref(ctime), None, ctypes.byref(ctime))
    finally:
        kernel32.CloseHandle(handle)
