from __future__ import annotations
import os
import shutil
import subprocess
import time
from pathlib import Path
from .models import WindowsTerminalRun
from ..platform.windows.window_activation import activate_windows_hwnd as _activate_windows_hwnd

def _find_window_for_run(run: WindowsTerminalRun) -> int:
    """Resolve the top-level terminal HWND for one managed command run.

    Windows Terminal and some console hosts decorate the title set by the
    inner cmd.exe (for example ``[#] <title> [#]``).  FindWindowW requires an
    exact title and therefore misses those windows.  Enumerating top-level
    windows and matching the unique run token is reliable across classic
    conhost and Windows Terminal.  A previously resolved HWND is reused while
    it remains valid.
    """
    if os.name != "nt":
        return 0

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    if run.hwnd and user32.IsWindow(wintypes.HWND(run.hwnd)):
        return run.hwnd

    title_needle = run.title.casefold()
    token_needle = f"[{run.window_token}]".casefold()
    matches: list[tuple[int, int]] = []

    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc_type
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        # Primary resolver: the managed explicit conhost.exe owns this HWND.
        # This is the same process object Close ultimately controls, so Focus
        # and Close now share one identity instead of using unrelated lookup
        # strategies.
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == run.pid:
            matches.append((3, int(hwnd)))
            return True

        # Compatibility fallback for command windows started by older builds
        # before explicit conhost ownership was introduced.
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        window_title = buffer.value.casefold()
        if title_needle and title_needle in window_title:
            matches.append((2, int(hwnd)))
        elif token_needle and token_needle in window_title:
            matches.append((1, int(hwnd)))
        return True

    user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(enum_proc, 0)
    if not matches:
        run.hwnd = 0
        return 0

    matches.sort(reverse=True)
    run.hwnd = matches[0][1]
    return run.hwnd

def resolve_windows_terminal_window(run: WindowsTerminalRun, wait_seconds: float = 0.0) -> int:
    """Resolve and cache a managed terminal HWND, optionally waiting briefly.

    The GUI poller calls this without waiting so the handle is normally cached
    before the user presses Focus. Focus itself uses a short bounded wait as a
    launch-race fallback.
    """
    if os.name != "nt":
        return 0
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        hwnd = _find_window_for_run(run)
        if hwnd:
            return hwnd
        if time.monotonic() >= deadline:
            return 0
        time.sleep(0.05)


