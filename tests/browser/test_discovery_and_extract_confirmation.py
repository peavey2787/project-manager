from __future__ import annotations

import unittest
from pathlib import Path

from repo_manager.ui.app import ProjectRepoManagerApp
from repo_manager.domain import Project
from repo_manager.config import SCHEMA_VERSION


class BrowserUrlMatchingTests(unittest.TestCase):
    def test_exact_saved_url_matches_active_url(self) -> None:
        self.assertGreater(
            ProjectRepoManagerApp._browser_url_match_score(
                "https://chatgpt.com/c/abc", "https://chatgpt.com/c/abc"
            ),
            0,
        )

    def test_transient_query_is_ignored_when_saved_url_has_no_query(self) -> None:
        self.assertGreater(
            ProjectRepoManagerApp._browser_url_match_score(
                "https://chatgpt.com/c/abc?temporary=1", "https://chatgpt.com/c/abc"
            ),
            0,
        )

    def test_different_chat_path_does_not_match(self) -> None:
        self.assertEqual(
            ProjectRepoManagerApp._browser_url_match_score(
                "https://chatgpt.com/c/project-a", "https://chatgpt.com/c/project-b"
            ),
            0,
        )

    def test_foreground_discovery_is_single_long_lived_thread(self) -> None:
        source = (Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8"))
        self.assertIn('name="browser-window-discovery"', source)
        self.assertIn("inspect_windows_browser_window(hwnd, self._available_browsers)", source)
        self.assertIn('("browser_focus", observation, None)', source)
        self.assertIn("self._select_project_status_label(project.project_id)", source)

    def test_discovered_windows_are_usable_by_existing_focus_close_mapping(self) -> None:
        source = (Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8"))
        self.assertIn("self._browser_runs.setdefault(item.url_id, []).append(run)", source)
        self.assertIn("run.discovered = True", source)


class ExtractConfirmationTests(unittest.TestCase):
    def test_confirmation_defaults_enabled(self) -> None:
        self.assertTrue(Project().confirm_extract_newest)
        self.assertGreaterEqual(SCHEMA_VERSION, 7)

    def test_confirmation_is_conditional(self) -> None:
        source = Path("repo_manager/ui/controllers/archive.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8")
        self.assertIn("project.confirm_extract_newest and not messagebox.askyesno", source)
        self.assertIn("Show confirmation before extracting newest matching ZIP", source)


class ArchiveLabelTests(unittest.TestCase):
    def test_last_extracted_contains_metadata_and_full_filename(self) -> None:
        self.assertEqual(
            ProjectRepoManagerApp._last_extracted_label("ghost-talk-v2-r-1.zip"),
            "Last extracted: v2 r1 (ghost-talk-v2-r-1.zip)",
        )

    def test_ready_status_contains_full_filename(self) -> None:
        source = Path("repo_manager/ui/controllers/archive.py").read_text(encoding="utf-8")
        self.assertIn('return f"Ready to extract: {newest.name}"', source)


if __name__ == "__main__":
    unittest.main()
