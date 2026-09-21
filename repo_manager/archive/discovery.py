from __future__ import annotations
import os
from pathlib import Path

def expanded(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()

def newest_matching_zip(folder: Path, match_string: str) -> tuple[Path, list[Path]]:
    folder = folder.resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"ZIP source folder does not exist: {folder}")
    needle = match_string.casefold().strip()
    if not needle:
        raise ValueError("Match string cannot be empty.")
    matches = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.casefold() == ".zip" and needle in p.name.casefold()
    ]
    if not matches:
        raise FileNotFoundError(f'No .zip in "{folder}" contains "{match_string}".')
    matches.sort(key=lambda p: (p.stat().st_mtime_ns, p.name.casefold()), reverse=True)
    return matches[0], matches[1:]

def find_matching_repo_dirs(root: Path, match_string: str, excluded_dirs: Iterable[str]) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"Repository root does not exist: {root}")
    target = match_string.casefold().strip()
    if not target:
        raise ValueError("Match string cannot be empty.")
    excluded = {name.casefold() for name in excluded_dirs}
    excluded.update({".git", ".hg", ".svn"})
    matches: list[Path] = []
    for current, dirs, _files in os.walk(root):
        current_path = Path(current)
        kept: list[str] = []
        for name in dirs:
            child = current_path / name
            if name.casefold() == target:
                matches.append(child)
                continue
            if name.casefold() in excluded:
                continue
            kept.append(name)
        dirs[:] = kept
    return sorted(matches, key=lambda p: str(p).casefold())

