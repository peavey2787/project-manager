from __future__ import annotations

from pathlib import Path

from repo_manager.config.repository import _project_from

ROOT = Path(__file__).resolve().parents[2]


def test_files_tab_and_server_controls_are_wired() -> None:
    layout = (ROOT / "repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8")
    actions = (ROOT / "repo_manager/ui/main_actions.py").read_text(encoding="utf-8")
    files_ui = (ROOT / "repo_manager/ui/files.py").read_text(encoding="utf-8")
    app = (ROOT / "repo_manager/ui/app.py").read_text(encoding="utf-8")
    assert 'self.notebook.add(self.files_tab, text="Files")' in layout
    assert "self._build_files()" in layout
    assert "FilesController" in app
    assert 'text="Start Server"' in files_ui
    assert '("HTTP", "HTTPS")' in files_ui
    assert '("Generated certificate", "Custom certificate")' in files_ui
    assert "self._load_editor_file(path)" in files_ui
    assert "path.write_text" in files_ui


def test_file_server_settings_and_last_created_zip_migrate() -> None:
    project = _project_from(
        {
            "name": "Demo",
            "last_created_zip": r"C:\\tmp\\demo.zip",
            "file_server_protocol": "https",
            "file_server_port": 9443,
            "file_server_cert_mode": "custom",
            "file_server_cert_path": r"C:\\cert.pem",
            "file_server_key_path": r"C:\\key.pem",
        }
    )
    assert project.last_created_zip.endswith("demo.zip")
    assert project.file_server_protocol == "https"
    assert project.file_server_port == 9443
    assert project.file_server_cert_mode == "custom"
    assert project.file_server_cert_path.endswith("cert.pem")
    assert project.file_server_key_path.endswith("key.pem")


def test_zip_project_paste_uses_file_clipboard_and_existing_chatgpt_focus_flow() -> None:
    layout = (ROOT / "repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8")
    actions = (ROOT / "repo_manager/ui/main_actions.py").read_text(encoding="utf-8")
    archive = (ROOT / "repo_manager/ui/controllers/archive.py").read_text(encoding="utf-8")
    chat = (ROOT / "repo_manager/ui/controllers/chat_status.py").read_text(encoding="utf-8")
    clipboard = (ROOT / "repo_manager/platform/windows/clipboard.py").read_text(encoding="utf-8")
    assert 'Paste ZIP Project into ChatGPT' in actions
    assert "project.last_created_zip = str(path)" in archive
    assert "def _paste_file_into_chatgpt" in chat
    assert "copy_file_to_windows_clipboard(path)" in chat
    assert "focus_windows_browser(run)" in chat
    assert "invoke_chatgpt_composer_msaa" in chat
    assert "_focus_chatgpt_composer_for_paste" in chat
    assert "for delay in (0.08, 0.18, 0.30)" in chat
    assert "Chat with ChatGPT" in chat
    assert "file remains at" in chat
    assert "paste_clipboard_with_ctrl_v()" in chat
    assert "CF_HDROP = 15" in clipboard


def test_files_protocol_is_black_and_server_command_button_is_wired() -> None:
    layout = (ROOT / "repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8")
    files_ui = (ROOT / "repo_manager/ui/files.py").read_text(encoding="utf-8")
    assert '"Protocol.TCombobox"' in layout
    assert 'fieldbackground="#000000"' in layout
    assert 'style="Protocol.TCombobox"' in files_ui
    assert 'text="Add Server Command"' in files_ui
    assert "build_folder_server_command(" in files_ui
    assert "project.commands.append(spec)" in files_ui
    assert "self.notebook.select(self.commands_tab)" in files_ui


def test_main_actions_exposes_clear_snapshot() -> None:
    layout = (ROOT / "repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8")
    actions = (ROOT / "repo_manager/ui/main_actions.py").read_text(encoding="utf-8")
    archive = (ROOT / "repo_manager/ui/controllers/archive.py").read_text(encoding="utf-8")
    assert 'Clear Snapshot' in actions
    assert "self._clear_snapshot" in actions
    assert "def _clear_snapshot" in archive
    assert "project.snapshot = []" in archive


def test_folder_zip_is_kept_before_chatgpt_paste_attempt() -> None:
    file_actions = (ROOT / "repo_manager/ui/file_chat_actions.py").read_text(encoding="utf-8")
    assert "return zip_project(" in file_actions
    assert "Created folder ZIP for ChatGPT" in file_actions
    assert "Created folder ZIP:" in file_actions
    start = file_actions.index("def _zip_folder_and_paste_into_chatgpt")
    body = file_actions[start:]
    assert body.index("return zip_project(") < body.index("self._paste_file_into_chatgpt(")
