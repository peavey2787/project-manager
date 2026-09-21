from __future__ import annotations
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable
from ..platform.windows.environment import refreshed_windows_environment
from .models import WindowsTerminalRun
from .runner import _resolved_script_path
from .terminal_window import _find_window_for_run
LogFn=Callable[[str],None]

def _batch_file_path(path: Path) -> str:
    # Percent signs are expanded by cmd.exe even inside quotes when a .cmd file
    # is parsed, so double them when embedding absolute paths in our wrapper.
    return str(path).replace("%", "%%")

def _batch_text(value: str) -> str:
    """Escape percent expansion when placing user text in a generated .cmd."""
    return value.replace("%", "%%")

def _terminal_title(label: str, token: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in " ._-" else "-" for ch in label).strip()
    safe = safe[:80] or "Command"
    return f"Project Repo Manager - {safe} [{token[:8]}]"

def _windows_keep_open_command_line(comspec: str, wrapper_path: Path) -> str:
    """Build a cmd.exe /k line using cmd-native quoting, not C argv quoting."""
    return f'"{comspec}" /d /k ""{wrapper_path}""'

def _windows_inner_invocation(command: str) -> str:
    """Return one command line suitable for our generated inner .cmd file.

    A selected script path gets an explicit interpreter/call form.  Arbitrary
    shell commands remain supported and are executed by cmd.exe itself.
    """
    path = _resolved_script_path(command)
    if path is None:
        # Commands are user-authored shell text. CALL keeps batch-file commands
        # from replacing our runner process and lets us inspect ERRORLEVEL.
        return f"call {_batch_text(command.strip())}"

    suffix = path.suffix.casefold()
    escaped = _batch_file_path(path)
    if suffix in {".cmd", ".bat"}:
        return f'call "{escaped}"'
    if suffix == ".ps1":
        executable = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
        return f'"{_batch_text(executable)}" -NoProfile -ExecutionPolicy Bypass -File "{escaped}"'
    if suffix == ".py":
        return f'"{_batch_file_path(Path(sys.executable).resolve())}" "{escaped}"'
    if suffix == ".sh":
        executable = shutil.which("bash") or "bash"
        return f'"{_batch_text(executable)}" "{escaped}"'
    return f'"{escaped}"'

