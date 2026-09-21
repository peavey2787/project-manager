from __future__ import annotations

import queue
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from repo_manager.domain import Project
from repo_manager.ui.app import ProjectRepoManagerApp


def test_auto_stop_setting_round_trips_project_loader() -> None:
    from repo_manager.config.repository import _project_from

    assert Project().auto_stop_commands_before_extract is False
    assert _project_from({"auto_stop_commands_before_extract": True}).auto_stop_commands_before_extract is True


def test_extract_stops_managed_commands_before_clearing_destination() -> None:
    app = ProjectRepoManagerApp.__new__(ProjectRepoManagerApp)
    app._extracting_project_ids = set()
    app._auto_run_jobs = {}
    app._archive_status_by_project = {}
    app._ui_queue = queue.Queue()
    app._thread_log = lambda _text: None
    app._update_project_status_cells = lambda: None
    app._update_download_status_label = lambda: None
    app._save_config = lambda: None
    app._set_status = lambda _text: None
    app._check_snapshot_after_extract = lambda _project, _path: None
    app._run_auto_commands = lambda *_args, **_kwargs: None
    app._run_worker = lambda _status, work, done: done(work())

    events: list[str] = []
    run = SimpleNamespace(pid=4242)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "demo.zip"
        archive.write_bytes(b"zip")
        project = Project(
            name="Demo",
            match_string="demo",
            downloads_dir=str(root),
            extract_parent=str(root),
            confirm_extract_newest=False,
            auto_stop_commands_before_extract=True,
        )
        app._project_command_runs = lambda _project: [run]
        app._current_project = lambda: None
        app._auto_run_jobs[project.project_id] = object()

        with (
            mock.patch("repo_manager.ui.controllers.archive.newest_matching_zip", return_value=(archive, [])),
            mock.patch("repo_manager.ui.controllers.archive.stop_windows_terminal_and_wait", side_effect=lambda _run: events.append("stop")),
            mock.patch("repo_manager.ui.controllers.archive.clear_directory", side_effect=lambda *_a, **_k: events.append("clear")),
            mock.patch("repo_manager.ui.controllers.archive.extract_zip_safely", side_effect=lambda *_a, **_k: events.append("extract")),
        ):
            app._extract_project(project, auto_triggered=True)

    assert events[:3] == ["stop", "clear", "extract"]
    assert project.project_id not in app._auto_run_jobs
