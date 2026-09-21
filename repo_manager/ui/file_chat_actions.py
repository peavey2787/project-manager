from __future__ import annotations

import tempfile
import tkinter as tk
from pathlib import Path

from ..archive.facade import default_zip_name, zip_project


class FileChatActionsController:
    def _show_files_context(self, event) -> None:
        item = self.files_tree.identify_row(event.y)
        if not item:
            return
        path = self._file_tree_paths.get(item)
        if path is None:
            return
        self.files_tree.selection_set(item)
        self.files_tree.focus(item)
        self._on_file_tree_select()
        menu = tk.Menu(self, tearoff=False)
        if path.is_file():
            menu.add_command(
                label="Paste into ChatGPT",
                command=lambda path=path: self._paste_file_into_chatgpt(
                    path,
                    action_title="Paste File into ChatGPT",
                    item_kind="file attachment",
                ),
            )
        elif path.is_dir():
            menu.add_command(
                label="ZIP and Paste into ChatGPT",
                command=lambda path=path: self._zip_folder_and_paste_into_chatgpt(path),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _zip_folder_and_paste_into_chatgpt(self, folder: Path) -> None:
        project = self._current_project()
        if project is None or not folder.is_dir():
            return
        output_dir = Path(tempfile.gettempdir()) / "project-repo-manager" / "chatgpt-zips" / project.project_id
        output_path = output_dir / default_zip_name(folder.name or "folder")

        def work():
            self._thread_log(f"=== ZIP folder for ChatGPT: {folder} ===")
            return zip_project(
                folder,
                output_path,
                project.excluded_dirs,
                project.zip_exclude_git,
                self._thread_log,
            )

        def done(path: Path):
            self._log(f"Created folder ZIP for ChatGPT: {path}")
            self._set_status(f"Created folder ZIP: {path}; attempting ChatGPT paste")
            self._paste_file_into_chatgpt(
                path,
                action_title="ZIP and Paste Folder into ChatGPT",
                item_kind="ZIP attachment",
            )

        self._run_worker("Creating folder ZIP for ChatGPT…", work, done)
