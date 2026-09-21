from __future__ import annotations

import os
import time
from pathlib import Path


def copy_file_to_windows_clipboard(path: Path) -> None:
    """Place one file on the Windows clipboard as CF_HDROP for attachment paste."""
    if os.name != "nt":
        raise OSError("File clipboard paste is available only on Windows.")
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File does not exist: {path}")

    import ctypes
    from ctypes import wintypes

    CF_HDROP = 15
    GMEM_MOVEABLE = 0x0002

    class DROPFILES(ctypes.Structure):
        _fields_ = [
            ("pFiles", wintypes.DWORD),
            ("pt", wintypes.POINT),
            ("fNC", wintypes.BOOL),
            ("fWide", wintypes.BOOL),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL

    names = (str(path) + "\0\0").encode("utf-16-le")
    header = DROPFILES()
    header.pFiles = ctypes.sizeof(DROPFILES)
    header.pt.x = 0
    header.pt.y = 0
    header.fNC = False
    header.fWide = True
    total = ctypes.sizeof(DROPFILES) + len(names)
    memory = kernel32.GlobalAlloc(GMEM_MOVEABLE, total)
    if not memory:
        raise OSError(ctypes.get_last_error(), "GlobalAlloc failed")

    transferred = False
    try:
        pointer = kernel32.GlobalLock(memory)
        if not pointer:
            raise OSError(ctypes.get_last_error(), "GlobalLock failed")
        try:
            ctypes.memmove(pointer, ctypes.byref(header), ctypes.sizeof(DROPFILES))
            ctypes.memmove(pointer + ctypes.sizeof(DROPFILES), names, len(names))
        finally:
            kernel32.GlobalUnlock(memory)

        opened = False
        for _attempt in range(10):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.05)
        if not opened:
            raise OSError(ctypes.get_last_error(), "OpenClipboard failed")
        try:
            if not user32.EmptyClipboard():
                raise OSError(ctypes.get_last_error(), "EmptyClipboard failed")
            if not user32.SetClipboardData(CF_HDROP, memory):
                raise OSError(ctypes.get_last_error(), "SetClipboardData(CF_HDROP) failed")
            transferred = True
        finally:
            user32.CloseClipboard()
    finally:
        if not transferred:
            kernel32.GlobalFree(memory)
