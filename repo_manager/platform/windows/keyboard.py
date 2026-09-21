from __future__ import annotations

import ctypes
import os
import time

VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


def paste_clipboard_with_ctrl_v(delay: float = 0.12) -> None:
    """Paste the current Windows clipboard into the already-focused control."""
    if os.name != "nt":
        raise RuntimeError("Clipboard keyboard paste is only available on Windows.")
    user32 = ctypes.windll.user32
    time.sleep(max(0.0, float(delay)))
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


__all__ = ["paste_clipboard_with_ctrl_v"]
