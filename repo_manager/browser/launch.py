from __future__ import annotations
import os
import subprocess
import time
from pathlib import Path
from typing import Iterable
from .models import WindowsBrowserRun, WindowsBrowserSpec
from .discovery import detect_windows_browsers, _windows_default_browser_registration
from .window_state import _windows_hwnd_pid, _windows_process_image, _windows_visible_top_level_windows, get_windows_window_geometry, windows_hwnd_exists
from ..platform.windows.window_activation import activate_windows_hwnd as _activate_windows_hwnd

def _browser_launch_argv(executable: Path, url: str, fixed_args: Iterable[str] = ()) -> list[str]:
    """Build a browser argv without altering the supplied URL in any way."""
    # Keep the URL as one argv element. Do not parse, normalize, unquote, or
    # rebuild it; deep paths, queries and fragments must survive byte-for-byte
    # at the Python string level.
    exact_url = url
    prefix = [str(executable), *list(fixed_args)]
    name = executable.name.casefold()
    if name in {"firefox.exe", "waterfox.exe", "librewolf.exe"}:
        # Firefox uses the single-dash form.  --new-window is not the
        # documented Firefox switch and can be interpreted inconsistently.
        return [*prefix, "-new-window", exact_url]
    if name in {
        "chrome.exe",
        "msedge.exe",
        "brave.exe",
        "chromium.exe",
        "chromium-browser.exe",
        "vivaldi.exe",
    }:
        return [*prefix, "--new-window", exact_url]
    if name.startswith("opera"):
        return [*prefix, "--new-window", exact_url]
    return [*prefix, exact_url]

def _shell_open_windows_url(url: str) -> None:
    """Delegate an exact URL to Windows' registered HTTPS handler.

    ShellExecute receives the URL as its own LPCWSTR argument, not through
    cmd.exe and not through a reconstructed browser command line. Therefore
    path/query/fragment text is not re-tokenized or truncated by this app.
    """
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE
    result = int(shell32.ShellExecuteW(None, "open", url, None, None, 1))
    if result <= 32:
        raise OSError(f"Windows could not open the URL (ShellExecute code {result}).")




