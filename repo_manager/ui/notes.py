from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..domain import NoteSpec
from .widgets import style_black_combobox_popup


class NotesController:
    """Persist project-specific free-form notes."""

    def _project_notes(self) -> list[NoteSpec]:
        project = self._current_project()
        return project.notes if project is not None else []

    def _build_notes(self) -> None:
        self._loading_note = False
        parent = self.notes_tab
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(1, weight=1)

        ttk.Label(parent, text="Notes", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        left = ttk.Frame(parent)
        left.grid(row=1, column=0, sticky="nsw", padx=(0, 10))
        self.notes_tree = ttk.Treeview(
            left,
            columns=("label",),
            show="headings",
            selectmode="browse",
            height=18,
        )
        self.notes_tree.heading("label", text="Note")
        self.notes_tree.column("label", width=250, minwidth=180, anchor="w")
        self.notes_tree.pack(fill="y", expand=True)
        self.notes_tree.bind("<<TreeviewSelect>>", self._on_note_select)

        controls = ttk.Frame(left)
        controls.pack(fill="x", pady=(6, 0))
        ttk.Button(controls, text="Add", command=self._add_note).pack(side="left", padx=(0, 5))
        ttk.Button(controls, text="Delete", command=self._delete_note).pack(side="left")

        editor = ttk.Frame(parent)
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(3, weight=1)
        ttk.Label(editor, text="Name").grid(row=0, column=0, sticky="w")
        self.note_label_var = tk.StringVar()
        self.note_label_entry = ttk.Entry(editor, textvariable=self.note_label_var)
        self.note_label_entry.grid(row=1, column=0, sticky="ew", pady=(3, 8))
        ttk.Label(editor, text="Note text").grid(row=2, column=0, sticky="w")
        self.note_text = tk.Text(editor, wrap="word", height=20)
        note_scroll = ttk.Scrollbar(editor, orient="vertical", command=self.note_text.yview)
        self.note_text.configure(yscrollcommand=note_scroll.set)
        self.note_text.grid(row=3, column=0, sticky="nsew")
        note_scroll.grid(row=3, column=1, sticky="ns")
        ttk.Label(
            editor,
            text="Notes are project-specific and saved automatically as you edit them.",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.note_label_var.trace_add("write", self._on_note_editor_changed)
        self.note_text.bind("<<Modified>>", self._on_note_text_modified, add=True)
        self.note_text.edit_modified(False)
        self._refresh_notes()
        self._refresh_note_picker()

    def _refresh_notes(self, preferred_id: str | None = None) -> None:
        project = self._current_project()
        notes = self._project_notes()
        selected = preferred_id or (project.selected_note_id if project is not None else "")
        if not hasattr(self, "notes_tree"):
            self._refresh_note_picker(preferred_id=selected or None)
            return

        for item in self.notes_tree.get_children():
            self.notes_tree.delete(item)
        for note in notes:
            self.notes_tree.insert("", "end", iid=note.note_id, values=(note.label,))

        valid_ids = {note.note_id for note in notes}
        if selected not in valid_ids:
            selected = notes[0].note_id if notes else ""
        if project is not None:
            project.selected_note_id = selected
        if selected:
            self.notes_tree.selection_set(selected)
            self.notes_tree.focus(selected)

        self._load_selected_note()
        self._refresh_note_picker(preferred_id=selected or None)

    def _selected_note(self) -> NoteSpec | None:
        if not hasattr(self, "notes_tree"):
            return None
        selected = self.notes_tree.selection()
        if not selected:
            return None
        note_id = selected[0]
        return next((note for note in self._project_notes() if note.note_id == note_id), None)

    def _load_selected_note(self) -> None:
        if not hasattr(self, "note_label_var"):
            return
        note = self._selected_note()
        self._loading_note = True
        try:
            self.note_label_var.set(note.label if note else "")
            self.note_text.delete("1.0", "end")
            if note:
                self.note_text.insert("1.0", note.text)
            self.note_text.edit_modified(False)
        finally:
            self._loading_note = False

    def _on_note_select(self, _event=None) -> None:
        if self._loading_note:
            return
        self._load_selected_note()
        note = self._selected_note()
        project = self._current_project()
        if project is not None:
            project.selected_note_id = note.note_id if note else ""
            self._save_config()
        self._refresh_note_picker(preferred_id=note.note_id if note else None)

    def _on_note_editor_changed(self, *_args) -> None:
        if not self._loading_note:
            self._save_note_editor()

    def _on_note_text_modified(self, _event=None) -> None:
        try:
            modified = bool(self.note_text.edit_modified())
            self.note_text.edit_modified(False)
        except tk.TclError:
            return
        if modified and not self._loading_note:
            self._save_note_editor()

    def _save_note_editor(self) -> None:
        note = self._selected_note()
        if note is None:
            return
        label = self.note_label_var.get().strip() or "Note"
        note.label = label
        note.text = self.note_text.get("1.0", "end-1c")
        if self.notes_tree.exists(note.note_id):
            self.notes_tree.item(note.note_id, values=(label,))
        project = self._current_project()
        if project is not None:
            project.selected_note_id = note.note_id
        self._save_config()
        self._refresh_note_picker(preferred_id=note.note_id)

    def _add_note(self) -> None:
        project = self._current_project()
        if project is None:
            messagebox.showinfo("No project", "Add or select a project first.", parent=self)
            return

        selected = self._selected_note()
        draft_label = self.note_label_var.get().strip() if hasattr(self, "note_label_var") else ""
        draft_text = self.note_text.get("1.0", "end-1c") if hasattr(self, "note_text") else ""
        default_label = f"Note {len(project.notes) + 1}"
        if selected is None and (draft_label or draft_text):
            note = NoteSpec(label=draft_label or default_label, text=draft_text)
        else:
            note = NoteSpec(label=default_label, text="")

        project.notes.append(note)
        project.selected_note_id = note.note_id
        self._save_config()
        self._refresh_notes(preferred_id=note.note_id)
        self.note_label_entry.focus_set()
        if not draft_label or selected is not None:
            self.note_label_entry.selection_range(0, "end")

    def _delete_note(self) -> None:
        project = self._current_project()
        note = self._selected_note()
        if project is None or note is None:
            return
        if not messagebox.askyesno("Delete note", f"Delete note {note.label!r}?", parent=self):
            return
        project.notes = [item for item in project.notes if item.note_id != note.note_id]
        if project.selected_note_id == note.note_id:
            project.selected_note_id = ""
        self._save_config()
        self._refresh_notes()

    def _style_note_picker_dropdown(self) -> None:
        if hasattr(self, "note_picker"):
            style_black_combobox_popup(self.note_picker)

    def _refresh_note_picker(self, preferred_id: str | None = None) -> None:
        if not hasattr(self, "note_picker"):
            return
        project = self._current_project()
        notes = self._project_notes()
        current_id = preferred_id or (project.selected_note_id if project is not None else "")
        self.note_picker.configure(values=[note.label for note in notes])
        selected_index = next((i for i, note in enumerate(notes) if note.note_id == current_id), -1)
        if selected_index < 0 and notes:
            selected_index = 0
        if selected_index >= 0:
            self.note_picker.current(selected_index)
            if project is not None:
                project.selected_note_id = notes[selected_index].note_id
        else:
            self.note_picker.set("")
            if project is not None:
                project.selected_note_id = ""
        self._update_paste_note_button()

    def _on_note_picker_selected(self, _event=None) -> None:
        project = self._current_project()
        notes = self._project_notes()
        index = self.note_picker.current() if hasattr(self, "note_picker") else -1
        if project is not None and 0 <= index < len(notes):
            project.selected_note_id = notes[index].note_id
            self._save_config()
            self._refresh_notes(preferred_id=project.selected_note_id)
        self._update_paste_note_button()

    def _update_paste_note_button(self) -> None:
        if hasattr(self, "paste_note_chatgpt_btn"):
            enabled = bool(self._project_notes()) and self._worker_count == 0 and self._current_project() is not None
            self.paste_note_chatgpt_btn.configure(state="normal" if enabled else "disabled")

    def _paste_selected_note_into_chatgpt(self) -> None:
        project = self._current_project()
        notes = self._project_notes()
        index = self.note_picker.current() if hasattr(self, "note_picker") else -1
        if project is None or index < 0 or index >= len(notes):
            messagebox.showinfo("No note selected", "Add or select a note first.", parent=self)
            return
        note = notes[index]
        if not note.text.strip():
            messagebox.showinfo("Note is empty", "The selected note has no text.", parent=self)
            return
        project.selected_note_id = note.note_id
        self._save_config()
        self._paste_text_into_chatgpt(
            note.text,
            action_title="Paste Note into ChatGPT",
            status_subject=note.label,
            debug_kind="note",
        )
