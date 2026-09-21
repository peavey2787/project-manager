from __future__ import annotations

import os
import threading
import time


def flash_windows_hwnd_border(
    hwnd: int,
    *,
    flashes: int = 4,
    interval: float = 0.14,
    thickness: int = 6,
) -> None:
    """Flash a non-focus-stealing border around the visible Windows frame."""
    if os.name != "nt" or not hwnd:
        return
    threading.Thread(
        target=_flash_windows_hwnd_border_worker,
        args=(int(hwnd), flashes, interval, thickness),
        name="window-selection-flash",
        daemon=True,
    ).start()


def _flash_windows_hwnd_border_worker(target: int, flashes: int, interval: float, thickness: int) -> None:
    """Draw using physical, visible-frame coordinates without activating the HWND."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        dwmapi = None

    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.PatBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.PatBlt.restype = wintypes.BOOL

    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

    set_thread_dpi = getattr(user32, "SetThreadDpiAwarenessContext", None)
    previous_dpi = None
    if set_thread_dpi is not None:
        set_thread_dpi.argtypes = [ctypes.c_void_p]
        set_thread_dpi.restype = ctypes.c_void_p
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == (HANDLE)-4.
        previous_dpi = set_thread_dpi(ctypes.c_void_p(-4))

    root = user32.GetAncestor(wintypes.HWND(target), 2)  # GA_ROOT
    root_hwnd = int(root) if root else target
    count = max(1, int(flashes))
    pause = max(0.04, float(interval))
    width = max(2, int(thickness))

    def visible_frame_rect() -> wintypes.RECT | None:
        if not user32.IsWindow(root_hwnd) or user32.IsIconic(root_hwnd):
            return None
        rect = wintypes.RECT()
        if dwmapi is not None:
            result = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(root_hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)  # DWMWA_EXTENDED_FRAME_BOUNDS
            )
            if result == 0 and rect.right > rect.left and rect.bottom > rect.top:
                return rect
        if user32.GetWindowRect(wintypes.HWND(root_hwnd), ctypes.byref(rect)):
            if rect.right > rect.left and rect.bottom > rect.top:
                return rect
        return None

    def invert(rect: wintypes.RECT) -> bool:
        w, h = rect.right - rect.left, rect.bottom - rect.top
        edge = min(width, max(1, w // 3), max(1, h // 3))
        dc = user32.GetDC(None)
        if not dc:
            return False
        try:
            patinvert = 0x005A0049  # PATINVERT
            gdi32.PatBlt(dc, rect.left, rect.top, w, edge, patinvert)
            gdi32.PatBlt(dc, rect.left, rect.bottom - edge, w, edge, patinvert)
            side_h = max(0, h - edge * 2)
            if side_h:
                gdi32.PatBlt(dc, rect.left, rect.top + edge, edge, side_h, patinvert)
                gdi32.PatBlt(dc, rect.right - edge, rect.top + edge, edge, side_h, patinvert)
            return True
        finally:
            user32.ReleaseDC(None, dc)

    try:
        for _ in range(count):
            rect = visible_frame_rect()
            if rect is None or not invert(rect):
                return
            time.sleep(pause)
            invert(rect)  # erase the exact pixels that were inverted above
            time.sleep(pause)
    finally:
        if set_thread_dpi is not None and previous_dpi:
            set_thread_dpi(previous_dpi)


__all__ = ["flash_windows_hwnd_border"]