def launch_windows_browser(
    url: str,
    label: str = "Link",
    wait_seconds: float = 8.0,
    browser: WindowsBrowserSpec | None = None,
) -> WindowsBrowserRun:
    """Open *url* in a new window of an explicitly selected browser.

    When *browser* is omitted, the registered HTTPS browser is retained as a
    compatibility fallback.  The URLs UI supplies an explicit detected browser,
    avoiding ambiguity about which application/profile should be used.

    Firefox-family browsers use Firefox's own native ``-new-window`` remote
    handoff.  If that browser is already running, the launcher executable is
    taken from the actual running browser process (not merely the registry or
    install scan) and the complete saved URL is passed as one argv element.
    This avoids brittle synthetic keyboard input while keeping the request in
    the user's existing Firefox installation/profile whenever Firefox can
    service the remote request.
    """
    if os.name != "nt":
        raise RuntimeError("Managed browser windows are only available on Windows.")

    fixed_args: list[str] = []
    if browser is None:
        registration = _windows_default_browser_registration()
        if registration is None:
            raise FileNotFoundError("Could not resolve the default Windows web browser executable.")
        executable, fixed_args = registration
        browser_id = executable.stem.casefold()
        browser_name = executable.stem
    else:
        executable = Path(browser.executable).resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"Browser executable no longer exists: {executable}")
        browser_id = browser.browser_id
        browser_name = browser.name

    exe_name = executable.name.casefold()
    executable_norm = os.path.normcase(os.path.abspath(str(executable)))

    def process_matches_selected_browser(pid: int) -> bool:
        image = _windows_process_image(pid)
        if not image:
            return False
        image_norm = os.path.normcase(os.path.abspath(image))
        if image_norm == executable_norm:
            return True
        # Some launchers/updaters can expose an equivalent executable through a
        # path alias. Fall back to the image basename only when the selected
        # browser has a unique executable name among our supported families.
        return Path(image).name.casefold() == exe_name

    firefox_family = exe_name in {"firefox.exe", "waterfox.exe", "librewolf.exe"}
    known_new_window = (
        firefox_family
        or exe_name in {
            "chrome.exe",
            "msedge.exe",
            "brave.exe",
            "chromium.exe",
            "chromium-browser.exe",
            "vivaldi.exe",
        }
        or exe_name.startswith("opera")
    )

    before_rows = _windows_visible_top_level_windows()
    before_hwnds = {hwnd for hwnd, _pid, _title in before_rows}
    exact_url = url

    process: subprocess.Popen | None = None
    if firefox_family:
        # Do not synthesize Ctrl+N / Ctrl+L / text input. Windows may reject
        # SendInput across foreground/integrity boundaries, and it is not
        # necessary: Firefox already provides a native remote new-window
        # command. Prefer the executable backing the *running* Firefox window
        # so a side-by-side/alternate install cannot accidentally open a
        # different profile.
        existing_firefox: list[tuple[int, int, str]] = []
        for hwnd, pid, title in before_rows:
            if process_matches_selected_browser(pid) and title.strip():
                existing_firefox.append((hwnd, pid, title))

        launch_executable = executable
        if existing_firefox:
            foreground = 0
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.GetForegroundWindow.restype = wintypes.HWND
                foreground = int(user32.GetForegroundWindow())
            except Exception:
                pass
            source = next((row for row in existing_firefox if row[0] == foreground), existing_firefox[0])
            running_image = _windows_process_image(source[1])
            if running_image and Path(running_image).is_file():
                launch_executable = Path(running_image).resolve()

        # shell=False plus a list argv means the complete URL is delivered as
        # exactly one argument.  No cmd.exe quoting, no registry URL template,
        # and no keyboard injection are involved.
        process = subprocess.Popen([str(launch_executable), "-new-window", exact_url])
    elif known_new_window:
        process = subprocess.Popen(_browser_launch_argv(executable, exact_url, fixed_args))
    else:
        process = subprocess.Popen([str(executable), *fixed_args, exact_url])

    deadline = time.monotonic() + max(0.5, wait_seconds)
    best: tuple[int, int] | None = None
    while time.monotonic() < deadline:
        rows = _windows_visible_top_level_windows()
        browser_rows: list[tuple[int, int, str]] = []
        for hwnd, pid, title in rows:
            if process_matches_selected_browser(pid):
                browser_rows.append((hwnd, pid, title))

        new_rows = [row for row in browser_rows if row[0] not in before_hwnds]
        if new_rows:
            foreground = 0
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.GetForegroundWindow.restype = wintypes.HWND
                foreground = int(user32.GetForegroundWindow())
            except Exception:
                pass
            chosen = next((row for row in new_rows if row[0] == foreground), None)
            if chosen is None:
                new_rows.sort(key=lambda row: (bool(row[2].strip()), row[0]), reverse=True)
                chosen = new_rows[0]
            best = (chosen[0], chosen[1])
            break
        time.sleep(0.08)

    run = WindowsBrowserRun(
        process=process,
        label=label,
        url=exact_url,
        executable=str(executable),
        browser_id=browser_id,
        browser_name=browser_name,
        hwnd=best[0] if best else 0,
        owner_pid=best[1] if best else 0,
    )
    if not run.hwnd:
        raise RuntimeError(
            f'{browser_name} was asked to create a new browser window, but Project Repo Manager '
            "could not capture the new browser window handle."
        )

    return run

def focus_windows_browser(run: WindowsBrowserRun) -> None:
    if os.name != "nt":
        raise RuntimeError("Browser-window focus is only available on Windows.")
    if not windows_hwnd_exists(run.hwnd):
        raise RuntimeError("That managed browser window is no longer open.")
    if not _activate_windows_hwnd(run.hwnd):
        raise RuntimeError("Windows did not allow the browser window to be brought to the foreground.")



def close_windows_browser(run: WindowsBrowserRun) -> None:
    if os.name != "nt":
        raise RuntimeError("Browser-window close is only available on Windows.")
    if not windows_hwnd_exists(run.hwnd):
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    if not user32.PostMessageW(wintypes.HWND(run.hwnd), 0x0010, 0, 0):  # WM_CLOSE
        raise RuntimeError("Windows could not close the managed browser window.")

