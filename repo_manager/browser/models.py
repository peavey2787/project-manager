from __future__ import annotations
import subprocess
from dataclasses import dataclass

@dataclass(frozen=True)
class WindowsBrowserSpec:
    """An installed browser that Project Repo Manager knows how to launch."""

    browser_id: str
    name: str
    executable: str
    icon: str

@dataclass
class WindowsBrowserRun:
    """One browser window opened and managed by Project Repo Manager."""

    process: subprocess.Popen | None
    label: str
    url: str
    executable: str
    browser_id: str = "browser"
    browser_name: str = "Browser"
    hwnd: int = 0
    owner_pid: int = 0
    discovered: bool = False
    window_title: str = ""

    @property
    def window_open(self) -> bool:
        from .window_state import windows_hwnd_exists
        return windows_hwnd_exists(self.hwnd)

