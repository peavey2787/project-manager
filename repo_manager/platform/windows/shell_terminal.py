from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .environment import refreshed_windows_environment


def _working_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {resolved}")
    return resolved


def open_cmd_terminal(path: Path) -> None:
    if os.name != "nt":
        raise OSError("Command Prompt is only available on Windows.")
    cwd = _working_directory(path)
    env = refreshed_windows_environment()
    comspec = env.get("COMSPEC") or os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
    subprocess.Popen(
        [comspec, "/d", "/k"],
        cwd=str(cwd),
        env=env,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
    )


def open_powershell_terminal(path: Path) -> None:
    if os.name != "nt":
        raise OSError("PowerShell terminal launch is only available on Windows.")
    cwd = _working_directory(path)
    env = refreshed_windows_environment()
    search_path = env.get("PATH", "")
    executable = (
        shutil.which("pwsh.exe", path=search_path)
        or shutil.which("pwsh", path=search_path)
        or shutil.which("powershell.exe", path=search_path)
        or shutil.which("powershell", path=search_path)
    )
    if not executable:
        fallback = Path(env.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if fallback.exists():
            executable = str(fallback)
    if not executable:
        raise FileNotFoundError("PowerShell was not found in the current Windows environment.")
    subprocess.Popen(
        [executable, "-NoExit"],
        cwd=str(cwd),
        env=env,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
    )
