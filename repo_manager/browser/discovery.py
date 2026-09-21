from __future__ import annotations
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable
from .models import WindowsBrowserRun, WindowsBrowserSpec
from .window_state import _windows_hwnd_pid, _windows_process_image, _windows_visible_top_level_windows, windows_hwnd_exists


_KNOWN_BROWSER_NAMES = {
    "firefox.exe": ("firefox", "Firefox"),
    "waterfox.exe": ("waterfox", "Waterfox"),
    "librewolf.exe": ("librewolf", "LibreWolf"),
    "msedge.exe": ("edge", "Edge"),
    "chrome.exe": ("chrome", "Chrome"),
    "brave.exe": ("brave", "Brave"),
    "chromium.exe": ("chromium", "Chromium"),
    "chromium-browser.exe": ("chromium", "Chromium"),
    "vivaldi.exe": ("vivaldi", "Vivaldi"),
    "opera.exe": ("opera", "Opera"),
}


def _browser_identity(
    image: str, browsers: Iterable[WindowsBrowserSpec] | None
) -> tuple[str, str, str] | None:
    if not image:
        return None
    exe_name = Path(image).name.casefold()
    specs = list(browsers or ())
    spec = next(
        (
            item
            for item in specs
            if os.path.normcase(os.path.abspath(item.executable))
            == os.path.normcase(os.path.abspath(image))
        ),
        None,
    )
    if spec is None:
        spec = next((item for item in specs if Path(item.executable).name.casefold() == exe_name), None)
    if spec is not None:
        return spec.browser_id, spec.name, spec.executable
    fallback = _KNOWN_BROWSER_NAMES.get(exe_name)
    if fallback is None:
        return None
    return fallback[0], fallback[1], image


def inspect_windows_browser_window(
    hwnd: int,
    browsers: Iterable[WindowsBrowserSpec] | None = None,
) -> WindowsBrowserRun | None:
    """Return the passively readable active URL for an existing browser HWND.

    This fast path uses MSAA only and never activates the browser. Startup, URL
    edits, and explicit Discover URL additionally use the batched UIA fallback
    below so already-open/background windows are not missed just because their
    MSAA address-bar tree is dormant.
    """
    if os.name != "nt" or not hwnd or not windows_hwnd_exists(int(hwnd)):
        return None
    pid = _windows_hwnd_pid(int(hwnd))
    image = _windows_process_image(pid)
    identity = _browser_identity(image, browsers)
    if identity is None:
        return None
    browser_id, browser_name, executable = identity

    try:
        from ..platform.windows.msaa import read_browser_active_url_msaa

        url = read_browser_active_url_msaa(int(hwnd))
    except Exception:
        url = ""
    if not url:
        return None

    return WindowsBrowserRun(
        process=None,
        label="",
        url=url,
        executable=str(executable),
        browser_id=browser_id,
        browser_name=browser_name,
        hwnd=int(hwnd),
        owner_pid=pid,
        discovered=True,
    )


def recover_windows_browser_window(
    hwnd: int,
    owner_pid: int,
    *,
    label: str,
    url: str,
    browser_id: str = "",
    browsers: Iterable[WindowsBrowserSpec] | None = None,
) -> WindowsBrowserRun | None:
    """Reattach a browser HWND persisted by an earlier PRM process."""
    if os.name != "nt" or not hwnd or not windows_hwnd_exists(int(hwnd)):
        return None
    current_pid = _windows_hwnd_pid(int(hwnd))
    if not current_pid or (int(owner_pid or 0) and current_pid != int(owner_pid)):
        return None
    image = _windows_process_image(current_pid)
    if not image:
        return None
    exe_name = Path(image).name.casefold()
    specs = list(browsers or ())
    spec = next((item for item in specs if item.browser_id == browser_id), None) if browser_id else None
    if spec is not None and Path(spec.executable).name.casefold() != exe_name:
        return None
    identity = _browser_identity(image, specs)
    if identity is None:
        return None
    resolved_id, resolved_name, executable = identity
    return WindowsBrowserRun(
        process=None,
        label=label,
        url=url,
        executable=str(executable),
        browser_id=resolved_id,
        browser_name=resolved_name,
        hwnd=int(hwnd),
        owner_pid=current_pid,
        discovered=True,
    )


def enumerate_windows_browser_window_candidates(
    browsers: Iterable[WindowsBrowserSpec] | None = None,
) -> list[WindowsBrowserRun]:
    """Return every visible supported browser top-level window, URL or not."""
    if os.name != "nt":
        return []

    specs = list(browsers or ())
    results: list[WindowsBrowserRun] = []
    for hwnd, pid, title in _windows_visible_top_level_windows():
        image = _windows_process_image(pid)
        identity = _browser_identity(image, specs)
        if identity is None:
            continue
        browser_id, browser_name, executable = identity
        results.append(
            WindowsBrowserRun(
                process=None,
                label="",
                url="",
                executable=str(executable),
                browser_id=browser_id,
                browser_name=browser_name,
                hwnd=int(hwnd),
                owner_pid=int(pid),
                discovered=True,
                window_title=title,
            )
        )
    return results


