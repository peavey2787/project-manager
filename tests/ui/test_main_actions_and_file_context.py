from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_https_certificate_selector_is_black_including_popup() -> None:
    layout = source("repo_manager/ui/controllers/layout.py")
    files = source("repo_manager/ui/files.py")
    widgets = source("repo_manager/ui/widgets.py")
    assert '"Certificate.TCombobox"' in layout
    assert 'style="Certificate.TCombobox"' in files
    assert 'postcommand=lambda: style_black_combobox_popup(self.file_server_cert_mode_box)' in files
    assert '"-background", "#000000"' in widgets
    assert '"-foreground", "#ffffff"' in widgets


def test_chatgpt_download_marks_commands_downloading_until_worker_finishes() -> None:
    app = source("repo_manager/ui/app.py")
    chat = source("repo_manager/ui/controllers/chat_status.py")
    sidebar = source("repo_manager/ui/controllers/project_sidebar.py")
    lifecycle = source("repo_manager/ui/controllers/lifecycle.py")
    assert "self._downloading_project_ids: set[str] = set()" in app
    assert "self._downloading_project_ids.add(project.project_id)" in chat
    assert 'self._ui_queue.put(("chat_download_finished", project.project_id, None))' in chat
    assert 'return "downloading"' in sidebar
    assert '"YELLOW · Downloading"' in sidebar
    assert 'color, text = "yellow", "Downloading"' in sidebar
    assert 'kind == "chat_download_finished"' in lifecycle


def test_project_context_closes_commands_after_url_windows_and_uses_close_wording() -> None:
    layout = source("repo_manager/ui/controllers/layout.py")
    close_urls = layout.index('label="Close All URL Windows"')
    close_commands = layout.index('label="Close All Commands"')
    assert close_urls < close_commands
    assert 'label="Stop All Commands"' not in layout
    assert 'text="Auto close all commands before extracting"' in layout


def test_main_actions_are_grouped_icons_with_tooltips_and_window_controls() -> None:
    actions = source("repo_manager/ui/main_actions.py")
    widgets = source("repo_manager/ui/widgets.py")
    for group in ['"Project"', '"Snapshot"', '"ChatGPT"', '"Windows"']:
        assert group in actions
    assert "bind_tooltip(button, tooltip)" in actions
    assert "class ToolTip" in widgets
    for label in [
        "Snapshot", "Clear Snapshot", "Close All Commands", "Close All URL Windows",
        "Open ChatGPT URL", "Close ChatGPT URL", "Focus ChatGPT", "Focus All Commands",
    ]:
        assert label in actions
    assert actions.index("Snapshot —") < actions.index("Clear Snapshot —")


def test_files_tree_context_can_paste_files_or_zip_folders_into_chatgpt() -> None:
    files = source("repo_manager/ui/files.py")
    context = source("repo_manager/ui/file_chat_actions.py")
    chat = source("repo_manager/ui/controllers/chat_status.py")
    assert 'self.files_tree.bind("<Button-3>", self._show_files_context' in files
    assert 'label="Paste into ChatGPT"' in context
    assert 'label="ZIP and Paste into ChatGPT"' in context
    assert "zip_project(" in context
    assert "self._paste_file_into_chatgpt(" in context
    assert "def _paste_file_into_chatgpt" in chat


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_project_list_uses_drag_and_context_menu_without_redundant_buttons() -> None:
    layout = source("repo_manager/ui/controllers/layout.py")
    sidebar = source("repo_manager/ui/controllers/project_sidebar.py")
    assert 'ttk.Button(btn_row, text="Add"' not in layout
    assert 'ttk.Button(btn_row, text="Remove"' not in layout
    assert 'text="▲ Up"' not in layout
    assert 'text="▼ Down"' not in layout
    assert 'self.project_menu.add_command(label="Add Project"' in layout
    assert 'self.project_empty_menu.add_command(label="Add Project"' in layout
    assert 'label="Remove Project"' in layout
    assert 'self.project_empty_menu.tk_popup' in sidebar
    assert 'self.project_list.bind("<B1-Motion>", self._project_drag_motion)' in layout


def test_main_actions_use_compact_52px_image_assets_without_caption_text() -> None:
    actions = source("repo_manager/ui/main_actions.py")
    layout = source("repo_manager/ui/controllers/layout.py")
    assets = ROOT / "repo_manager" / "ui" / "assets" / "main_actions"
    expected = {
        "extract.png", "zip-project.png", "replace-repo.png", "open-explorer.png",
        "snapshot-save.png", "snapshot-update.png", "snapshot-clear.png",
        "open-chatgpt.png", "close-chatgpt.png", "focus-chatgpt.png",
        "download-chatgpt-files.png", "paste-zip-chatgpt.png",
        "paste-error-chatgpt.png", "paste-output-chatgpt.png",
        "paste-note-chatgpt.png", "focus-all-commands.png",
        "close-all-commands.png", "close-all-url-windows.png",
    }
    assert {p.name for p in assets.glob("*.png")} == expected
    assert all(_png_size(assets / name) == (52, 52) for name in expected)
    assert 'image=image' in actions
    assert 'style="ImageAction.TButton"' in actions
    assert 'text=icon' not in actions
    assert '"snapshot-update" if project is not None and bool(project.snapshot)' in actions
    assert 'style.configure("ImageAction.TButton"' in layout
    assert 'padding=(0, 0)' in layout
    assert 'sticky=""' in actions


def test_main_actions_have_note_picker_and_paste_note_action() -> None:
    actions = source("repo_manager/ui/main_actions.py")
    notes = source("repo_manager/ui/notes.py")
    lifecycle = source("repo_manager/ui/controllers/lifecycle.py")
    assert 'label="Note"' in actions
    assert 'attr="note_picker"' in actions
    assert 'paste_command=self._paste_selected_note_into_chatgpt' in actions
    assert 'asset="paste-note-chatgpt"' in actions
    assert 'def _refresh_note_picker' in notes
    assert 'def _paste_selected_note_into_chatgpt' in notes
    assert 'debug_kind="note"' in notes
    assert 'self._update_paste_note_button()' in lifecycle