def _focus_windows_console_by_pid(run: WindowsTerminalRun) -> bool:
    """Focus the real console window attached to the managed cmd.exe PID.

    Close can always fall back to ``taskkill`` and therefore does not prove
    that we successfully resolved a top-level HWND.  Focus *does* need the
    HWND.  Rather than guessing from a decorated title or from the window
    owner's process (the visible console is normally owned by conhost, not
    cmd.exe), run a tiny hidden PowerShell helper that attaches to the exact
    cmd.exe console by PID and asks kernel32 for that console's HWND.

    This path is independent of the terminal caption and therefore continues
    to work when Windows adds prefixes/suffixes such as ``[#] ... [#]``.
    """
    if os.name != "nt":
        return False

    powershell = (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or str(
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
    )
    if not Path(powershell).exists() and not shutil.which(powershell):
        return False

    # Use an environment variable for the PID so the encoded helper script has
    # no user-controlled interpolation/quoting surface.
    script = r"""
$ErrorActionPreference = 'Stop'
$targetPid = [uint32]$env:PRM_COMMAND_PID

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class PrmConsoleFocus {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool FreeConsole();

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AttachConsole(uint dwProcessId);

    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();

    [DllImport("kernel32.dll")]
    public static extern uint GetCurrentThreadId();

    [DllImport("user32.dll")]
    public static extern bool IsWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool BringWindowToTop(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern IntPtr SetActiveWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr processId);

    [DllImport("user32.dll")]
    public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(
        IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint flags);

    [DllImport("user32.dll")]
    public static extern void SwitchToThisWindow(IntPtr hWnd, bool altTab);
}
'@

# CREATE_NO_WINDOW normally leaves this helper unattached.  FreeConsole first
# anyway so AttachConsole is deterministic even if Windows changes that detail.
[void][PrmConsoleFocus]::FreeConsole()
if (-not [PrmConsoleFocus]::AttachConsole($targetPid)) { exit 2 }

try {
    $hWnd = [PrmConsoleFocus]::GetConsoleWindow()
    if ($hWnd -eq [IntPtr]::Zero -or -not [PrmConsoleFocus]::IsWindow($hWnd)) { exit 3 }

    if ([PrmConsoleFocus]::IsIconic($hWnd)) {
        [void][PrmConsoleFocus]::ShowWindow($hWnd, 9) # SW_RESTORE
    } else {
        [void][PrmConsoleFocus]::ShowWindow($hWnd, 5) # SW_SHOW
    }

    $foreground = [PrmConsoleFocus]::GetForegroundWindow()
    $currentThread = [PrmConsoleFocus]::GetCurrentThreadId()
    $targetThread = [PrmConsoleFocus]::GetWindowThreadProcessId($hWnd, [IntPtr]::Zero)
    $foregroundThread = if ($foreground -ne [IntPtr]::Zero) {
        [PrmConsoleFocus]::GetWindowThreadProcessId($foreground, [IntPtr]::Zero)
    } else { 0 }

    $attachedTarget = $false
    $attachedForeground = $false
    try {
        if ($targetThread -ne 0 -and $targetThread -ne $currentThread) {
            $attachedTarget = [PrmConsoleFocus]::AttachThreadInput($currentThread, $targetThread, $true)
        }
        if ($foregroundThread -ne 0 -and $foregroundThread -ne $currentThread -and $foregroundThread -ne $targetThread) {
            $attachedForeground = [PrmConsoleFocus]::AttachThreadInput($currentThread, $foregroundThread, $true)
        }

        $HWND_TOPMOST = [IntPtr](-1)
        $HWND_NOTOPMOST = [IntPtr](-2)
        $flags = 0x0001 -bor 0x0002 -bor 0x0040 # NOSIZE|NOMOVE|SHOWWINDOW

        # Raise the exact console, but immediately remove TOPMOST so Focus does
        # not permanently pin command windows above other applications.
        [void][PrmConsoleFocus]::SetWindowPos($hWnd, $HWND_TOPMOST, 0, 0, 0, 0, $flags)
        [void][PrmConsoleFocus]::ShowWindow($hWnd, 9)
        [void][PrmConsoleFocus]::BringWindowToTop($hWnd)
        [void][PrmConsoleFocus]::SetForegroundWindow($hWnd)
        [void][PrmConsoleFocus]::SetActiveWindow($hWnd)
        [PrmConsoleFocus]::SwitchToThisWindow($hWnd, $true)
        [void][PrmConsoleFocus]::SetWindowPos($hWnd, $HWND_NOTOPMOST, 0, 0, 0, 0, $flags)
        [void][PrmConsoleFocus]::BringWindowToTop($hWnd)
        [void][PrmConsoleFocus]::SetForegroundWindow($hWnd)

        Start-Sleep -Milliseconds 40
        $focused = [PrmConsoleFocus]::GetForegroundWindow() -eq $hWnd
        Write-Output ("HWND=" + $hWnd.ToInt64())
        if ($focused) { exit 0 }

        # The topmost/not-topmost raise has already brought the requested
        # window visibly to the front.  Treat a valid HWND as success even if
        # Windows' foreground-lock policy declines keyboard focus for a moment.
        exit 0
    }
    finally {
        if ($attachedForeground) {
            [void][PrmConsoleFocus]::AttachThreadInput($currentThread, $foregroundThread, $false)
        }
        if ($attachedTarget) {
            [void][PrmConsoleFocus]::AttachThreadInput($currentThread, $targetThread, $false)
        }
    }
}
finally {
    [void][PrmConsoleFocus]::FreeConsole()
}
"""

    import base64

    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    env = os.environ.copy()
    env["PRM_COMMAND_PID"] = str(run.pid)
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except (OSError, subprocess.SubprocessError):
        return False

    if completed.returncode != 0:
        return False

    for line in completed.stdout.splitlines():
        if line.startswith("HWND="):
            try:
                run.hwnd = int(line.partition("=")[2].strip())
            except ValueError:
                pass
            break
    return True

def focus_windows_terminal(run: WindowsTerminalRun) -> None:
    if os.name != "nt":
        raise RuntimeError("Command-window focus is only available on Windows.")
    if not run.window_open:
        raise RuntimeError("That command window is no longer open.")

    # New managed windows are launched through explicit conhost.exe, and the
    # Popen PID therefore owns the visible top-level console window.  Resolve
    # that exact HWND and activate it.  Close uses the same run/process identity.
    hwnd = resolve_windows_terminal_window(run, wait_seconds=1.0)
    if hwnd and _activate_windows_hwnd(hwnd):
        return

    # Compatibility path for a terminal launched by an older build that was
    # already running when this version of the GUI started.
    if _focus_windows_console_by_pid(run):
        return

    raise RuntimeError(
        "Windows could not activate the managed console window. Start this command again with the current build "
        "so it is launched under the tracked Windows Console Host."
    )

def close_windows_terminal(run: WindowsTerminalRun) -> None:
    if os.name != "nt":
        raise RuntimeError("Command-window close is only available on Windows.")
    if not run.window_open:
        return

    hwnd = resolve_windows_terminal_window(run, wait_seconds=0.25)
    if hwnd:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        if user32.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0):  # WM_CLOSE
            return

    # If the terminal exists but its top-level HWND cannot be resolved, kill
    # the whole process tree as the final fallback.
    subprocess.run(
        ["taskkill", "/PID", str(run.pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )

def stop_windows_terminal_and_wait(run: WindowsTerminalRun, timeout_seconds: float = 5.0) -> None:
    """Close one managed command window and ensure its process tree is gone."""
    if os.name != "nt":
        raise RuntimeError("Command-window stop is only available on Windows.")
    if not run.window_open:
        return
    close_windows_terminal(run)
    try:
        run.process.wait(timeout=max(0.1, float(timeout_seconds)))
        return
    except subprocess.TimeoutExpired:
        pass
    subprocess.run(
        ["taskkill", "/PID", str(run.pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    try:
        run.process.wait(timeout=2.0)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Managed command PID {run.pid} did not stop before extraction.") from exc


def read_windows_terminal_output(run: WindowsTerminalRun) -> str:
    try:
        return run.output_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""

