from __future__ import annotations

import unittest

from repo_manager.ui.app import ProjectRepoManagerApp
from repo_manager.domain import AppConfig, Project, UrlSpec
from repo_manager.config import SCHEMA_VERSION


class PersistenceModelTests(unittest.TestCase):
    def test_url_defaults_do_not_force_open(self) -> None:
        item = UrlSpec()
        self.assertFalse(item.ensure_open)
        self.assertEqual(item.last_browser_id, "")
        self.assertEqual(item.window_width, 0)
        self.assertEqual(item.window_height, 0)

    def test_app_geometry_defaults(self) -> None:
        config = AppConfig()
        self.assertEqual(config.window_geometry, "1280x820")
        self.assertEqual(config.window_state, "normal")
        self.assertGreaterEqual(SCHEMA_VERSION, 8)


class ProjectMarkerTests(unittest.TestCase):
    def test_ready_project_gets_star(self) -> None:
        app = ProjectRepoManagerApp.__new__(ProjectRepoManagerApp)
        app._archive_status_by_project = {}
        project = Project(name="Ghost Talk")
        app._archive_status_by_project[project.project_id] = "Ready to extract: ghost-talk-r200.zip"
        self.assertEqual(app._project_display_name(project), "Ghost Talk *")

    def test_non_ready_project_has_plain_name(self) -> None:
        app = ProjectRepoManagerApp.__new__(ProjectRepoManagerApp)
        app._archive_status_by_project = {}
        project = Project(name="Ghost Talk")
        app._archive_status_by_project[project.project_id] = "Downloading"
        self.assertEqual(app._project_display_name(project), "Ghost Talk")


if __name__ == "__main__":
    unittest.main()
