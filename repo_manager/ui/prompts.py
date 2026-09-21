from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ..domain import PromptSpec
from .widgets import bind_tooltip


class PromptsController:
    """Edit project-specific ChatGPT prompts and expose them to Main actions."""

    def _project_prompts(self) -> list[PromptSpec]:
        project = self._current_project()
        return project.prompts if project is not None else []

    def _build_prompts(self) -> None:
        self._loading_prompt = False
        self._prompts_split_initialized = False
        parent = self.prompts_tab
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        ttk.Label(parent, text="Reusable ChatGPT prompts", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        panes = ttk.Panedwindow(parent, orient="vertical")
        panes.grid(row=1, column=0, sticky="nsew")
        self.prompts_panes = panes

        prompt_list = ttk.Frame(panes)
        prompt_list.rowconfigure(0, weight=1)
        prompt_list.columnconfigure(0, weight=1)
        self.prompts_tree = ttk.Treeview(
            prompt_list,
            columns=("label",),
            show="headings",
            selectmode="browse",
            height=8,
        )
        self.prompts_tree.heading("label", text="Prompt")
        self.prompts_tree.column("label", minwidth=180, anchor="w", stretch=True)
        prompt_scroll = ttk.Scrollbar(prompt_list, orient="vertical", command=self.prompts_tree.yview)
        self.prompts_tree.configure(yscrollcommand=prompt_scroll.set)
        self.prompts_tree.grid(row=0, column=0, sticky="nsew")
        prompt_scroll.grid(row=0, column=1, sticky="ns")
        self.prompts_tree.bind("<<TreeviewSelect>>", self._on_prompt_select)

        controls = ttk.Frame(prompt_list)
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(controls, text="Add", command=self._add_prompt).pack(side="left", padx=(0, 5))
        ttk.Button(controls, text="Remove", command=self._remove_prompt).pack(side="left", padx=(0, 5))
        common_assets = Path(__file__).with_name("assets") / "common"
        self._prompt_order_images = {
            "up": tk.PhotoImage(file=str(common_assets / "move-up.png")),
            "down": tk.PhotoImage(file=str(common_assets / "move-down.png")),
        }
        up_button = ttk.Button(
            controls,
            image=self._prompt_order_images["up"],
            style="ImageAction.TButton",
            command=lambda: self._move_prompt(-1),
        )
        up_button.pack(side="left", padx=(0, 5))
        bind_tooltip(up_button, "Move Up — move the selected prompt one row earlier.")
        down_button = ttk.Button(
            controls,
            image=self._prompt_order_images["down"],
            style="ImageAction.TButton",
            command=lambda: self._move_prompt(1),
        )
        down_button.pack(side="left", padx=(0, 5))
        bind_tooltip(down_button, "Move Down — move the selected prompt one row later.")

        editor = ttk.Frame(panes, padding=(0, 8, 0, 0))
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(3, weight=1)
        ttk.Label(editor, text="Name").grid(row=0, column=0, sticky="w")
        self.prompt_label_var = tk.StringVar()
        self.prompt_label_entry = ttk.Entry(editor, textvariable=self.prompt_label_var)
        self.prompt_label_entry.grid(row=1, column=0, sticky="ew", pady=(3, 8))
        ttk.Label(editor, text="Prompt text").grid(row=2, column=0, sticky="w")
        self.prompt_text = tk.Text(editor, wrap="word", height=8)
        editor_scroll = ttk.Scrollbar(editor, orient="vertical", command=self.prompt_text.yview)
        self.prompt_text.configure(yscrollcommand=editor_scroll.set)
        self.prompt_text.grid(row=3, column=0, sticky="nsew")
        editor_scroll.grid(row=3, column=1, sticky="ns")
        ttk.Label(
            editor,
            text=(
                "Prompts are project-specific and saved automatically. Main actions can paste "
                "the selected prompt into a monitored ChatGPT window without sending it."
            ),
            wraplength=720,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        panes.add(prompt_list, weight=1)
        panes.add(editor, weight=1)
        self.after_idle(self._set_prompts_default_split)

        self.prompt_label_var.trace_add("write", self._on_prompt_editor_changed)
        self.prompt_text.bind("<<Modified>>", self._on_prompt_text_modified, add=True)
        self.prompt_text.edit_modified(False)
        self._refresh_prompts()

    def _set_prompts_default_split(self) -> None:
        if self._prompts_split_initialized or not hasattr(self, "prompts_panes"):
            return
        height = self.prompts_panes.winfo_height()
        if height < 80:
            self.after(50, self._set_prompts_default_split)
            return
        try:
            self.prompts_panes.sashpos(0, height // 2)
            self._prompts_split_initialized = True
        except tk.TclError:
            pass

    def _refresh_prompts(self, preferred_id: str | None = None) -> None:
        project = self._current_project()
        prompts = self._project_prompts()
        selected = preferred_id or (project.selected_prompt_id if project is not None else "")
        if not hasattr(self, "prompts_tree"):
            self._refresh_prompt_picker(preferred_id=selected or None)
            return

        for item in self.prompts_tree.get_children():
            self.prompts_tree.delete(item)
        for prompt in prompts:
            self.prompts_tree.insert("", "end", iid=prompt.prompt_id, values=(prompt.label,))

        valid_ids = {prompt.prompt_id for prompt in prompts}
        if selected not in valid_ids:
            selected = prompts[0].prompt_id if prompts else ""
        if project is not None:
            project.selected_prompt_id = selected
        if selected:
            self.prompts_tree.selection_set(selected)
            self.prompts_tree.focus(selected)

        self._load_selected_prompt()
        self._refresh_prompt_picker(preferred_id=selected or None)

    def _selected_prompt(self) -> PromptSpec | None:
        if not hasattr(self, "prompts_tree"):
            return None
        selected = self.prompts_tree.selection()
        if not selected:
            return None
        prompt_id = selected[0]
        return next((prompt for prompt in self._project_prompts() if prompt.prompt_id == prompt_id), None)

    def _load_selected_prompt(self) -> None:
        if not hasattr(self, "prompt_label_var"):
            return
        prompt = self._selected_prompt()
        self._loading_prompt = True
        try:
            self.prompt_label_var.set(prompt.label if prompt else "")
            self.prompt_text.delete("1.0", "end")
            if prompt:
                self.prompt_text.insert("1.0", prompt.text)
            self.prompt_text.edit_modified(False)
        finally:
            self._loading_prompt = False

    def _on_prompt_select(self, _event=None) -> None:
        if self._loading_prompt:
            return
        self._load_selected_prompt()
        prompt = self._selected_prompt()
        project = self._current_project()
        if project is not None:
            project.selected_prompt_id = prompt.prompt_id if prompt else ""
            self._save_config()
        self._refresh_prompt_picker(preferred_id=prompt.prompt_id if prompt else None)

    def _on_prompt_editor_changed(self, *_args) -> None:
        if not self._loading_prompt:
            self._save_prompt_editor()

    def _on_prompt_text_modified(self, _event=None) -> None:
        try:
            modified = bool(self.prompt_text.edit_modified())
            self.prompt_text.edit_modified(False)
        except tk.TclError:
            return
        if modified and not self._loading_prompt:
            self._save_prompt_editor()

    def _save_prompt_editor(self) -> None:
        prompt = self._selected_prompt()
        if prompt is None:
            return
        label = self.prompt_label_var.get().strip() or "Prompt"
        prompt.label = label
        prompt.text = self.prompt_text.get("1.0", "end-1c")
        if self.prompts_tree.exists(prompt.prompt_id):
            self.prompts_tree.item(prompt.prompt_id, values=(label,))
        project = self._current_project()
        if project is not None:
            project.selected_prompt_id = prompt.prompt_id
        self._save_config()
        self._refresh_prompt_picker(preferred_id=prompt.prompt_id)

    def _add_prompt(self) -> None:
        project = self._current_project()
        if project is None:
            messagebox.showinfo("No project", "Add or select a project first.", parent=self)
            return
        prompt = PromptSpec(label=f"Prompt {len(project.prompts) + 1}", text="")
        project.prompts.append(prompt)
        project.selected_prompt_id = prompt.prompt_id
        self._save_config()
        self._refresh_prompts(preferred_id=prompt.prompt_id)
        self.prompt_label_entry.focus_set()
        self.prompt_label_entry.selection_range(0, "end")

    def _remove_prompt(self) -> None:
        project = self._current_project()
        prompt = self._selected_prompt()
        if project is None or prompt is None:
            return
        if not messagebox.askyesno("Remove prompt", f"Remove prompt {prompt.label!r}?", parent=self):
            return
        project.prompts = [item for item in project.prompts if item.prompt_id != prompt.prompt_id]
        if project.selected_prompt_id == prompt.prompt_id:
            project.selected_prompt_id = ""
        self._save_config()
        self._refresh_prompts()

    def _move_prompt(self, delta: int) -> None:
        project = self._current_project()
        prompt = self._selected_prompt()
        if project is None or prompt is None:
            return
        index = next((i for i, item in enumerate(project.prompts) if item.prompt_id == prompt.prompt_id), -1)
        target = index + delta
        if index < 0 or target < 0 or target >= len(project.prompts):
            return
        project.prompts[index], project.prompts[target] = project.prompts[target], project.prompts[index]
        project.selected_prompt_id = prompt.prompt_id
        self._save_config()
        self._refresh_prompts(preferred_id=prompt.prompt_id)

    def _style_prompt_picker_dropdown(self) -> None:
        """Force the Main-actions prompt popup to a readable black palette."""
        if not hasattr(self, "prompt_picker"):
            return
        try:
            popdown = self.prompt_picker.tk.call("ttk::combobox::PopdownWindow", str(self.prompt_picker))
            listbox = f"{popdown}.f.l"
            self.prompt_picker.tk.call(
                listbox,
                "configure",
                "-background", "#000000",
                "-foreground", "#ffffff",
                "-selectbackground", "#3f6ea8",
                "-selectforeground", "#ffffff",
            )
        except tk.TclError:
            pass

    def _refresh_prompt_picker(self, preferred_id: str | None = None) -> None:
        if not hasattr(self, "prompt_picker"):
            return
        project = self._current_project()
        prompts = self._project_prompts()
        current_id = preferred_id or (project.selected_prompt_id if project is not None else "")
        values = [f"{index + 1}. {prompt.label}" for index, prompt in enumerate(prompts)]
        self.prompt_picker.configure(values=values)
        selected_index = next((i for i, prompt in enumerate(prompts) if prompt.prompt_id == current_id), -1)
        if selected_index < 0 and prompts:
            selected_index = 0
        if selected_index >= 0:
            self.prompt_picker.current(selected_index)
            if project is not None:
                project.selected_prompt_id = prompts[selected_index].prompt_id
        else:
            self.prompt_picker.set("")
            if project is not None:
                project.selected_prompt_id = ""
        self._update_paste_prompt_button()

    def _on_prompt_picker_selected(self, _event=None) -> None:
        project = self._current_project()
        prompts = self._project_prompts()
        index = self.prompt_picker.current() if hasattr(self, "prompt_picker") else -1
        if project is not None and 0 <= index < len(prompts):
            project.selected_prompt_id = prompts[index].prompt_id
            self._save_config()
            self._refresh_prompts(preferred_id=project.selected_prompt_id)
        self._update_paste_prompt_button()

    def _update_paste_prompt_button(self) -> None:
        if hasattr(self, "paste_prompt_chatgpt_btn"):
            enabled = bool(self._project_prompts()) and self._worker_count == 0 and self._current_project() is not None
            self.paste_prompt_chatgpt_btn.configure(state="normal" if enabled else "disabled")

    def _paste_selected_prompt_into_chatgpt(self) -> None:
        project = self._current_project()
        prompts = self._project_prompts()
        index = self.prompt_picker.current() if hasattr(self, "prompt_picker") else -1
        if project is None or index < 0 or index >= len(prompts):
            messagebox.showinfo("No prompt selected", "Add or select a prompt first.", parent=self)
            return
        prompt = prompts[index]
        if not prompt.text.strip():
            messagebox.showinfo("Prompt is empty", "The selected prompt has no text.", parent=self)
            return
        project.selected_prompt_id = prompt.prompt_id
        self._save_config()
        self._paste_text_into_chatgpt(
            prompt.text,
            action_title="Paste Prompt into ChatGPT",
            status_subject=prompt.label,
            debug_kind="prompt",
        )
