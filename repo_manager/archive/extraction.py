from __future__ import annotations
import os
import shutil
import stat
import zipfile
from pathlib import Path
from typing import Callable
from .packaging import should_exclude
LogFn=Callable[[str],None]

def _make_writable(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    except OSError:
        pass

def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        _make_writable(path)
        path.unlink(missing_ok=True)
        return

    def onerror(func, name, _exc):
        target = Path(name)
        _make_writable(target)
        func(name)

    shutil.rmtree(path, onerror=onerror)

def clear_directory(folder: Path, preserve_git: bool, log: LogFn | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for child in folder.iterdir():
        if preserve_git and child.name == ".git":
            if log:
                log(f"Preserving {child}")
            continue
        _remove_path(child)

def _safe_member_destination(root: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError(f"Unsafe absolute ZIP member: {member_name}")
    destination = (root / normalized).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Unsafe ZIP path traversal member: {member_name}") from exc
    return destination

def _normalized_zip_parts(member_name: str) -> tuple[str, ...]:
    """Return safe POSIX-like ZIP path parts, rejecting traversal first."""
    normalized = member_name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return ()
    if normalized.startswith("/"):
        raise ValueError(f"Unsafe absolute ZIP member: {member_name}")
    parts = tuple(part for part in normalized.split("/") if part not in {"", "."})
    if any(part == ".." for part in parts):
        raise ValueError(f"Unsafe ZIP path traversal member: {member_name}")
    # Reject Windows drive/UNC-like names as archive roots too.
    if parts and (":" in parts[0] or parts[0].startswith("\\")):
        raise ValueError(f"Unsafe ZIP member: {member_name}")
    return parts

def _zip_wrapper_depth(members: list[zipfile.ZipInfo]) -> int:
    """Count recursively nested single-directory wrapper levels.

    Example:
      ghost-talk-r160-source/ghost-talk/Cargo.toml
      ghost-talk-r160-source/ghost-talk/crates/...

    has wrapper depth 2 and therefore extracts directly as Cargo.toml, crates/...
    into the configured destination.  We only peel a level when *every* real
    payload entry is below the same directory and there are no files at that
    level, so already-flat archives remain unchanged.
    """
    payload_parts: list[tuple[str, ...]] = []
    for member in members:
        parts = _normalized_zip_parts(member.filename)
        if not parts or member.is_dir():
            continue
        payload_parts.append(parts)
    if not payload_parts:
        return 0

    depth = 0
    while True:
        # A wrapper can only be removed when every payload entry still has at
        # least one component below it. A root-level file stops flattening.
        if any(len(parts) <= depth + 1 for parts in payload_parts):
            break
        names = {parts[depth].casefold() for parts in payload_parts}
        if len(names) != 1:
            break
        depth += 1
    return depth

def extract_zip_safely(
    zip_path: Path,
    destination: Path,
    log: LogFn | None = None,
    preserve_existing_git: bool = False,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        members = archive.infolist()

        # Validate every original archive member before applying any wrapper
        # stripping. A malicious ../ path must never become harmless merely
        # because an outer directory is removed.
        normalized_members: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        for member in members:
            parts = _normalized_zip_parts(member.filename)
            if parts:
                # Keep the existing canonical path containment check as a
                # second layer of traversal defense.
                _safe_member_destination(destination, "/".join(parts))
            normalized_members.append((member, parts))

        wrapper_depth = _zip_wrapper_depth(members)
        if log and wrapper_depth:
            wrappers: list[str] = []
            sample = next((parts for _m, parts in normalized_members if len(parts) > wrapper_depth), ())
            if sample:
                wrappers = list(sample[:wrapper_depth])
            detail = "/".join(wrappers) if wrappers else f"{wrapper_depth} level(s)"
            log(f"Flattening ZIP wrapper path: {detail}")

        for member, parts in normalized_members:
            if not parts or len(parts) <= wrapper_depth:
                continue
            relative_parts = parts[wrapper_depth:]
            if not relative_parts:
                continue
            if preserve_existing_git and relative_parts[0].casefold() == ".git":
                if log:
                    log(f"Skipped archive Git metadata while preserving destination .git: {member.filename}")
                continue

            relative_name = "/".join(relative_parts)
            target = _safe_member_destination(destination, relative_name)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            unix_mode = (member.external_attr >> 16) & 0o777
            if unix_mode:
                try:
                    target.chmod(unix_mode)
                except OSError:
                    pass
    if log:
        log(f"Extracted {zip_path.name} -> {destination}")

def delete_files(files: Iterable[Path], log: LogFn | None = None) -> list[Path]:
    deleted: list[Path] = []
    for path in files:
        try:
            path.unlink()
            deleted.append(path)
            if log:
                log(f"Deleted older ZIP: {path}")
        except FileNotFoundError:
            continue
    return deleted

def copy_project_contents(
    source: Path,
    destination: Path,
    excluded_dirs: Iterable[str],
    preserve_git: bool,
    log: LogFn | None = None,
) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"Current project folder does not exist: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    clear_directory(destination, preserve_git=preserve_git, log=log)
    excluded = {name.casefold() for name in excluded_dirs}

    for current, dirs, files in os.walk(source):
        current_path = Path(current)
        rel_dir = current_path.relative_to(source)
        parts = rel_dir.parts
        if should_exclude(parts, excluded, exclude_git=True):
            dirs[:] = []
            continue
        dirs[:] = [
            name for name in dirs
            if not should_exclude(parts + (name,), excluded, exclude_git=True)
        ]
        target_dir = destination / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            src_file = current_path / name
            rel_file = rel_dir / name
            if should_exclude(rel_file.parts, excluded, exclude_git=True):
                continue
            dst_file = destination / rel_file
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if src_file.is_symlink():
                try:
                    link_target = os.readlink(src_file)
                    if dst_file.exists() or dst_file.is_symlink():
                        _remove_path(dst_file)
                    os.symlink(link_target, dst_file)
                except (OSError, NotImplementedError):
                    shutil.copy2(src_file, dst_file, follow_symlinks=True)
            else:
                shutil.copy2(src_file, dst_file)
    if log:
        log(f"Replaced repository contents: {destination}")

