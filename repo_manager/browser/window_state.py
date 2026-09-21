from __future__ import annotations
import ctypes
import os
from pathlib import Path
from .models import WindowsBrowserRun

def windows_hwnd_exists(hwnd: int) -> bool:
    if os.name != "nt" or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    return bool(user32.IsWindow(wintypes.HWND(hwnd)))

def _windows_hwnd_pid(hwnd: int) -> int:
    if os.name != "nt" or not hwnd:
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    return int(pid.value)

def get_windows_window_geometry(hwnd: int) -> tuple[int, int, int, int, bool] | None:
    """Return the normal/restored rectangle plus maximized state for *hwnd*."""
    if os.name != "nt" or not hwnd or not windows_hwnd_exists(int(hwnd)):
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.UINT),
            ("flags", wintypes.UINT),
            ("showCmd", wintypes.UINT),
            ("ptMinPosition", POINT),
            ("ptMaxPosition", POINT),
            ("rcNormalPosition", RECT),
        ]

    user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
    user32.GetWindowPlacement.restype = wintypes.BOOL
    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not user32.GetWindowPlacement(wintypes.HWND(int(hwnd)), ctypes.byref(placement)):
        return None
    rect = placement.rcNormalPosition
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None
    SW_SHOWMAXIMIZED = 3
    return (int(rect.left), int(rect.top), width, height, int(placement.showCmd) == SW_SHOWMAXIMIZED)

def set_windows_window_geometry(
    hwnd: int,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    maximized: bool = False,
) -> None:
    """Restore a previously recorded normal rectangle/maximized state."""
    if os.name != "nt" or not hwnd or not windows_hwnd_exists(int(hwnd)):
        return
    import ctypes
    from ctypes import wintypes

    width = max(320, int(width))
    height = max(240, int(height))
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    SW_RESTORE = 9
    SW_MAXIMIZE = 3
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    user32.ShowWindow(wintypes.HWND(int(hwnd)), SW_RESTORE)
    user32.SetWindowPos(
        wintypes.HWND(int(hwnd)), None, int(x), int(y), width, height, SWP_NOZORDER | SWP_NOACTIVATE
    )
    if maximized:
        user32.ShowWindow(wintypes.HWND(int(hwnd)), SW_MAXIMIZE)

def _windows_visible_top_level_windows() -> list[tuple[int, int, str]]:
    """Return visible top-level HWND, PID, caption tuples."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    rows: list[tuple[int, int, str]] = []
    enum_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_type
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rows.append((int(hwnd), int(pid.value), title))
        return True

    user32.EnumWindows.argtypes = [enum_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(callback, 0)
    return rows

def _windows_process_image(pid: int) -> str:
    if os.name != "nt" or not pid:
        return ""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value
    finally:
        kernel32.CloseHandle(handle)

def get_windows_foreground_hwnd() -> int:
    """Return the current foreground top-level HWND on Windows, or 0 elsewhere."""
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    return int(user32.GetForegroundWindow() or 0)

