from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from repo_manager.ui.app import ProjectRepoManagerApp
from repo_manager.domain import Project


class ArchiveDownloadStatusTests(unittest.TestCase):
    def _app(self) -> ProjectRepoManagerApp:
        return ProjectRepoManagerApp.__new__(ProjectRepoManagerApp)

    @staticmethod
    def _write_zip(path: Path) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("README.md", "ok")

    def test_partial_matching_zip_is_downloading(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "ghost-talk-v2.zip.part").write_bytes(b"partial")
            project = Project(match_string="ghost-talk", downloads_dir=str(root))
            self.assertEqual(self._app()._download_archive_status(project), "Downloading")

    def test_complete_new_zip_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "ghost-talk-v2.zip"
            self._write_zip(archive)
            project = Project(match_string="ghost-talk", downloads_dir=str(root))
            self.assertEqual(self._app()._download_archive_status(project), "Ready to extract: ghost-talk-v2.zip")

    def test_same_extracted_signature_is_not_ready_again(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "ghost-talk-v2.zip"
            self._write_zip(archive)
            stat = archive.stat()
            project = Project(
                match_string="ghost-talk",
                downloads_dir=str(root),
                last_extracted_zip=archive.name,
                last_extracted_zip_size=stat.st_size,
                last_extracted_zip_mtime_ns=stat.st_mtime_ns,
            )
            self.assertEqual(self._app()._download_archive_status(project), "")

    def test_replaced_same_filename_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "ghost-talk-v2.zip"
            self._write_zip(archive)
            project = Project(
                match_string="ghost-talk",
                downloads_dir=str(root),
                last_extracted_zip=archive.name,
                last_extracted_zip_size=1,
                last_extracted_zip_mtime_ns=1,
            )
            self.assertEqual(self._app()._download_archive_status(project), "Ready to extract: ghost-talk-v2.zip")


class SourceLayoutTests(unittest.TestCase):
    def test_command_dialog_browse_is_opt_in_and_starts_in_project_folder(self) -> None:
        source = ((Path("repo_manager/ui/controllers/command_editor.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/command_actions.py").read_text(encoding="utf-8")) + Path("repo_manager/ui/dialogs/command.py").read_text(encoding="utf-8"))
        self.assertIn("initial_dir = project.working_dir", source)
        self.assertNotIn("auto_browse=True", source)
        self.assertNotIn("self.after_idle(self._browse)", source)
        self.assertIn('text="Browse…"', source)

    def test_settings_are_auto_saved_and_no_save_button_remains(self) -> None:
        source = (Path("repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/project_settings.py").read_text(encoding="utf-8"))
        self.assertNotIn('text="Save Project Settings"', source)
        self.assertIn("_bind_project_settings_autosave", source)
        self.assertIn("_autosave_project_settings_now", source)

    def test_open_project_folder_is_in_main_actions(self) -> None:
        source = Path("repo_manager/ui/main_actions.py").read_text(encoding="utf-8")
        self.assertIn("open_project_folder_btn", source)
        self.assertIn('Open Current Project Folder', source)

    def test_activity_log_is_bounded(self) -> None:
        source = (Path("repo_manager/ui/controllers/lifecycle.py").read_text(encoding="utf-8") + Path("repo_manager/ui/constants.py").read_text(encoding="utf-8"))
        self.assertIn("MAX_ACTIVITY_LINES", source)
        self.assertIn("MAX_PENDING_LOG_LINES", source)
        self.assertIn("faulthandler.enable", source)

    def test_chat_accessibility_events_are_filtered_before_ui_queue(self) -> None:
        source = (Path("repo_manager/chat/event_router.py").read_text(encoding="utf-8") + Path("repo_manager/chat/polling.py").read_text(encoding="utf-8"))
        self.assertIn("Firefox publishes a very large number of accessibility changes", source)
        self.assertIn("if not relevant:", source)
        self.assertIn("Unchanged polling snapshots do not need to be queued to Tk", source)

    def test_project_settings_start_expanded(self) -> None:
        source = (Path("repo_manager/ui/app.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8"))
        self.assertIn("self._project_settings_visible = True", source)
        self.assertIn('text="▼ Project Settings"', source)
        self.assertIn('settings.pack(fill="x"', source)

    def test_project_context_has_active_command_copy_and_extract(self) -> None:
        source = (Path("repo_manager/ui/controllers/project_sidebar.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8"))
        self.assertIn('label="Copy Active Command Output"', source)
        self.assertIn('label="Copy Active Command Error Output"', source)
        self.assertIn('label="Extract Newest ZIP"', source)

    def test_commands_tab_selects_active_command(self) -> None:
        source = Path("repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/project_sidebar.py").read_text(encoding="utf-8")
        self.assertIn('self.notebook.bind("<<NotebookTabChanged>>"', source)
        self.assertIn("_select_active_command_for_current_project", source)
        self.assertIn("preferred_id=project.commands[0].command_id", Path("repo_manager/ui/controllers/command_actions.py").read_text(encoding="utf-8"))

    def test_window_geometry_and_ensure_open_are_persisted(self) -> None:
        source = (Path("repo_manager/ui/controllers/lifecycle.py").read_text(encoding="utf-8") + (Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8")))
        self.assertIn("_capture_all_url_window_geometries", source)
        self.assertIn("_restore_url_window_geometry", source)
        self.assertIn("_poll_ensure_open_urls", source)
        self.assertIn("enumerate_windows_browser_windows", source)


if __name__ == "__main__":
    unittest.main()