def resolve_windows_browser_window_candidates(
    candidates: Iterable[WindowsBrowserRun],
    browsers: Iterable[WindowsBrowserSpec] | None = None,
    *,
    use_uia_fallback: bool = False,
) -> tuple[list[WindowsBrowserRun], list[WindowsBrowserRun]]:
    """Passively resolve candidate address bars, optionally with batched UIA."""
    candidates = list(candidates)
    resolved: list[WindowsBrowserRun] = []
    unresolved: list[WindowsBrowserRun] = []
    for candidate in candidates:
        try:
            observation = inspect_windows_browser_window(candidate.hwnd, browsers)
        except Exception:
            observation = None
        if observation is None:
            unresolved.append(candidate)
            continue
        observation.window_title = candidate.window_title
        resolved.append(observation)

    if use_uia_fallback and unresolved:
        try:
            from ..platform.windows.browser_uia import read_browser_active_urls_uia

            urls = read_browser_active_urls_uia(candidate.hwnd for candidate in unresolved)
        except Exception:
            urls = {}
        still_unresolved: list[WindowsBrowserRun] = []
        for candidate in unresolved:
            url = urls.get(candidate.hwnd, "")
            if not url:
                still_unresolved.append(candidate)
                continue
            candidate.url = url
            resolved.append(candidate)
        unresolved = still_unresolved

    return resolved, unresolved


def enumerate_windows_browser_windows(
    browsers: Iterable[WindowsBrowserSpec] | None = None,
    *,
    use_uia_fallback: bool = False,
) -> list[WindowsBrowserRun]:
    """Return visible supported browser windows whose active URL is readable."""
    candidates = enumerate_windows_browser_window_candidates(browsers)
    resolved, _unresolved = resolve_windows_browser_window_candidates(
        candidates, browsers, use_uia_fallback=use_uia_fallback
    )
    return resolved


def enumerate_windows_browser_discovery_rows(
    browsers: Iterable[WindowsBrowserSpec] | None = None,
) -> list[WindowsBrowserRun]:
    """Return all supported browser windows for the explicit Discover dialog.

    URL resolution is best-effort MSAA + batched UIA. Unreadable windows remain
    in the list with a blank URL so the user can still bind the correct HWND.
    """
    candidates = enumerate_windows_browser_window_candidates(browsers)
    resolved, _unresolved = resolve_windows_browser_window_candidates(
        candidates, browsers, use_uia_fallback=True
    )
    by_hwnd = {run.hwnd: run for run in resolved}
    return [by_hwnd.get(candidate.hwnd, candidate) for candidate in candidates]


