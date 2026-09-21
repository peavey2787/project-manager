from __future__ import annotations

from types import SimpleNamespace

import repo_manager.ui.auto_run as auto_run_module
from repo_manager.commands.scheduling import build_auto_run_groups
from repo_manager.domain import CommandSpec, Project
from repo_manager.ui.auto_run import AutoRunController, AutoRunJob


class _FakeRun:
    _pid = 1000

    def __init__(self) -> None:
        type(self)._pid += 1
        self.pid = type(self)._pid
        self.completion_mode = "exit_code"
        self.completion_delay_seconds = 5.0
        self.exit_code = 0
        self.delay_elapsed = True
        self.window_open = True
        self.process = SimpleNamespace(returncode=0)


class _Harness(AutoRunController):
    def __init__(self, project: Project, *, fail_cosmetic_refresh: bool = False) -> None:
        self._closing = False
        self._auto_run_jobs = {}
        self._command_runs = {}
        self._callbacks = []
        self.logs = []
        self.statuses = []
        self.fail_cosmetic_refresh = fail_cosmetic_refresh
        self.project = project

    def after(self, _delay, callback):
        self._callbacks.append(callback)

    def _log(self, message):
        self.logs.append(message)

    def _set_status(self, message):
        self.statuses.append(message)

    def _refresh_commands(self, preferred_id=None):
        if self.fail_cosmetic_refresh and preferred_id is None:
            raise RuntimeError("synthetic Treeview refresh failure")

    def _update_project_status_cells(self):
        return None

    def _show_error(self, title, exc):
        raise AssertionError(f"{title}: {exc}")

    def drain(self, limit=100):
        for _ in range(limit):
            if not self._callbacks:
                return
            callback = self._callbacks.pop(0)
            try:
                callback()
            except RuntimeError as exc:
                if "synthetic Treeview refresh failure" not in str(exc):
                    raise
        raise AssertionError("scheduler callback queue did not drain")


def test_exact_0_1_1_2_schedule_always_reaches_final_order(monkeypatch, tmp_path) -> None:
    commands = [
        CommandSpec(label="Zero", command="zero", auto_run=True, auto_run_order=0),
        CommandSpec(label="One A", command="one-a", auto_run=True, auto_run_order=1),
        CommandSpec(label="One B", command="one-b", auto_run=True, auto_run_order=1),
        CommandSpec(label="Two", command="two", auto_run=True, auto_run_order=2),
    ]
    launched = []

    def fake_launch(command, _cwd, label):
        launched.append((label, command))
        return _FakeRun()

    monkeypatch.setattr(auto_run_module, "run_windows_batch_in_terminal", fake_launch)
    project = Project(name="Demo", commands=commands)
    groups = build_auto_run_groups(commands)
    harness = _Harness(project)
    job = AutoRunJob(project=project, cwd=tmp_path, groups=groups)
    harness._auto_run_jobs[project.project_id] = job

    harness._start_auto_run_group(job)
    harness.drain()

    assert [label for label, _command in launched] == ["Zero", "One A", "One B", "Two"]
    assert project.project_id not in harness._auto_run_jobs
    assert any("advancing to order 2" in line for line in harness.logs)


def test_cosmetic_refresh_failure_cannot_strand_next_order(monkeypatch, tmp_path) -> None:
    commands = [
        CommandSpec(label="Zero", command="zero", auto_run=True, auto_run_order=0),
        CommandSpec(label="Two", command="two", auto_run=True, auto_run_order=2),
    ]
    launched = []

    def fake_launch(command, _cwd, label):
        launched.append(label)
        return _FakeRun()

    monkeypatch.setattr(auto_run_module, "run_windows_batch_in_terminal", fake_launch)
    project = Project(name="Demo", commands=commands)
    harness = _Harness(project, fail_cosmetic_refresh=True)
    job = AutoRunJob(project=project, cwd=tmp_path, groups=build_auto_run_groups(commands))
    harness._auto_run_jobs[project.project_id] = job

    harness._start_auto_run_group(job)
    harness.drain()

    assert launched == ["Zero", "Two"]


def test_successful_auto_run_closes_only_commands_opted_in(monkeypatch, tmp_path) -> None:
    commands = [
        CommandSpec(label="Close Me", command="close-me", auto_run=True, auto_run_order=0, auto_close_on_success=True),
        CommandSpec(label="Keep Me", command="keep-me", auto_run=True, auto_run_order=0, auto_close_on_success=False),
    ]
    runs_by_label = {}
    closed = []

    def fake_launch(_command, _cwd, label):
        run = _FakeRun()
        runs_by_label[label] = run
        return run

    def fake_close(run):
        closed.append(run.pid)
        run.window_open = False

    monkeypatch.setattr(auto_run_module, "run_windows_batch_in_terminal", fake_launch)
    monkeypatch.setattr(auto_run_module, "close_windows_terminal", fake_close)
    project = Project(name="Demo", commands=commands)
    harness = _Harness(project)
    job = AutoRunJob(project=project, cwd=tmp_path, groups=build_auto_run_groups(commands))
    harness._auto_run_jobs[project.project_id] = job

    harness._start_auto_run_group(job)
    harness.drain()

    assert closed == [runs_by_label["Close Me"].pid]
    assert runs_by_label["Keep Me"].window_open is True
