from __future__ import annotations
import os
import time

def activate_windows_hwnd(hwnd: int) -> bool:
    """Restore and bring a top-level window to the foreground.

    The thread-input attachment is a fallback for Windows' foreground-lock
    rules.  Because Focus is initiated by an explicit user click in this app,
    foreground activation is normally permitted; this makes the remaining
    edge cases reliable without installing pywin32.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetActiveWindow.argtypes = [wintypes.HWND]
    user32.SetActiveWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    target = wintypes.HWND(hwnd)
    if user32.IsIconic(target):
        user32.ShowWindow(target, 9)  # SW_RESTORE
    else:
        user32.ShowWindow(target, 5)  # SW_SHOW

    # First try the normal path. This succeeds in the common case because the
    # user just clicked Focus in our foreground application.
    user32.BringWindowToTop(target)
    if user32.SetForegroundWindow(target):
        user32.SetActiveWindow(target)
        return True

    # Foreground-lock fallback: temporarily join the current/foreground thread
    # input queues to the target thread, perform the activation, then detach.
    current_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(target, None)
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached: list[tuple[int, int]] = []
    try:
        for source_thread in (current_thread, foreground_thread):
            if source_thread and target_thread and source_thread != target_thread:
                if user32.AttachThreadInput(source_thread, target_thread, True):
                    attached.append((source_thread, target_thread))

        # Brief topmost/not-topmost toggle raises the exact terminal without
        # leaving it pinned above other applications.
        HWND_TOPMOST = wintypes.HWND(-1)
        HWND_NOTOPMOST = wintypes.HWND(-2)
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
        user32.SetWindowPos(target, HWND_TOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(target, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        user32.BringWindowToTop(target)
        activated = bool(user32.SetForegroundWindow(target))
        user32.SetActiveWindow(target)
        if activated or user32.GetForegroundWindow() == target:
            return True

        # Final Windows fallback. SwitchToThisWindow is present on supported
        # desktop Windows versions and is especially useful when Windows
        # Terminal/conhost owns the top-level window rather than cmd.exe.
        try:
            switch_to = user32.SwitchToThisWindow
            switch_to.argtypes = [wintypes.HWND, wintypes.BOOL]
            switch_to.restype = None
            switch_to(target, True)
            return user32.GetForegroundWindow() == target
        except AttributeError:
            return False
    finally:
        for source_thread, target_thread_id in reversed(attached):
            user32.AttachThreadInput(source_thread, target_thread_id, False)
