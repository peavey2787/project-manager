from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Iterable

def _expand_windows_environment_value(value: str, env: dict[str, str]) -> str:
    """Expand %NAME% references using an explicit Windows environment mapping."""
    pattern = re.compile(r"%([^%]+)%")

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        for key, current in env.items():
            if key.casefold() == name.casefold():
                return current
        return match.group(0)

    previous = str(value or "")
    for _ in range(8):
        expanded = pattern.sub(repl, previous)
        if expanded == previous:
            return expanded
        previous = expanded
    return previous

def _dedupe_windows_path(parts: Iterable[str]) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in parts:
        value = str(raw or "").strip().strip('"')
        if not value:
            continue
        key = os.path.normcase(os.path.normpath(value))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ";".join(ordered)

def refreshed_windows_environment() -> dict[str, str]:
    """Return a child environment with the *current* registry PATH.

    A long-running GUI keeps the PATH it inherited when it started. Windows
    Settings/CMD can see a PATH entry added later while Project Repo Manager
    cannot. Read the machine + user Environment registry keys at command-launch
    time and merge them ahead of the process' original PATH so tools such as
    ``make.exe`` become immediately available without restarting PRM.
    """
    env = dict(os.environ)
    if os.name != "nt":
        return env

    try:
        import winreg
    except Exception:
        return env

    def read_value(root, subkey: str, name: str) -> str:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _kind = winreg.QueryValueEx(key, name)
                return str(value or "")
        except OSError:
            return ""

    machine_path = read_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        "Path",
    )
    user_path = read_value(winreg.HKEY_CURRENT_USER, r"Environment", "Path")

    # Registry PATH values often contain %SystemRoot%, %USERPROFILE%, etc.
    machine_path = _expand_windows_environment_value(machine_path, env)
    user_path = _expand_windows_environment_value(user_path, env)

    current_path = env.get("PATH") or env.get("Path") or ""
    merged = _dedupe_windows_path(
        [
            *machine_path.split(";"),
            *user_path.split(";"),
            *current_path.split(";"),
        ]
    )
    if merged:
        # Keep one canonical key. Environment variable names are
        # case-insensitive on Windows, but duplicate PATH/Path keys are not
        # useful when passed explicitly to CreateProcess.
        for key in [key for key in env if key.casefold() == "path"]:
            env.pop(key, None)
        env["PATH"] = merged
    return env

