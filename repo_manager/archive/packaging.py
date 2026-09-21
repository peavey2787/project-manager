from __future__ import annotations
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable
LogFn=Callable[[str],None]

def should_exclude(rel_parts: tuple[str, ...], excluded_dirs: set[str], exclude_git: bool) -> bool:
    for part in rel_parts:
        folded = part.casefold()
        if exclude_git and folded == ".git":
            return True
        if folded in excluded_dirs:
            return True
    return False

def zip_project(
    source: Path,
    output_zip: Path,
    excluded_dirs: Iterable[str],
    exclude_git: bool,
    log: LogFn | None = None,
) -> Path:
    if not source.is_dir():
        raise FileNotFoundError(f"Current project folder does not exist: {source}")
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    excluded = {name.casefold() for name in excluded_dirs}
    source_resolved = source.resolve()
    output_resolved = output_zip.resolve()

    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for current, dirs, files in os.walk(source):
            current_path = Path(current)
            rel_dir = current_path.relative_to(source)
            dirs[:] = [
                name for name in dirs
                if not should_exclude(rel_dir.parts + (name,), excluded, exclude_git)
            ]
            for name in files:
                src = current_path / name
                rel = src.relative_to(source)
                if should_exclude(rel.parts, excluded, exclude_git):
                    continue
                try:
                    if src.resolve() == output_resolved:
                        continue
                except OSError:
                    pass
                archive.write(src, arcname=rel.as_posix())
    if log:
        log(f"Created ZIP: {output_zip}")
    return output_zip

def default_zip_name(match_string: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{match_string}-{stamp}.zip"

