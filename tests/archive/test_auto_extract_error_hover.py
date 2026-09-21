from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from repo_manager.ui.app import ProjectRepoManagerApp
from repo_manager.domain import AppConfig, Project
from repo_manager.config.repository import _project_from


class AutoExtractModelTests(unittest.TestCase):
    def test_auto_extract_defaults_off_and_round_trips_loader(self):
        self.assertFalse(Project().auto_extract_when_ready)
        self.assertTrue(_project_from({"auto_extract_when_ready": True}).auto_extract_when_ready)
        self.assertTrue(_project_from({"auto_extract": True}).auto_extract_when_ready)

    def test_ready_archive_signature_rearms_only_when_archive_changes(self):
        app = ProjectRepoManagerApp.__new__(ProjectRepoManagerApp)
        app._archive_status_by_project = {}
        app._auto_extract_attempted = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "ghost-v2-r1.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("a.txt", "x")
            project = Project(name="Ghost", match_string="ghost", downloads_dir=str(root))
            app._archive_status_by_project[project.project_id] = f"Ready to extract: {archive.name}"
            first = app._ready_archive_signature(project)
            self.assertIsNotNone(first)
            app._auto_extract_attempted[project.project_id] = first
            self.assertEqual(app._ready_archive_signature(project), first)
            # A same-name replacement has a different size and therefore a new signature.
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("a.txt", "replacement is larger")
            second = app._ready_archive_signature(project)
            self.assertNotEqual(first, second)

    def test_auto_extract_starts_once_for_same_ready_signature(self):
        app = ProjectRepoManagerApp.__new__(ProjectRepoManagerApp)
        app._closing = False
        app._worker_count = 0
        app._extracting_project_ids = set()
        app._archive_status_by_project = {}
        app._auto_extract_attempted = {}
        app._log = lambda _message: None
        calls = []
        app._extract_project = lambda project, auto_triggered: calls.append((project.project_id, auto_triggered))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "ghost-v2-r1.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("a.txt", "x")
            project = Project(
                name="Ghost",
                match_string="ghost",
                downloads_dir=str(root),
                auto_extract_when_ready=True,
            )
            app.config_data = AppConfig(projects=[project])
            app._archive_status_by_project[project.project_id] = f"Ready to extract: {archive.name}"
            app._maybe_auto_extract_ready_project()
            app._maybe_auto_extract_ready_project()
            self.assertEqual(calls, [(project.project_id, True)])



class ErrorCaptureTests(unittest.TestCase):
    def test_crap_gate_copies_command_and_all_findings(self):
        output = """\
==> python scripts/check-crap.py --max-crap 25 --unmeasured-report
CRAP gate: FAIL - 10 measured functions exceed 25.0
examples/a.rs:79 first: CC=6 coverage=0.0% CRAP=42.00
examples/b.rs:145 second: CC=6 coverage=0.0% CRAP=42.00
examples/c.rs:219 third: CC=5 coverage=0.0% CRAP=30.00
ERROR: gate failed with exit code 1: python scripts/check-crap.py --max-crap 25 --unmeasured-report
QUALITY GATES FAILED with exit code 1.
"""
        copied = ProjectRepoManagerApp._rust_error_tail(output)
        self.assertIsNotNone(copied)
        self.assertTrue(copied.startswith("==> python scripts/check-crap.py"))
        self.assertIn("CRAP gate: FAIL - 10 measured functions", copied)
        self.assertIn("examples/a.rs:79", copied)
        self.assertIn("examples/b.rs:145", copied)
        self.assertIn("examples/c.rs:219", copied)
        self.assertIn("QUALITY GATES FAILED with exit code 1.", copied)
        self.assertTrue(ProjectRepoManagerApp._output_has_decisive_command_error(output))


class HoverStabilitySourceTests(unittest.TestCase):
    def test_command_poll_does_not_reconfigure_unchanged_button_state(self):
        text = Path("repo_manager/ui/controllers/command_state.py").read_text(encoding="utf-8")
        self.assertIn("def _set_ttk_button_enabled", text)
        self.assertIn('if current != desired:', text)
        self.assertIn('self._set_ttk_button_enabled(button, has_open_window)', text)


if __name__ == "__main__":
    unittest.main()
