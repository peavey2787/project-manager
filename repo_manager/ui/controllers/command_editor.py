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

class CommandEditorController:
    def _refresh_commands(self, preferred_id: str | None = None):
        if not hasattr(self, "commands_tree"):
            return
        selected = self.commands_tree.selection()
        selected_id = preferred_id or (selected[0] if selected else None)
        for item in self.commands_tree.get_children():
            self.commands_tree.delete(item)
        project = self._current_project()
        if not project:
            self._update_command_action_buttons()
            return
        for command in project.commands:
            self.commands_tree.insert(
                "",
                "end",
                iid=command.command_id,
                values=(
                    command.label,
                    (
                        f"Order {command.auto_run_order} · "
                        + (
                            f"delay {command.completion_delay_seconds:g}s"
                            if command.completion_mode == "delay"
                            else "exit code"
                        )
                    )
                    if command.auto_run
                    else "No",
                    self._command_window_status(command.command_id),
                    command.command,
                ),
            )
        if selected_id and self.commands_tree.exists(selected_id):
            self.commands_tree.selection_set(selected_id)
            self.commands_tree.focus(selected_id)
            self.commands_tree.see(selected_id)
        self._update_command_action_buttons()

    def _selected_command_index(self) -> int | None:
        project = self._current_project()
        selection = self.commands_tree.selection()
        if not project or not selection:
            return None
        command_id = selection[0]
        for index, command in enumerate(project.commands):
            if command.command_id == command_id:
                return index
        return None

    def _selected_command(self) -> CommandSpec | None:
        project = self._current_project()
        index = self._selected_command_index()
        if not project or index is None:
            return None
        return project.commands[index]

    def _add_command(self):
        project = self._current_project()
        if not project:
            return
        # Add opens only the command editor. The file picker is opt-in via
        # Browse…, which starts at the selected project's current working folder.
        initial_dir = project.working_dir
        dialog = CommandDialog(self, initial_dir=initial_dir)
        if dialog.result:
            project.commands.append(dialog.result)
            self._save_config()
            self._refresh_commands(preferred_id=dialog.result.command_id)

    def _edit_command(self):
        project_index = self._current_index()
        project = self._project_at(project_index)
        index = self._selected_command_index()
        if project_index is None or not project or index is None:
            return

        original_id = project.commands[index].command_id
        dialog = CommandDialog(self, project.commands[index], initial_dir=project.working_dir)
        if not dialog.result:
            return

        # Update by stable command id rather than trusting a stale row index.
        # The dialog is modal, but using the id also makes edit resilient to
        # future list refresh/reorder behavior.  CommandDialog deliberately
        # preserves the id for an edit.
        edited = dialog.result
        edited.command_id = original_id
        target_project = self._project_at(project_index)
        if target_project is None:
            return
        for target_index, existing in enumerate(target_project.commands):
            if existing.command_id == original_id:
                target_project.commands[target_index] = edited
                break
        else:
            # Never make an edited command disappear if the view refreshed
            # unexpectedly while the dialog was open.
            target_project.commands.append(edited)

        self._save_config()
        if self._current_index() == project_index:
            self._refresh_commands(preferred_id=original_id)

    def _remove_command(self):
        project = self._current_project()
        index = self._selected_command_index()
        if not project or index is None:
            return
        del project.commands[index]
        self._save_config()
        self._refresh_commands()

    def _move_command(self, delta: int):
        project = self._current_project()
        index = self._selected_command_index()
        if not project or index is None:
            return
        target = index + delta
        if target < 0 or target >= len(project.commands):
            return
        project.commands[index], project.commands[target] = project.commands[target], project.commands[index]
        self._save_config()
        self._refresh_commands(preferred_id=project.commands[target].command_id)

