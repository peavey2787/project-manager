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

class ProjectSidebarController:
    def _project_chat_state(self, project: Project) -> tuple[ChatState, bool]:
        watches = self._chat_watch_manager.watches_for_project(project.project_id)
        if not watches:
            return ChatState.IDLE, False
        now = time.monotonic()
        for state in (ChatState.ERROR, ChatState.IN_PROGRESS, ChatState.SUCCESS, ChatState.UNAVAILABLE):
            matching = [watch for watch in watches if watch.state == state]
            if matching:
                flash = state in {ChatState.ERROR, ChatState.SUCCESS} and any(not watch.acknowledged for watch in matching)
                if state == ChatState.IN_PROGRESS:
                    # Normal work is deliberately calm: steady yellow. Only a
                    # response that has remained generating for 28 minutes is
                    # treated as unusually long-running and begins flashing.
                    flash = any(
                        not watch.acknowledged
                        and watch.in_progress_since > 0.0
                        and now - watch.in_progress_since >= LONG_RUNNING_FLASH_SECONDS
                        for watch in matching
                    )
                return state, flash
        return ChatState.IDLE, False

    def _project_command_state(self, project: Project) -> str:
        if project.project_id in self._extracting_project_ids:
            return "extracting"
        if project.project_id in self.__dict__.get("_downloading_project_ids", set()):
            return "downloading"
        runs = self._project_command_runs(project)
        if not runs:
            return "idle"
        # Failure has precedence over "still running". A terminal deliberately
        # remains open after the command sequence returns, and a Rust/Cargo
        # diagnostic can be visible before the wrapper writes its done marker.
        if any(self._command_run_failed(run) for run in runs):
            return "error"
        if any(not run.command_finished for run in runs):
            return "running"
        if any(run.completion_succeeded for run in runs):
            return "success"
        return "idle"

    def _project_chat_indicator(self, project: Project) -> str:
        state, flash = self._project_chat_state(project)
        phase = self._flash_phase and flash
        if state == ChatState.IN_PROGRESS:
            return "WHITE · Running" if phase else "YELLOW · Running"
        if state == ChatState.SUCCESS:
            return "WHITE · Finished" if phase else "GREEN · Finished"
        if state == ChatState.ERROR:
            return "BLACK · Error" if phase else "RED · Error"
        if state == ChatState.UNAVAILABLE:
            return "BLACK · N/A"
        return "WHITE · Idle"

    def _project_command_indicator(self, project: Project) -> str:
        state = self._project_command_state(project)
        if state == "extracting":
            return "YELLOW · Extracting"
        if state == "downloading":
            return "YELLOW · Downloading"
        if state == "running":
            runs = self._project_command_runs(project, running_only=True)
            now = time.monotonic()
            overdue = any(
                run.pid not in self._acknowledged_command_pids
                and run.started_at > 0.0
                and now - run.started_at >= LONG_RUNNING_FLASH_SECONDS
                for run in runs
            )
            if overdue and self._flash_phase:
                return "WHITE · Running"
            return "YELLOW · Running"
        if state == "success":
            return "GREEN · Done"
        if state == "error":
            return "RED · Error"
        return "WHITE · Idle"

    @staticmethod
    def _status_cell_palette(color_name: str) -> tuple[str, str]:
        palettes = {
            "yellow": ("#FFD400", "#111111"),
            "white": ("#FFFFFF", "#111111"),
            "green": ("#22C55E", "#07130B"),
            "red": ("#E53935", "#FFFFFF"),
            "black": ("#111111", "#FFFFFF"),
            "idle": ("#D9D9D9", "#222222"),
        }
        return palettes[color_name]

    def _project_chat_visual(self, project: Project) -> tuple[str, str, str]:
        state, flash = self._project_chat_state(project)
        phase = self._flash_phase and flash
        if state == ChatState.IN_PROGRESS:
            color = "white" if phase else "yellow"
            text = "Running"
        elif state == ChatState.SUCCESS:
            color = "white" if phase else "green"
            text = "Finished"
        elif state == ChatState.ERROR:
            color = "black" if phase else "red"
            text = "Error"
        elif state == ChatState.UNAVAILABLE:
            color, text = "black", "N/A"
        else:
            color, text = "idle", "Idle"
        background, foreground = self._status_cell_palette(color)
        return text, background, foreground

    def _project_command_visual(self, project: Project) -> tuple[str, str, str]:
        state = self._project_command_state(project)
        if state == "extracting":
            color, text = "yellow", "Extracting"
        elif state == "downloading":
            color, text = "yellow", "Downloading"
        elif state == "running":
            runs = self._project_command_runs(project, running_only=True)
            now = time.monotonic()
            overdue = any(
                run.pid not in self._acknowledged_command_pids
                and run.started_at > 0.0
                and now - run.started_at >= LONG_RUNNING_FLASH_SECONDS
                for run in runs
            )
            color = "white" if overdue and self._flash_phase else "yellow"
            text = "Running"
        elif state == "success":
            color, text = "green", "Done"
        elif state == "error":
            failed_runs = [run for run in self._project_command_runs(project) if self._command_run_failed(run)]
            unacknowledged = any(run.pid not in self._acknowledged_command_pids for run in failed_runs)
            color = "black" if unacknowledged and self._flash_phase else "red"
            text = "Error"
        else:
            color, text = "idle", "Idle"
        background, foreground = self._status_cell_palette(color)
        return text, background, foreground

    def _acknowledge_project_attention(self, project: Project | None) -> None:
        """Stop current flashing for one project without muting future work."""
        if project is None:
            return
        for watch in self._chat_watch_manager.watches_for_project(project.project_id):
            self._chat_watch_manager.acknowledge(watch.hwnd)
        for run in self._project_command_runs(project):
            self._acknowledged_command_pids.add(run.pid)
        self._update_project_status_cells()

    def _acknowledge_clicked_project(self, event) -> None:
        # <<TreeviewSelect>> does not fire when the already-selected row is
        # clicked again, so acknowledge directly from the pointer's row.
        try:
            project_id = self.project_list.identify_row(event.y)
        except tk.TclError:
            project_id = ""
        if project_id:
            self._acknowledge_project_attention(self._project_by_id(project_id))

    def _on_notebook_click_acknowledge(self, event) -> None:
        # A click on the Project tab should acknowledge even when that tab is
        # already selected (there would be no <<NotebookTabChanged>> event).
        try:
            index = self.notebook.index(f"@{event.x},{event.y}")
            tab_id = self.notebook.tabs()[index]
        except (tk.TclError, IndexError):
            return
        if tab_id == str(self.overview_tab):
            self._acknowledge_project_attention(self._current_project())

    def _on_notebook_tab_changed(self, _event=None) -> None:
        try:
            selected = self.notebook.select()
        except tk.TclError:
            return
        if selected == str(self.commands_tab):
            self.after_idle(self._select_active_command_for_current_project)

    def _run_main_action(self, callback):
        self._acknowledge_project_attention(self._current_project())
        return callback()

    def _select_project_status_label(self, project_id: str):
        if self.project_list.exists(project_id):
            self.project_list.selection_set(project_id)
            self.project_list.focus(project_id)
            self.project_list.see(project_id)
            self._on_project_select()

    def _show_project_status_context(self, project_id: str, event):
        self._select_project_status_label(project_id)
        try:
            self.project_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.project_menu.grab_release()

    def _layout_project_status_labels(self):
        if not hasattr(self, "project_list") or not self.project_list.winfo_exists():
            return
        valid_keys: set[tuple[str, str]] = set()
        for project in self.config_data.projects:
            if not self.project_list.exists(project.project_id):
                continue
            visuals = {
                "chat": self._project_chat_visual(project),
                "commands": self._project_command_visual(project),
            }
            for column, (text, background, foreground) in visuals.items():
                key = (project.project_id, column)
                valid_keys.add(key)
                label = self._project_status_labels.get(key)
                if label is None or not label.winfo_exists():
                    label = tk.Label(
                        self.project_list,
                        borderwidth=0,
                        padx=2,
                        pady=0,
                        anchor="center",
                        font=("TkDefaultFont", 9, "bold"),
                    )
                    label.bind(
                        "<Button-1>",
                        lambda _e, project_id=project.project_id: self._select_project_status_label(project_id),
                    )
                    label.bind(
                        "<Button-3>",
                        lambda e, project_id=project.project_id: self._show_project_status_context(project_id, e),
                    )
                    self._project_status_labels[key] = label
                label.configure(text=text, background=background, foreground=foreground)
                bbox = self.project_list.bbox(project.project_id, column)
                if bbox:
                    x, y, width, height = bbox
                    label.place(x=x + 1, y=y + 1, width=max(1, width - 2), height=max(1, height - 2))
                    label.lift()
                else:
                    label.place_forget()
        for key, label in list(self._project_status_labels.items()):
            if key not in valid_keys:
                try:
                    label.destroy()
                except tk.TclError:
                    pass
                self._project_status_labels.pop(key, None)

    def _update_project_chat_timing_label(self) -> None:
        if not hasattr(self, "chat_worked_for_var"):
            return
        project = self._current_project()
        if project is None:
            self.chat_worked_for_var.set("ChatGPT worked for: —")
            return
        watches = [
            watch
            for watch in self._chat_watch_manager.watches_for_project(project.project_id)
            if watch.worked_for_text
        ]
        if not watches:
            self.chat_worked_for_var.set("ChatGPT worked for: —")
            return
        watch = max(watches, key=lambda item: (float(item.completed_at or 0.0), item.hwnd))
        suffix = f" — {watch.label}" if len(watches) > 1 else ""
        self.chat_worked_for_var.set(f"ChatGPT worked for: {watch.worked_for_text}{suffix}")

    def _update_project_status_cells(self):
        if not hasattr(self, "project_list"):
            return
        for project in self.config_data.projects:
            if not self.project_list.exists(project.project_id):
                continue
            self.project_list.item(
                project.project_id,
                values=(self._project_display_name(project), self._project_chat_indicator(project), self._project_command_indicator(project)),
            )
        self._layout_project_status_labels()
        self._update_project_chat_timing_label()

    def _flash_status_indicators(self):
        if self._closing or not self.winfo_exists():
            return
        self._flash_phase = not self._flash_phase
        self._update_project_status_cells()
        try:
            self.after(450, self._flash_status_indicators)
        except tk.TclError:
            pass

    def _refresh_projects(self, select: int | None = None):
        current_selection = self.project_list.selection()
        current_id = current_selection[0] if current_selection else None
        for item in self.project_list.get_children():
            self.project_list.delete(item)
        for project in self.config_data.projects:
            self.project_list.insert(
                "",
                "end",
                iid=project.project_id,
                values=(
                    self._project_display_name(project),
                    self._project_chat_indicator(project),
                    self._project_command_indicator(project),
                ),
            )
        if self.config_data.projects:
            index = self.config_data.selected_project if select is None else select
            if select is None and current_id:
                for candidate, project in enumerate(self.config_data.projects):
                    if project.project_id == current_id:
                        index = candidate
                        break
            index = max(0, min(index, len(self.config_data.projects) - 1))
            project_id = self.config_data.projects[index].project_id
            self.project_list.selection_set(project_id)
            self.project_list.focus(project_id)
            self.project_list.see(project_id)
            self.config_data.selected_project = index
            self._load_project_form()
            self._load_files_project(self.config_data.projects[index])
        else:
            self._clear_project_form()
        self._refresh_commands()
        self._refresh_urls()
        self._refresh_prompts()
        self._refresh_notes()
        self._update_download_chatgpt_button()
        self._update_paste_prompt_button()
        self._update_paste_note_button()
        self.after_idle(self._layout_project_status_labels)
        self._save_config()

    def _add_project(self):
        self._commit_form_to_project(self._loaded_project_index)
        self._save_config()
        name = simpledialog.askstring("New project", "Project name:", parent=self)
        if not name:
            return
        project = Project(name=name.strip(), match_string=name.strip())
        self.config_data.projects.append(project)
        self._refresh_projects(select=len(self.config_data.projects) - 1)

    def _remove_project(self):
        index = self._current_index()
        if index is None:
            return
        project = self.config_data.projects[index]
        if not messagebox.askyesno("Remove project", f'Remove "{project.name}" from this app?\n\nNo files on disk will be deleted.', parent=self):
            return
        for item in self._project_url_specs(project):
            for run in self._browser_runs.get(item.url_id, []):
                self._chat_watch_manager.remove(run.hwnd)
            self._browser_runs.pop(item.url_id, None)
        del self.config_data.projects[index]
        self._refresh_projects(select=max(0, index - 1))

    def _move_project(self, delta: int):
        index = self._current_index()
        if index is None:
            return
        target = index + delta
        if target < 0 or target >= len(self.config_data.projects):
            return
        self._commit_form_to_project()
        projects = self.config_data.projects
        projects[index], projects[target] = projects[target], projects[index]
        self._refresh_projects(select=target)

    def _project_drag_start(self, event):
        row_id = self.project_list.identify_row(event.y)
        if not row_id:
            self._project_drag_index = None
            return
        try:
            self._project_drag_index = list(self.project_list.get_children()).index(row_id)
        except ValueError:
            self._project_drag_index = None

    def _project_drag_motion(self, event):
        if self._project_drag_index is None:
            return
        row_id = self.project_list.identify_row(event.y)
        if not row_id:
            return
        children = list(self.project_list.get_children())
        try:
            new_index = children.index(row_id)
        except ValueError:
            return
        old_index = self._project_drag_index
        if new_index == old_index or not (0 <= new_index < len(self.config_data.projects)):
            return
        self._commit_form_to_project()
        item = self.config_data.projects.pop(old_index)
        self.config_data.projects.insert(new_index, item)
        self._project_drag_index = new_index
        self._refresh_projects(select=new_index)

    def _project_drag_end(self, _event):
        self._project_drag_index = None

    def _show_project_context(self, event):
        row_id = self.project_list.identify_row(event.y)
        if not row_id:
            try:
                self.project_empty_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.project_empty_menu.grab_release()
            return
        index = next((i for i, project in enumerate(self.config_data.projects) if project.project_id == row_id), None)
        if index is None:
            return
        self._commit_form_to_project(self._loaded_project_index)
        self._save_config()
        self.project_list.selection_set(row_id)
        self.project_list.focus(row_id)
        self._acknowledge_project_attention(self._project_at(index))
        self.config_data.selected_project = index
        self._load_project_form()
        self._load_files_project(self._project_at(index))
        self._refresh_commands()
        self._refresh_urls()
        self._refresh_prompts()
        self._refresh_notes()
        self._update_download_chatgpt_button()
        self._update_paste_prompt_button()
        self._update_paste_note_button()
        try:
            self.project_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.project_menu.grab_release()

    def _project_display_name(self, project: Project) -> str:
        status = self._archive_status_by_project.get(project.project_id)
        if status is None:
            status = self._download_archive_status(project)
            self._archive_status_by_project[project.project_id] = status
        return f"{project.name} *" if status.startswith("Ready to extract:") else project.name

