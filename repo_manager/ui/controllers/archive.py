from __future__ import annotations

import faulthandler
import os
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from tkinter import filedialog, messagebox, simpledialog, ttk

from ...archive.facade import *
from ...browser.facade import *
from ...chat import ChatEvent, ChatState, ChatWatchManager
from ...commands.facade import *
from ...config import config_path, save_config
from ...domain import AppConfig, CommandSpec, Project, UrlGroup, UrlSpec
from ...platform.windows.desktop import open_folder, open_in_vscode
from ..constants import *
from ..dialogs.command import CommandDialog
from ..dialogs.url import UrlDialog
from ..dialogs.misc import MultiSelectDialog, SnapshotDiffDialog
from ..widgets import ScrollableFrame

class ArchiveController:
    @staticmethod
    def _last_extracted_summary(filename: str) -> str:
        """Return v/r metadata from an extracted ZIP name when present.

        Examples:
          ghost-talk-v1.0.0-r-321.zip -> v1.0.0 r321
          ghost-talk-r180.zip         -> r180
          ghost-talk-v2.0.0.zip       -> v2.0.0
        If neither token exists, preserve the complete ZIP filename.
        """
        name = Path(filename).name.strip()
        if not name:
            return "none"
        stem = name[:-4] if name.casefold().endswith(".zip") else name
        version = re.search(r"(?i)(?:^|[-_.])v[-_]?(\d+(?:\.\d+)*)", stem)
        revision = re.search(r"(?i)(?:^|[-_.])r[-_]?(\d+)(?=$|[-_.])", stem)
        parts: list[str] = []
        if version:
            parts.append(f"v{version.group(1)}")
        if revision:
            parts.append(f"r{revision.group(1)}")
        return " ".join(parts) if parts else name

    @classmethod
    def _last_extracted_label(cls, filename: str) -> str:
        name = Path(filename).name.strip()
        if not name:
            return "Last extracted: none"
        summary = cls._last_extracted_summary(name)
        if summary == name:
            return f"Last extracted: {name}"
        return f"Last extracted: {summary} ({name})"

    @staticmethod
    def _zip_looks_complete(path: Path) -> bool:
        """Return True only when the ZIP central directory is currently readable.

        Firefox normally downloads to ``.part`` and renames at completion, but
        some download paths expose the final ``.zip`` name before all bytes are
        flushed. Opening the central directory distinguishes that from a ready
        archive without computing an expensive full CRC over the project.
        """
        try:
            with zipfile.ZipFile(path, "r") as archive:
                archive.infolist()
            return True
        except (OSError, zipfile.BadZipFile, EOFError):
            return False

    @staticmethod
    def _archive_matches_project_name(name: str, match_string: str) -> bool:
        needle = match_string.strip().casefold()
        return bool(needle and needle in name.casefold())

    def _download_archive_status(self, project: Project) -> str:
        try:
            folder = expanded(project.downloads_dir)
        except Exception:
            return ""
        if not folder.is_dir():
            return ""

        try:
            entries = [entry for entry in folder.iterdir() if entry.is_file()]
        except OSError:
            return ""

        # Partial browser downloads win over any older ready ZIP. Include the
        # common Firefox/Chromium suffixes while requiring both the project's
        # match string and a ZIP-looking filename.
        partials: list[Path] = []
        for entry in entries:
            lower = entry.name.casefold()
            if not self._archive_matches_project_name(entry.name, project.match_string):
                continue
            if ".zip" not in lower:
                continue
            if lower.endswith((".part", ".crdownload", ".tmp", ".download")):
                try:
                    if project.last_extracted_zip_mtime_ns and entry.stat().st_mtime_ns <= project.last_extracted_zip_mtime_ns:
                        continue
                except OSError:
                    pass
                partials.append(entry)
        if partials:
            return "Downloading"

        zips = [
            entry
            for entry in entries
            if entry.suffix.casefold() == ".zip"
            and self._archive_matches_project_name(entry.name, project.match_string)
        ]
        if not zips:
            return ""
        try:
            zips.sort(key=lambda item: (item.stat().st_mtime_ns, item.name.casefold()), reverse=True)
            newest = zips[0]
            stat = newest.stat()
        except OSError:
            return "Downloading"

        # A final-name ZIP that does not yet have a readable central directory
        # is still being written and should not be offered as ready.
        if not self._zip_looks_complete(newest):
            return "Downloading"

        if newest.name == project.last_extracted_zip:
            # Schema v6 records the exact extracted file signature so a fresh
            # download that reuses the same filename is still detected. Older
            # configs have zero signatures and retain the historical name-only
            # behavior until the next successful extraction.
            if project.last_extracted_zip_size and project.last_extracted_zip_mtime_ns:
                if (
                    int(stat.st_size) == int(project.last_extracted_zip_size)
                    and int(stat.st_mtime_ns) == int(project.last_extracted_zip_mtime_ns)
                ):
                    return ""
            else:
                return ""
        return f"Ready to extract: {newest.name}"

    def _refresh_archive_status_cache(self) -> bool:
        changed = False
        valid_ids = {project.project_id for project in self.config_data.projects}
        for stale_id in [key for key in self._archive_status_by_project if key not in valid_ids]:
            self._archive_status_by_project.pop(stale_id, None)
            changed = True
        for project in self.config_data.projects:
            value = self._download_archive_status(project)
            if self._archive_status_by_project.get(project.project_id) != value:
                self._archive_status_by_project[project.project_id] = value
                changed = True
        return changed

    def _update_download_status_label(self):
        if not hasattr(self, "download_status_var"):
            return
        project = self._current_project()
        value = self._download_archive_status(project) if project else ""
        if project is not None:
            self._archive_status_by_project[project.project_id] = value
        if self.download_status_var.get() != value:
            self.download_status_var.set(value)

    def _poll_download_status(self):
        if self._closing:
            return
        try:
            archive_changed = self._refresh_archive_status_cache()
            self._update_download_status_label()
            self._runtime_housekeeping()
            if archive_changed:
                self._update_project_status_cells()
            self._maybe_auto_extract_ready_project()
        except Exception as exc:
            # Folder polling/housekeeping must never terminate the Tk event loop.
            self._log(f"Archive status poll warning: {exc}")
        try:
            self.after(DOWNLOAD_STATUS_POLL_MS, self._poll_download_status)
        except tk.TclError:
            pass

    def _ready_archive_signature(self, project: Project) -> tuple[str, int, int] | None:
        status = self._archive_status_by_project.get(project.project_id, "")
        prefix = "Ready to extract: "
        if not status.startswith(prefix):
            self._auto_extract_attempted.pop(project.project_id, None)
            return None
        name = status[len(prefix):].strip()
        if not name:
            return None
        try:
            path = expanded(project.downloads_dir) / name
            stat = path.stat()
            return (name, int(stat.st_size), int(stat.st_mtime_ns))
        except OSError:
            return None

    def _maybe_auto_extract_ready_project(self) -> None:
        """Start at most one ready auto-extraction at a time.

        Automatic extraction intentionally bypasses the manual confirmation
        dialog; enabling this setting is itself the user's standing consent. A
        failed attempt is not retried until the ready archive's identity changes.
        """
        if self._closing or self._worker_count or self._extracting_project_ids:
            return
        for project in self.config_data.projects:
            if not project.auto_extract_when_ready:
                continue
            signature = self._ready_archive_signature(project)
            if signature is None:
                continue
            if self._auto_extract_attempted.get(project.project_id) == signature:
                continue
            self._auto_extract_attempted[project.project_id] = signature
            self._log(f"Auto extract ready: {project.name} — {signature[0]}")
            self._extract_project(project, auto_triggered=True)
            return

    def _extract_newest(self):
        project = self._validate_project_settings()
        if not project:
            return
        self._extract_project(project, auto_triggered=False)

    def _extract_project(self, project: Project, *, auto_triggered: bool) -> None:
        if project.project_id in self._extracting_project_ids:
            return
        try:
            source = expanded(project.downloads_dir)
            newest, older = newest_matching_zip(source, project.match_string)
            destination = expanded(project.extract_parent) / project.match_string
        except Exception as exc:
            if auto_triggered:
                self._log(f"Auto extract could not start for {project.name}: {exc}")
            else:
                self._show_error("Cannot extract", exc)
            return

        preserve_note = "The existing .git folder will be preserved." if project.preserve_git_on_extract else "The entire destination will be emptied, including .git."
        older_note = f"\n\n{len(older)} older matching ZIP(s) will be deleted after extraction succeeds." if project.delete_older_zips else ""
        if not auto_triggered:
            if project.confirm_extract_newest and not messagebox.askyesno(
                "Extract newest ZIP",
                f"Newest archive:\n{newest}\n\nDestination:\n{destination}\n\n{preserve_note}{older_note}",
                parent=self,
            ):
                return

        runs_to_stop = self._project_command_runs(project) if project.auto_stop_commands_before_extract else []
        if project.auto_stop_commands_before_extract:
            # Prevent an old ordered auto-run callback from launching another
            # command while this extraction is stopping existing processes.
            self._auto_run_jobs.pop(project.project_id, None)

        def work():
            try:
                self._thread_log(f"=== Extract {project.name} ===")
                if runs_to_stop:
                    self._thread_log(
                        f"Closing {len(runs_to_stop)} managed command window(s) before extraction..."
                    )
                    for run in runs_to_stop:
                        stop_windows_terminal_and_wait(run)
                        self._thread_log(f"Closed managed command PID {run.pid} before extraction.")
                clear_directory(destination, project.preserve_git_on_extract, self._thread_log)
                extract_zip_safely(
                    newest,
                    destination,
                    self._thread_log,
                    preserve_existing_git=project.preserve_git_on_extract,
                )
                if project.delete_older_zips and older:
                    delete_files(older, self._thread_log)
                return destination
            finally:
                # Queue the state transition back to Tk's main thread on both
                # success and failure. Do not mutate Tk widgets from the worker.
                self._ui_queue.put(("extract_finished", project.project_id, None))

        def done(destination_path: Path):
            project.last_extracted_zip = newest.name
            try:
                archive_stat = newest.stat()
                project.last_extracted_zip_size = int(archive_stat.st_size)
                project.last_extracted_zip_mtime_ns = int(archive_stat.st_mtime_ns)
            except OSError:
                project.last_extracted_zip_size = 0
                project.last_extracted_zip_mtime_ns = 0
            self._archive_status_by_project[project.project_id] = ""
            current = self._current_project()
            if current is not None and current.project_id == project.project_id:
                self.last_extracted_var.set(self._last_extracted_label(newest.name))
                self._update_download_status_label()
            self._update_project_status_cells()
            self._save_config()
            self._set_status(f"Extracted {newest.name}")
            self._check_snapshot_after_extract(project, destination_path)
            auto_commands = [c for c in project.commands if c.auto_run]
            if auto_commands:
                self._log(f"Starting {len(auto_commands)} auto-run command(s)…")
                self._run_auto_commands(project, auto_commands, destination_path)

        self._extracting_project_ids.add(project.project_id)
        self._update_project_status_cells()
        self._run_worker("Extracting…", work, done)

    def _clear_snapshot(self) -> None:
        project = self._current_project()
        if project is None:
            return
        if not project.snapshot:
            self._set_status(f"Snapshot already clear for {project.name}")
            return
        if not messagebox.askyesno(
            "Clear snapshot",
            f"Clear the saved project-structure snapshot for {project.name!r}?",
            parent=self,
        ):
            return
        project.snapshot = []
        if hasattr(self, "snapshot_status_var"):
            self.snapshot_status_var.set("Snapshot: not set")
        self._save_config()
        self._refresh_snapshot_action_icon()
        self._set_status(f"Cleared snapshot for {project.name}")
        self._log(f"Snapshot cleared: {project.name}")

    def _check_snapshot_after_extract(self, project: Project, folder: Path):
        if not project.snapshot:
            return
        try:
            current = iter_snapshot_entries(folder, project.snapshot_excluded_dirs)
            diff = compare_snapshot(project.snapshot, current, project.snapshot_excluded_dirs)
        except Exception as exc:
            self._show_error("Snapshot check failed", exc)
            return
        if not diff.changed:
            self._log("Snapshot check: no added or missing files/folders.")
            return
        dialog = SnapshotDiffDialog(self, diff)
        self._log(f"Snapshot differences: {len(diff.added)} added, {len(diff.missing)} missing")
        if dialog.result == "accept":
            project.snapshot = current
            selected = self._current_project()
            if selected is not None and selected.project_id == project.project_id:
                self.snapshot_status_var.set(f"Snapshot: {len(project.snapshot):,} entries")
            self._save_config()
            self._refresh_snapshot_action_icon()
            self._log("Snapshot updated to current project structure.")

    def _replace_repo(self):
        project = self._validate_project_settings()
        if not project:
            return
        try:
            source = project.working_dir.resolve()
            repo_root = expanded(project.repo_root)
            matches = find_matching_repo_dirs(repo_root, project.match_string, project.excluded_dirs)
        except Exception as exc:
            self._show_error("Cannot find repo", exc)
            return
        if not source.is_dir():
            messagebox.showerror("Current project missing", f"Extracted/current project folder does not exist:\n{source}", parent=self)
            return
        if not matches:
            messagebox.showinfo("No matching repo", f'No folder named "{project.match_string}" was found under:\n{repo_root}', parent=self)
            return

        dialog = MultiSelectDialog(
            self,
            "Replace Repo",
            "Select one or more matching repository folders to replace. Build/output folders and source .git data are never copied.",
            [str(p) for p in matches],
        )
        if not dialog.result:
            return
        selected = [matches[i] for i in dialog.result]
        preserve_note = ".git will be preserved in each destination." if project.preserve_git_on_replace else ".git will be removed from each destination."
        if not messagebox.askyesno(
            "Confirm repo replacement",
            f"Replace contents of {len(selected)} folder(s) with files from:\n{source}\n\n{preserve_note}\n\nExcluded build folders:\n" + ", ".join(project.excluded_dirs),
            parent=self,
        ):
            return

        def work():
            self._thread_log(f"=== Replace Repo: {project.name} ===")
            for target in selected:
                copy_project_contents(source, target, project.excluded_dirs, project.preserve_git_on_replace, self._thread_log)
            return selected

        def done(targets):
            self._set_status(f"Replaced {len(targets)} repo folder(s)")
            messagebox.showinfo("Replace Repo", f"Successfully replaced {len(targets)} repo folder(s).", parent=self)

        self._run_worker("Replacing repo…", work, done)

    def _zip_current(self):
        project = self._validate_project_settings()
        if not project:
            return
        source = project.working_dir.resolve()
        if not source.is_dir():
            messagebox.showerror("Project folder missing", f"Current project folder does not exist:\n{source}", parent=self)
            return
        initial_dir = expanded(project.downloads_dir)
        initial_dir.mkdir(parents=True, exist_ok=True)
        output = filedialog.asksaveasfilename(
            parent=self,
            title="Save project ZIP",
            initialdir=initial_dir,
            initialfile=default_zip_name(project.match_string),
            defaultextension=".zip",
            filetypes=[("ZIP archive", "*.zip")],
        )
        if not output:
            return
        output_path = Path(output)

        def work():
            self._thread_log(f"=== ZIP {project.name} ===")
            return zip_project(source, output_path, project.excluded_dirs, project.zip_exclude_git, self._thread_log)

        def done(path: Path):
            project.last_created_zip = str(path)
            self._save_config()
            self._set_status(f"Created {path.name}; ready to paste into ChatGPT")
            messagebox.showinfo(
                "ZIP created",
                f"Created:\n{path}\n\nUse 'Paste ZIP Project into ChatGPT' to attach it to a managed ChatGPT window.",
                parent=self,
            )

        self._run_worker("Creating ZIP…", work, done)

    def _take_snapshot(self):
        project = self._validate_project_settings()
        if not project:
            return
        folder = project.working_dir.resolve()
        if not folder.is_dir():
            messagebox.showerror("Project folder missing", f"Current project folder does not exist:\n{folder}", parent=self)
            return
        try:
            current = iter_snapshot_entries(folder, project.snapshot_excluded_dirs)
        except Exception as exc:
            self._show_error("Snapshot failed", exc)
            return
        if project.snapshot:
            diff = compare_snapshot(project.snapshot, current, project.snapshot_excluded_dirs)
            details = f"\n\nCompared with saved snapshot: {len(diff.added)} added, {len(diff.missing)} missing."
            prompt = f"Replace the existing snapshot with the current {len(current):,} files/folders?{details}"
        else:
            prompt = f"Save a snapshot containing {len(current):,} files/folders?"
        if messagebox.askyesno("Take snapshot", prompt, parent=self):
            project.snapshot = current
            self.snapshot_status_var.set(f"Snapshot: {len(current):,} entries")
            self._save_config()
            self._refresh_snapshot_action_icon()
            self._log(f"Snapshot saved with {len(current)} entries.")

    def _open_selected_folder(self):
        project = self._validate_project_settings()
        if not project:
            return
        try:
            open_folder(project.working_dir.resolve())
        except Exception as exc:
            self._show_error("Open folder failed", exc)

    def _open_selected_vscode(self):
        project = self._validate_project_settings()
        if not project:
            return
        try:
            open_in_vscode(project.working_dir.resolve())
        except Exception as exc:
            self._show_error("Open VS Code failed", exc)

