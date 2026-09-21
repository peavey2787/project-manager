from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..commands.facade import AutoRunGroup, WindowsTerminalRun, build_auto_run_groups, close_windows_terminal, run_windows_batch_in_terminal
from ..domain import CommandSpec, Project


@dataclass
class AutoRunJob:
    project: Project
    cwd: Path
    groups: list[AutoRunGroup]
    group_index: int = 0
    current_runs: list[tuple[CommandSpec, WindowsTerminalRun]] = field(default_factory=list)
    poll_generation: int = 0


class AutoRunController:
    """Run post-extraction commands by ordered concurrent batches."""

    def _run_auto_commands(self, project: Project, commands: list[CommandSpec], cwd: Path) -> None:
        groups = build_auto_run_groups(commands)
        if not groups:
            return
        if not cwd.is_dir():
            self._show_error("Auto-run cannot start", FileNotFoundError(f"Working directory does not exist: {cwd}"))
            return
        if os.name != "nt":
            # The visible managed-terminal scheduler is Windows-specific. Keep
            # the existing portable fallback rather than inventing a hidden UI.
            ordered = [command for group in groups for command in group.commands]
            self._run_command_sequence(project, ordered, cwd, auto=True)
            return
        if project.project_id in self._auto_run_jobs:
            self._log(f"Auto-run is already active for {project.name}; duplicate start ignored.")
            return
        job = AutoRunJob(project=project, cwd=cwd, groups=groups)
        self._auto_run_jobs[project.project_id] = job
        self._start_auto_run_group(job)

    def _start_auto_run_group(self, job: AutoRunJob) -> None:
        if self._closing:
            self._auto_run_jobs.pop(job.project.project_id, None)
            return
        if job.group_index >= len(job.groups):
            self._auto_run_jobs.pop(job.project.project_id, None)
            self._set_status(f"Auto-run commands completed: {job.project.name}")
            self._log(f"Auto-run complete for {job.project.name}.")
            self._update_project_status_cells()
            return

        group = job.groups[job.group_index]
        job.current_runs.clear()
        job.poll_generation += 1
        generation = job.poll_generation
        self._log(
            f"Starting auto-run order {group.order}: "
            + ", ".join(command.label for command in group.commands)
        )
        try:
            for command in group.commands:
                run = run_windows_batch_in_terminal(command.command, job.cwd, command.label)
                if run is None:
                    raise RuntimeError("Managed Windows terminal launch unexpectedly returned no run.")
                run.completion_mode = "delay" if command.completion_mode == "delay" else "exit_code"
                run.completion_delay_seconds = max(0.1, float(command.completion_delay_seconds or 5.0))
                self._command_runs.setdefault(command.command_id, []).append(run)
                self._command_runs[command.command_id] = self._command_runs[command.command_id][-10:]
                job.current_runs.append((command, run))
                completion = (
                    f"delay {run.completion_delay_seconds:g}s"
                    if run.completion_mode == "delay"
                    else "exit code"
                )
                self._log(
                    f"Auto-run order {group.order}: started {command.label} "
                    f"(PID {run.pid}; wait for {completion})."
                )
        except Exception as exc:
            self._auto_run_jobs.pop(job.project.project_id, None)
            self._show_error("Auto-run command could not start", exc)
            return

        self._set_status(
            f"Auto-run order {group.order}: "
            + (group.commands[0].label if len(group.commands) == 1 else f"{len(group.commands)} commands running")
        )
        try:
            self._refresh_commands(preferred_id=group.commands[0].command_id)
            self._update_project_status_cells()
        except Exception as exc:
            self._log(f"Auto-run UI refresh failed after starting order {group.order}: {exc}")
        self.after(250, lambda current=job, token=generation: self._poll_auto_run_group(current, token))

    def _poll_auto_run_group(self, job: AutoRunJob, generation: int | None = None) -> None:
        if self._closing or self._auto_run_jobs.get(job.project.project_id) is not job:
            return
        if generation is not None and generation != job.poll_generation:
            return
        results: list[tuple[CommandSpec, int | None, str]] = []
        for command, run in job.current_runs:
            code = run.exit_code
            if code is not None and code != 0:
                results.append((command, int(code), "exit_code"))
                continue
            if run.completion_mode == "delay":
                if not run.delay_elapsed:
                    self._set_status(
                        f"Auto-run order {job.groups[job.group_index].order}: waiting for "
                        f"{command.label} delay ({run.completion_delay_seconds:g}s)"
                    )
                    self.after(250, lambda current=job, token=job.poll_generation: self._poll_auto_run_group(current, token))
                    return
                results.append((command, code, "delay"))
                continue
            if code is None:
                if not run.window_open:
                    code = run.process.returncode if run.process.returncode is not None else 1
                else:
                    self._set_status(
                        f"Auto-run order {job.groups[job.group_index].order}: waiting for "
                        f"{command.label} exit code"
                    )
                    self.after(250, lambda current=job, token=job.poll_generation: self._poll_auto_run_group(current, token))
                    return
            results.append((command, int(code), "exit_code"))

        failed = [(command, code) for command, code, _mode in results if code is not None and code != 0]
        for command, code, mode in results:
            if mode == "delay" and (code is None or code == 0):
                self._log(
                    f"Auto-run finished: {command.label} reached its "
                    f"{command.completion_delay_seconds:g}s completion delay."
                )
            else:
                self._log(f"Auto-run finished: {command.label} exited with code {code}.")
        if not failed:
            for command, run in job.current_runs:
                if command.auto_close_on_success and run.window_open:
                    try:
                        close_windows_terminal(run)
                        self._log(f"Auto-closed successful command window: {command.label} (PID {run.pid}).")
                    except Exception as exc:
                        self._log(f"Could not auto-close successful command {command.label}: {exc}")

        if failed:
            self._auto_run_jobs.pop(job.project.project_id, None)
            command, code = failed[0]
            self._set_status(f"Auto-run stopped: {command.label} ({code})")
            self._log(
                f"Auto-run for {job.project.name} stopped after order "
                f"{job.groups[job.group_index].order} because a command failed."
            )
            try:
                self._refresh_commands()
                self._update_project_status_cells()
            except Exception as exc:
                self._log(f"Auto-run UI refresh failed after command failure: {exc}")
            return

        completed_order = job.groups[job.group_index].order
        job.group_index += 1
        if job.group_index < len(job.groups):
            next_order = job.groups[job.group_index].order
            self._log(f"Auto-run order {completed_order} completed; advancing to order {next_order}.")
        else:
            self._log(f"Auto-run order {completed_order} completed; no later order remains.")

        # Queue the next scheduler transition before any cosmetic UI refresh.
        # A Treeview/status refresh must never be able to strand a completed
        # batch and prevent the next order from launching.
        self.after(0, lambda current=job: self._start_auto_run_group(current))
        try:
            self._refresh_commands()
            self._update_project_status_cells()
        except Exception as exc:
            self._log(f"Auto-run UI refresh failed while advancing orders: {exc}")