def _split_windows_command_line(command: str) -> list[str]:
    """Split a Windows command line using the same parser CreateProcess clients use."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
            shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
            kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
            kernel32.LocalFree.restype = wintypes.HLOCAL
            argc = ctypes.c_int()
            argv = shell32.CommandLineToArgvW(command, ctypes.byref(argc))
            if argv:
                try:
                    return [argv[i] for i in range(argc.value)]
                finally:
                    kernel32.LocalFree(argv)
        except Exception:
            pass

    # Test/non-Windows fallback. Registry browser commands are simple enough
    # for this fallback; Windows itself uses CommandLineToArgvW above.
    import shlex

    return [part.strip('"') for part in shlex.split(command, posix=False)]

def _read_windows_app_path(exe_name: str) -> Path | None:
    """Resolve a browser executable through Windows App Paths when available."""
    if os.name != "nt":
        return None
    try:
        import winreg

        locations = (
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"),
        )
        for root, key_name in locations:
            try:
                with winreg.OpenKey(root, key_name) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                candidate = Path(os.path.expandvars(str(value).strip().strip('"')))
                if candidate.is_file():
                    return candidate.resolve()
            except OSError:
                continue
    except Exception:
        return None
    return None

def detect_windows_browsers() -> list[WindowsBrowserSpec]:
    """Return supported browsers installed on this Windows machine.

    Detection intentionally does not depend on the default browser.  This lets
    the URLs tab expose one explicit launch button per installed browser so the
    user always controls which profile/application receives a saved URL.
    """
    if os.name != "nt":
        return []

    env = {key: os.environ.get(key, "") for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")}
    definitions = [
        ("firefox", "Firefox", "firefox.exe", "🦊", [
            r"%PROGRAMFILES%\Mozilla Firefox\firefox.exe",
            r"%PROGRAMFILES(X86)%\Mozilla Firefox\firefox.exe",
            r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe",
        ]),
        ("edge", "Edge", "msedge.exe", "ⓔ", [
            r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
            r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
            r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
        ]),
        ("chrome", "Chrome", "chrome.exe", "ⓒ", [
            r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
            r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ]),
        ("brave", "Brave", "brave.exe", "🦁", [
            r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%PROGRAMFILES(X86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
        ]),
        ("vivaldi", "Vivaldi", "vivaldi.exe", "ⓥ", [
            r"%PROGRAMFILES%\Vivaldi\Application\vivaldi.exe",
            r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe",
        ]),
        ("opera", "Opera", "opera.exe", "ⓞ", [
            r"%LOCALAPPDATA%\Programs\Opera\opera.exe",
            r"%PROGRAMFILES%\Opera\opera.exe",
            r"%PROGRAMFILES(X86)%\Opera\opera.exe",
        ]),
        ("waterfox", "Waterfox", "waterfox.exe", "💧", [
            r"%PROGRAMFILES%\Waterfox\waterfox.exe",
            r"%PROGRAMFILES(X86)%\Waterfox\waterfox.exe",
            r"%LOCALAPPDATA%\Waterfox\waterfox.exe",
        ]),
        ("librewolf", "LibreWolf", "librewolf.exe", "🐺", [
            r"%PROGRAMFILES%\LibreWolf\librewolf.exe",
            r"%PROGRAMFILES(X86)%\LibreWolf\librewolf.exe",
            r"%LOCALAPPDATA%\Programs\LibreWolf\librewolf.exe",
        ]),
        ("chromium", "Chromium", "chromium.exe", "◉", [
            r"%PROGRAMFILES%\Chromium\Application\chromium.exe",
            r"%PROGRAMFILES(X86)%\Chromium\Application\chromium.exe",
            r"%LOCALAPPDATA%\Chromium\Application\chromium.exe",
        ]),
    ]

    found: list[WindowsBrowserSpec] = []
    seen_paths: set[str] = set()
    for browser_id, name, exe_name, icon, candidates in definitions:
        path: Path | None = _read_windows_app_path(exe_name)
        if path is None:
            which = shutil.which(exe_name)
            if which:
                candidate = Path(which)
                if candidate.is_file():
                    path = candidate.resolve()
        if path is None:
            for template in candidates:
                expanded_candidate = template
                for key, value in env.items():
                    expanded_candidate = expanded_candidate.replace(f"%{key}%", value)
                if not expanded_candidate or "%" in expanded_candidate:
                    continue
                candidate = Path(expanded_candidate)
                if candidate.is_file():
                    path = candidate.resolve()
                    break
        if path is None:
            continue
        canonical = os.path.normcase(str(path))
        if canonical in seen_paths:
            continue
        seen_paths.add(canonical)
        found.append(WindowsBrowserSpec(browser_id, name, str(path), icon))
    return found

def _windows_default_browser_registration() -> tuple[Path, list[str]] | None:
    """Return the registered HTTPS browser executable and its fixed arguments.

    Keeping the registered fixed arguments matters for browser/profile wrappers.
    Earlier builds extracted only the .exe path, which could launch a different
    browser context and make authenticated deep links fall back to a site root.
    URL placeholders and URL-dispatch-only switches are removed here because
    `_browser_launch_argv` adds the exact URL itself.
    """
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        ) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
            command, _ = winreg.QueryValueEx(key, None)
        command = os.path.expandvars(str(command).strip())
        parts = _split_windows_command_line(command)
        if parts:
            candidate = Path(parts[0])
            if candidate.exists():
                fixed: list[str] = []
                skip_next_placeholder = False
                placeholders = {"%1", "%l", "%L", "%u", "%U"}
                url_flags = {"-url", "--url", "--single-argument"}
                for arg in parts[1:]:
                    if arg in placeholders:
                        skip_next_placeholder = False
                        continue
                    folded = arg.casefold()
                    if folded in url_flags:
                        # This flag belongs to the registry URL dispatch template;
                        # the managed launcher supplies its own new-window flag.
                        skip_next_placeholder = True
                        continue
                    if skip_next_placeholder and arg in placeholders:
                        skip_next_placeholder = False
                        continue
                    # Firefox's -osint only accepts very specific URL-handler
                    # shapes and is incompatible with our --new-window shape.
                    if folded == "-osint":
                        continue
                    fixed.append(arg)
                return candidate.resolve(), fixed
    except (OSError, ValueError, TypeError):
        pass

    # Conservative fallback for common browsers if registry resolution fails.
    for name in ("msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve(), []
    return None

def _windows_default_browser_executable() -> Path | None:
    registration = _windows_default_browser_registration()
    return registration[0] if registration else None

