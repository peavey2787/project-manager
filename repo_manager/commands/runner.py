from __future__ import annotations
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable
LogFn=Callable[[str],None]

def _resolved_script_path(command: str) -> Path | None:
    expanded_command = os.path.expandvars(os.path.expanduser(command.strip()))
    path = Path(expanded_command.strip('"'))
    if path.exists() and path.is_file():
        return path.resolve()
    return None

def _windows_batch_command_line(path: Path, *, keep_open: bool = False) -> str:
    # Feed CreateProcess one complete command line instead of relying on
    # subprocess.list2cmdline().  cmd.exe treats (), &, ^ and similar
    # characters as syntax, so an otherwise valid path such as
    # C:\Downloads\project(1)\run.cmd must remain explicitly quoted.
    comspec = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
    mode = "/k" if keep_open else "/c"
    return f'"{comspec}" /d /s {mode} call "{path}"'

def command_argv(command: str) -> tuple[object, bool]:
    path = _resolved_script_path(command)
    if path is not None:
        suffix = path.suffix.casefold()
        if suffix in {".cmd", ".bat"}:
            if os.name != "nt":
                raise RuntimeError(".cmd/.bat files require Windows.")
            return _windows_batch_command_line(path), False
        if suffix == ".ps1":
            executable = shutil.which("pwsh") or shutil.which("powershell")
            if not executable:
                raise RuntimeError("PowerShell was not found.")
            return [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)], False
        if suffix == ".sh":
            return [shutil.which("bash") or "bash", str(path)], False
        if suffix == ".py":
            return [sys.executable, str(path)], False
        return [str(path)], False
    return os.path.expandvars(os.path.expanduser(command.strip())), True

def command_display(command: str) -> str:
    path = _resolved_script_path(command)
    if path is not None and path.suffix.casefold() in {".cmd", ".bat"} and os.name == "nt":
        return _windows_batch_command_line(path)
    argv, use_shell = command_argv(command)
    if isinstance(argv, str):
        return argv
    return subprocess.list2cmdline([str(value) for value in argv]) if os.name == "nt" else " ".join(map(str, argv))

def run_command_streaming(command: str, cwd: Path, emit: LogFn) -> int:
    argv, use_shell = command_argv(command)
    emit(f"Working directory: {cwd}")
    emit(f"Launching: {command_display(command)}")
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=use_shell,
        bufsize=1,
    )
    emit(f"Started process PID {process.pid}")
    assert process.stdout is not None
    for line in process.stdout:
        emit(line.rstrip("\r\n"))
    return process.wait()

