from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .widgets import bind_tooltip


ASSET_DIR = Path(__file__).with_name("assets") / "main_actions"


class MainActionsController:
    """Compact grouped image actions for high-frequency project workflows."""

    def _load_main_action_icons(self) -> None:
        names = (
            "extract",
            "replace-repo",
            "zip-project",
            "open-explorer",
            "snapshot-save",
            "snapshot-update",
            "snapshot-clear",
            "open-chatgpt",
            "close-chatgpt",
            "focus-chatgpt",
            "download-chatgpt-files",
            "paste-zip-chatgpt",
            "paste-error-chatgpt",
            "paste-output-chatgpt",
            "paste-note-chatgpt",
            "focus-all-commands",
            "close-all-commands",
            "close-all-url-windows",
        )
        self._main_action_images: dict[str, tk.PhotoImage] = {}
        for name in names:
            self._main_action_images[name] = tk.PhotoImage(file=str(ASSET_DIR / f"{name}.png"))

    def _action_icon(self, parent, *, asset: str, tooltip: str, command, attr: str):
        image = self._main_action_images[asset]
        button = ttk.Button(
            parent,
            image=image,
            style="ImageAction.TButton",
            command=command,
            takefocus=True,
        )
        setattr(self, attr, button)
        bind_tooltip(button, tooltip)
        self._main_action_buttons.append(button)
        return button

    def _action_group(
        self,
        parent,
        title: str,
        specs: list[tuple[str, str, object, str]],
        *,
        columns: int = 4,
    ) -> None:
        group = ttk.Frame(parent)
        group.pack(fill="x", pady=(3, 4))
        ttk.Label(group, text=title, style="ActionGroup.TLabel").pack(anchor="w", pady=(0, 3))
        row = ttk.Frame(group)
        row.pack(fill="x")
        columns = max(1, min(columns, len(specs)))
        for index, (asset, tooltip, command, attr) in enumerate(specs):
            grid_row, grid_column = divmod(index, columns)
            self._action_icon(
                row,
                asset=asset,
                tooltip=tooltip,
                command=command,
                attr=attr,
            ).grid(
                row=grid_row,
                column=grid_column,
                sticky="",
                padx=(0 if grid_column == 0 else 3, 0),
                pady=(0 if grid_row == 0 else 3, 0),
            )

    def _build_picker_row(
        self,
        parent,
        *,
        label: str,
        attr: str,
        selected,
        style_popup,
        paste_command,
        paste_attr: str,
        tooltip: str,
    ) -> ttk.Combobox:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(4, 0))
        row.columnconfigure(1, weight=1)
        ttk.Label(row, text=label).grid(row=0, column=0, sticky="w", padx=(0, 6))
        picker = ttk.Combobox(
            row,
            state="readonly",
            style="Prompt.TCombobox",
            postcommand=style_popup,
        )
        setattr(self, attr, picker)
        picker.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        picker.bind("<<ComboboxSelected>>", selected, add=True)
        button = self._action_icon(
            row,
            asset="paste-note-chatgpt",
            tooltip=tooltip,
            command=lambda: self._run_main_action(paste_command),
            attr=paste_attr,
        )
        button.grid(row=0, column=2, sticky="ew")
        return picker

    def _build_main_actions(self, parent) -> None:
        actions = ttk.LabelFrame(parent, text="Main actions", padding=8)
        actions.pack(fill="x", pady=(10, 0))
        self._main_action_buttons: list[ttk.Button] = []
        self._load_main_action_icons()

        self._action_group(
            actions,
            "Project",
            [
                ("extract", "Extract Newest ZIP — extract the newest matching ZIP into this project.", lambda: self._run_main_action(self._extract_newest), "extract_btn"),
                ("replace-repo", "Replace Repo — replace matching repository folders with the current project contents.", lambda: self._run_main_action(self._replace_repo), "replace_btn"),
                ("zip-project", "ZIP Project — create a ZIP archive of the current project.", lambda: self._run_main_action(self._zip_current), "zip_btn"),
                ("open-explorer", "Open Current Project Folder — open the project in the system file manager.", lambda: self._run_main_action(self._open_selected_folder), "open_project_folder_btn"),
            ],
        )
        self._action_group(
            actions,
            "ChatGPT",
            [
                ("download-chatgpt-files", "Download ChatGPT Files — download file attachments exposed by the newest available response.", lambda: self._run_main_action(self._download_chatgpt_response_files), "download_chatgpt_btn"),
                ("focus-chatgpt", "Focus ChatGPT — focus all managed ChatGPT windows for this project.", lambda: self._run_main_action(self._focus_project_chatgpt_windows), "focus_chatgpt_btn"),
                ("close-chatgpt", "Close ChatGPT URL — close the managed ChatGPT window(s) for this project.", lambda: self._run_main_action(self._close_project_chatgpt_windows), "close_chatgpt_btn"),
                ("open-chatgpt", "Open ChatGPT URL — open the configured ChatGPT URL if it is not already open.", lambda: self._run_main_action(self._open_project_chatgpt_windows), "open_chatgpt_btn"),
                ("paste-zip-chatgpt", "Paste ZIP Project into ChatGPT — attach the most recent ZIP Project archive without sending.", lambda: self._run_main_action(self._paste_zip_project_into_chatgpt), "paste_zip_chatgpt_btn"),
                ("paste-error-chatgpt", "Paste Error Output into ChatGPT — paste the active command's detected error output without sending.", lambda: self._run_main_action(self._paste_error_output_into_chatgpt), "paste_error_chatgpt_btn"),
                ("paste-output-chatgpt", "Paste Output into ChatGPT — paste the active command's captured output without sending.", lambda: self._run_main_action(self._paste_output_into_chatgpt), "paste_output_chatgpt_btn"),
            ],
            columns=4,
        )
        self._action_group(
            actions,
            "Windows",
            [
                ("focus-all-commands", "Focus All Commands — focus all currently running managed command windows for this project.", lambda: self._run_main_action(self._focus_project_commands), "focus_commands_btn"),
                ("close-all-commands", "Close All Commands — close all managed command windows; running commands will be stopped.", lambda: self._run_main_action(self._stop_project_commands), "close_commands_btn"),
                ("close-all-url-windows", "Close All URL Windows — close every managed URL window for this project.", lambda: self._run_main_action(self._close_project_url_windows), "close_urls_btn"),
            ],
            columns=3,
        )
        self._action_group(
            actions,
            "Snapshot",
            [
                ("snapshot-save", "Snapshot — save a new snapshot or update the existing project structure snapshot.", lambda: self._run_main_action(self._take_snapshot), "snapshot_btn"),
                ("snapshot-clear", "Clear Snapshot — clear the saved project structure snapshot.", lambda: self._run_main_action(self._clear_snapshot), "clear_snapshot_btn"),
            ],
            columns=2,
        )

        self._build_picker_row(
            actions,
            label="Prompt",
            attr="prompt_picker",
            selected=self._on_prompt_picker_selected,
            style_popup=self._style_prompt_picker_dropdown,
            paste_command=self._paste_selected_prompt_into_chatgpt,
            paste_attr="paste_prompt_chatgpt_btn",
            tooltip="Paste Prompt into ChatGPT — paste the selected reusable prompt without sending it.",
        )
        self._build_picker_row(
            actions,
            label="Note",
            attr="note_picker",
            selected=self._on_note_picker_selected,
            style_popup=self._style_note_picker_dropdown,
            paste_command=self._paste_selected_note_into_chatgpt,
            paste_attr="paste_note_chatgpt_btn",
            tooltip="Paste Note into ChatGPT — paste the selected note without sending it.",
        )
        self._refresh_prompt_picker()
        self._refresh_note_picker()
        self._refresh_snapshot_action_icon()

    def _refresh_snapshot_action_icon(self) -> None:
        if not hasattr(self, "snapshot_btn") or not hasattr(self, "_main_action_images"):
            return
        project = self._current_project()
        key = "snapshot-update" if project is not None and bool(project.snapshot) else "snapshot-save"
        self.snapshot_btn.configure(image=self._main_action_images[key])
