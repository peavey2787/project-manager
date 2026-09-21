from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .widgets import bind_tooltip


ASSET_DIR = Path(__file__).with_name("assets") / "commands"
COMMON_ASSET_DIR = Path(__file__).with_name("assets") / "common"


class CommandToolbarController:
    """Image toolbars for command management and runtime actions."""

    def _load_command_action_icons(self) -> None:
        command_names = (
            "add-command",
            "edit-command",
            "remove-command",
            "run-command",
            "focus-command",
            "copy-command",
            "copy-output",
            "copy-error-output",
            "close-command",
        )
        images: dict[str, tk.PhotoImage] = {
            name: tk.PhotoImage(file=str(ASSET_DIR / f"{name}.png"))
            for name in command_names
        }
        images["move-up"] = tk.PhotoImage(file=str(COMMON_ASSET_DIR / "move-up.png"))
        images["move-down"] = tk.PhotoImage(file=str(COMMON_ASSET_DIR / "move-down.png"))
        self._command_action_images = images

    def _ensure_command_toolbar_state(self) -> None:
        if not hasattr(self, "_command_action_images"):
            self._load_command_action_icons()
        if not hasattr(self, "command_action_buttons"):
            self.command_action_buttons: dict[str, ttk.Button] = {}

    def _command_icon_button(
        self,
        parent,
        *,
        asset: str,
        key: str,
        tooltip: str,
        command,
    ) -> ttk.Button:
        self._ensure_command_toolbar_state()
        button = ttk.Button(
            parent,
            image=self._command_action_images[asset],
            style="CommandImageAction.TButton",
            command=command,
            takefocus=True,
        )
        bind_tooltip(button, tooltip)
        self.command_action_buttons[key] = button
        return button

    def _build_command_manage_toolbar(self, parent) -> None:
        """Build CRUD/reorder controls directly above the command table."""
        self._ensure_command_toolbar_state()
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 8))
        specs = (
            (
                "add-command",
                "Add",
                "Add Command — create a new command or script entry.",
                self._add_command,
            ),
            (
                "edit-command",
                "Edit",
                "Edit Command — edit the selected command or script entry.",
                self._edit_command,
            ),
            (
                "remove-command",
                "Remove",
                "Remove Command — delete the selected command or script entry.",
                self._remove_command,
            ),
            (
                "move-up",
                "Move Up",
                "Move Up — move the selected command one row earlier.",
                lambda: self._move_command(-1),
            ),
            (
                "move-down",
                "Move Down",
                "Move Down — move the selected command one row later.",
                lambda: self._move_command(1),
            ),
        )
        for index, (asset, key, tooltip, command) in enumerate(specs):
            button = self._command_icon_button(
                toolbar,
                asset=asset,
                key=key,
                tooltip=tooltip,
                command=command,
            )
            button.grid(row=0, column=index, padx=(0 if index == 0 else 4, 0))

    def _build_command_runtime_toolbar(self, parent) -> None:
        """Build run/focus/copy/close controls directly below the command table."""
        self._ensure_command_toolbar_state()
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(8, 0))
        specs = (
            (
                "run-command",
                "Run Now",
                "Run Command Now — launch the selected command immediately.",
                self._run_selected_command,
            ),
            (
                "focus-command",
                "Focus",
                "Focus Command Window — bring the selected command's managed terminal to the foreground.",
                self._focus_selected_command_window,
            ),
            (
                "copy-command",
                "Copy Command",
                "Copy Command — copy the selected Command / Script value to the clipboard.",
                self._copy_selected_command_text,
            ),
            (
                "copy-output",
                "Copy Output",
                "Copy Command Output — copy the selected command's captured output.",
                self._copy_selected_command_output,
            ),
            (
                "copy-error-output",
                "Copy Error Output",
                "Copy Error Output — copy the selected command's captured error output.",
                self._copy_selected_command_error_output,
            ),
            (
                "close-command",
                "Close",
                "Close Command Window — close the selected command's managed terminal window.",
                self._close_selected_command_window,
            ),
        )
        for index, (asset, key, tooltip, command) in enumerate(specs):
            button = self._command_icon_button(
                toolbar,
                asset=asset,
                key=key,
                tooltip=tooltip,
                command=command,
            )
            button.grid(row=0, column=index, padx=(0 if index == 0 else 4, 0))
        self._update_command_action_buttons()

    def _copy_selected_command_text(self) -> None:
        spec = self._selected_command()
        if spec is None:
            self._update_command_action_buttons()
            return
        self.clipboard_clear()
        self.clipboard_append(spec.command)
        self.update_idletasks()
        self._set_status(f"Copied command: {spec.label}")
