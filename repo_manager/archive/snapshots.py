from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SnapshotDiff:
    added: list[str]
    missing: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.missing)


def _normalized_exclusions(excluded_dirs: Iterable[str] | None) -> set[str]:
    return {str(name).strip().casefold() for name in (excluded_dirs or ()) if str(name).strip()}


def snapshot_entry_is_excluded(entry: str, excluded_dirs: Iterable[str] | None) -> bool:
    """Return True when any path component belongs to a build/output exclusion."""
    excluded = _normalized_exclusions(excluded_dirs)
    if not excluded:
        return False
    value = str(entry).replace('\\', '/').rstrip('/@')
    return any(part.casefold() in excluded for part in value.split('/') if part)


def filter_snapshot_entries(entries: Iterable[str], excluded_dirs: Iterable[str] | None) -> list[str]:
    return sorted(
        (str(entry) for entry in entries if not snapshot_entry_is_excluded(str(entry), excluded_dirs)),
        key=str.casefold,
    )


def iter_snapshot_entries(root: Path, excluded_dirs: Iterable[str] | None = None) -> list[str]:
    if not root.is_dir():
        raise FileNotFoundError(f"Project folder does not exist: {root}")
    excluded = _normalized_exclusions(excluded_dirs)
    entries: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name.casefold() not in excluded]
        dirs.sort(key=str.casefold)
        files.sort(key=str.casefold)
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        if rel_dir != Path('.'):
            entries.append(rel_dir.as_posix() + '/')
        for name in files:
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            entries.append(rel)
        for name in list(dirs):
            path = current_path / name
            if path.is_symlink():
                rel = path.relative_to(root).as_posix()
                entries.append(rel + '@')
                dirs.remove(name)
    entries.sort(key=str.casefold)
    return entries


def compare_snapshot(saved: list[str], current: list[str], excluded_dirs: Iterable[str] | None = None) -> SnapshotDiff:
    saved_set = set(filter_snapshot_entries(saved, excluded_dirs))
    current_set = set(filter_snapshot_entries(current, excluded_dirs))
    return SnapshotDiff(
        added=sorted(current_set - saved_set, key=str.casefold),
        missing=sorted(saved_set - current_set, key=str.casefold),
    )
