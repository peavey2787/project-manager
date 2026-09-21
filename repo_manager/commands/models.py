from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WindowsTerminalRun:
    process: subprocess.Popen
    title: str
    window_token: str
    output_path: Path
    done_marker: Path
    wrapper_path: Path
    inner_path: Path
    exit_code_path: Path
    hwnd: int = 0
    started_at: float = 0.0
    completion_mode: str = "exit_code"
    completion_delay_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.started_at <= 0.0:
            self.started_at = time.monotonic()
        self.completion_mode = "delay" if self.completion_mode == "delay" else "exit_code"
        self.completion_delay_seconds = max(0.1, float(self.completion_delay_seconds or 5.0))

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def window_open(self) -> bool:
        return self.process.poll() is None

    @property
    def delay_elapsed(self) -> bool:
        return time.monotonic() - self.started_at >= self.completion_delay_seconds

    @property
    def command_finished(self) -> bool:
        if self.completion_mode == "delay":
            return self.delay_elapsed
        return self.done_marker.exists()

    @property
    def exit_code(self) -> int | None:
        try:
            return int(self.exit_code_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError, OSError):
            return None

    @property
    def completion_succeeded(self) -> bool:
        if not self.command_finished:
            return False
        if self.completion_mode == "delay":
            code = self.exit_code
            return code is None or code == 0
        return self.exit_code == 0