def run_windows_commands_in_terminal(
    commands: list[tuple[str, str]],
    cwd: Path,
    label: str = "Command",
) -> WindowsTerminalRun | None:
    """Launch commands in one managed, visible Windows terminal.

    The commands run sequentially and stop on the first non-zero exit code.
    Their stdout/stderr is displayed live in that terminal and copied into a
    per-run log so the GUI's Copy Output button can retrieve it.  The terminal
    deliberately remains open after completion for inspection.
    """
    if os.name != "nt":
        return None
    if not commands:
        raise ValueError("At least one command is required.")

    token = uuid.uuid4().hex
    run_dir = Path(tempfile.gettempdir()) / "project-repo-manager" / "command-runs" / token
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "output.txt"
    done_marker = run_dir / "finished.marker"
    exit_code_path = run_dir / "exit-code.txt"
    wrapper_path = run_dir / "run.cmd"
    inner_path = run_dir / "commands.cmd"
    tee_path = Path(__file__).with_name("terminal_tee.py").resolve()
    title = _terminal_title(label, token)

    output_path.write_text(
        f"===== {label} =====\n"
        f"Working directory: {cwd}\n"
        + "".join(f"Command: {command_label}: {command}\n" for command_label, command in commands)
        + "\n",
        encoding="utf-8",
    )

    # Put the actual command sequence in its own batch file.  This is important:
    # the outer wrapper can pipe the entire sequence through our tee helper while
    # the inner runner still owns ERRORLEVEL and can stop on the first failure.
    inner_lines = [
        "@echo off",
        "setlocal EnableExtensions DisableDelayedExpansion",
        f'cd /d "{_batch_file_path(cwd)}"',
        'set "PRM_RC=0"',
        "",
    ]
    for index, (command_label, command) in enumerate(commands, start=1):
        safe_label = _batch_text(command_label)
        fail_label = f"prm_failed_{index}"
        next_label = f"prm_next_{index}"
        inner_lines.extend(
            [
                "echo.",
                f"echo ===== {safe_label} =====",
                _windows_inner_invocation(command),
                'set "PRM_LAST_RC=%ERRORLEVEL%"',
                'if not "%PRM_LAST_RC%"=="0" goto :' + fail_label,
                f"goto :{next_label}",
                f":{fail_label}",
                'set "PRM_RC=%PRM_LAST_RC%"',
                "goto :prm_finish",
                f":{next_label}",
                "",
            ]
        )
    inner_lines.extend(
        [
            ":prm_finish",
            f'>"{_batch_file_path(exit_code_path)}" echo %PRM_RC%',
            "exit /b %PRM_RC%",
            "",
        ]
    )
    inner_path.write_text("\r\n".join(inner_lines), encoding="utf-8", newline="")

    wrapper = "\r\n".join(
        [
            "@echo off",
            "setlocal EnableExtensions DisableDelayedExpansion",
            f"title {title}",
            "echo.",
            f"echo ===== {title} =====",
            f'echo Working directory: "{_batch_file_path(cwd)}"',
            "echo.",
            (
                f'call "{_batch_file_path(inner_path)}" 2>&1 '
                f'| "{_batch_file_path(Path(sys.executable).resolve())}" '
                f'"{_batch_file_path(tee_path)}" "{_batch_file_path(output_path)}"'
            ),
            f'>"{_batch_file_path(done_marker)}" echo finished',
            f'>>"{_batch_file_path(output_path)}" echo.',
            f'>>"{_batch_file_path(output_path)}" echo ===== COMMAND RETURNED TO TERMINAL =====',
            "echo.",
            "echo ===== COMMAND RETURNED TO TERMINAL =====",
            "echo Exit code:",
            f'type "{_batch_file_path(exit_code_path)}" 2>nul',
            "echo.",
            "echo This window is managed by Project Repo Manager.",
            "echo Use Focus, Copy Output, Close, or Close All Running Commands there.",
            "echo.",
            "endlocal",
            "",
        ]
    )
    wrapper_path.write_text(wrapper, encoding="utf-8", newline="")

    launch_env = refreshed_windows_environment()
    comspec = launch_env.get("COMSPEC") or os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
    launch_line = _windows_keep_open_command_line(comspec, wrapper_path)

    # Force the inbox Windows Console Host for managed command windows.
    # Windows 11 can delegate an ordinary CREATE_NEW_CONSOLE launch to Windows
    # Terminal, where the visible HWND belongs to a different host process and
    # cannot be resolved reliably from cmd.exe.  Explicitly invoking conhost.exe
    # gives us one real host process whose PID owns the visible window, so Focus
    # and Close can target the exact same window without relying on its title.
    conhost = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "conhost.exe"
    if conhost.exists():
        host_launch_line = f'"{conhost}" {launch_line}'
        process = subprocess.Popen(
            host_launch_line,
            cwd=str(cwd),
            creationflags=0,
            shell=False,
            env=launch_env,
        )
    else:
        # Very old/unusual Windows installation fallback.
        process = subprocess.Popen(
            launch_line,
            cwd=str(cwd),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
            shell=False,
            env=launch_env,
        )
    run = WindowsTerminalRun(
        process=process,
        title=title,
        window_token=token[:8],
        output_path=output_path,
        done_marker=done_marker,
        wrapper_path=wrapper_path,
        inner_path=inner_path,
        exit_code_path=exit_code_path,
    )
    # The terminal window is created asynchronously. Resolve immediately when
    # possible, but keep lazy resolution for terminals (such as Windows
    # Terminal) that create/decorate their top-level window a little later.
    try:
        run.hwnd = _find_window_for_run(run)
    except Exception:
        run.hwnd = 0
    return run

def run_windows_batch_in_terminal(command: str, cwd: Path, label: str = "Command") -> WindowsTerminalRun | None:
    """Compatibility wrapper: manual Run Now always gets a Windows terminal."""
    return run_windows_commands_in_terminal([(label, command)], cwd, label)

