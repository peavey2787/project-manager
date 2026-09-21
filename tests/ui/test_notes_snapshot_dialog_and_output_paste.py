from pathlib import Path

from repo_manager.config.repository import _note_from
from repo_manager.domain import NoteSpec


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_notes_tab_is_editable_deletable_and_persistent() -> None:
    layout = _source("repo_manager/ui/controllers/layout.py")
    notes = _source("repo_manager/ui/notes.py")
    config = _source("repo_manager/config/repository.py")
    assert 'self.notebook.add(self.notes_tab, text="Notes")' in layout
    assert "def _add_note" in notes
    assert "def _delete_note" in notes
    assert "def _save_note_editor" in notes
    assert "self._save_config()" in notes
    parsed = _note_from({"label": "Build notes", "text": "Remember this", "note_id": "n1"})
    assert parsed == NoteSpec(label="Build notes", text="Remember this", note_id="n1")
    assert 'data.get("notes")' in config


def test_main_actions_has_full_output_paste_next_to_error_output_and_reuses_shared_chat_paste() -> None:
    layout = _source("repo_manager/ui/controllers/layout.py")
    actions = _source("repo_manager/ui/main_actions.py")
    chat = _source("repo_manager/ui/controllers/chat_status.py")
    commands = _source("repo_manager/ui/controllers/command_actions.py")
    assert 'Paste Error Output into ChatGPT' in actions
    assert 'Paste Output into ChatGPT' in actions
    assert "def _active_project_command_output" in commands
    method = chat[chat.index("def _paste_output_into_chatgpt"):chat.index("def _chat_state_label")]
    assert "_active_project_command_output()" in method
    assert "self._paste_text_into_chatgpt(" in method


def test_snapshot_dialog_is_large_and_footer_buttons_use_fixed_grid_row() -> None:
    source = _source("repo_manager/ui/dialogs/misc.py")
    section = source[source.index("class SnapshotDiffDialog"):source.index("class MultiSelectDialog")]
    assert "width = min(980" in section
    assert "height = min(700" in section
    assert 'buttons.grid(row=3' in section
    assert 'text="Accept current as new snapshot"' in section
    assert 'text="Ignore"' in section


def test_project_settings_contains_auto_stop_before_extract() -> None:
    layout = _source("repo_manager/ui/controllers/layout.py")
    settings = _source("repo_manager/ui/controllers/project_settings.py")
    assert 'text="Auto close all commands before extracting"' in layout
    assert "auto_stop_commands_before_extract_var" in settings
    assert "project.auto_stop_commands_before_extract" in settings


def test_legacy_notes_migrate_into_each_project_without_sharing_objects() -> None:
    from repo_manager.config.repository import _project_from

    legacy = [{"label": "Legacy note", "text": "Keep this", "note_id": "legacy-note"}]
    first = _project_from({"name": "One"}, legacy_notes=legacy)
    second = _project_from({"name": "Two"}, legacy_notes=legacy)
    assert first.notes[0].text == "Keep this"
    assert second.notes[0].text == "Keep this"
    assert first.notes is not second.notes
    assert first.notes[0] is not second.notes[0]


def test_add_note_preserves_typed_draft_when_no_note_is_selected() -> None:
    from repo_manager.domain import Project
    from repo_manager.ui.notes import NotesController

    class Var:
        def get(self):
            return "Release checklist"

    class Text:
        def get(self, *_args):
            return "first line\nsecond line"

    class Entry:
        def focus_set(self):
            pass

        def selection_range(self, *_args):
            pass

    class Dummy(NotesController):
        def __init__(self):
            self.project = Project(name="Demo")
            self.project.notes.clear()
            self.note_label_var = Var()
            self.note_text = Text()
            self.note_label_entry = Entry()
            self.saved = 0
            self.refreshed = ""

        def _current_project(self):
            return self.project

        def _selected_note(self):
            return None

        def _save_config(self):
            self.saved += 1

        def _refresh_notes(self, preferred_id=None):
            self.refreshed = preferred_id or ""

    app = Dummy()
    app._add_note()
    assert len(app.project.notes) == 1
    assert app.project.notes[0].label == "Release checklist"
    assert app.project.notes[0].text == "first line\nsecond line"
    assert app.project.selected_note_id == app.project.notes[0].note_id
    assert app.refreshed == app.project.notes[0].note_id
    assert app.saved == 1
