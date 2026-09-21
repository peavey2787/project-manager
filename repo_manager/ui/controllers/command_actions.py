from __future__ import annotations

import faulthandler
import os
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from tkinter import filedialog, messagebox, simpledialog, ttk

from ...archive.facade import *
from ...browser.facade import *
from ...chat import ChatEvent, ChatState, ChatWatchManager
from ...commands.facade import *
from ...config import config_path, save_config
from ...domain import AppConfig, CommandSpec, Project, UrlGroup, UrlSpec
from ...platform.windows.desktop import open_folder, open_in_vscode
from ..constants import *
from ..dialogs.command import CommandDialog
from ..dialogs.url import UrlDialog
from ..dialogs.misc import MultiSelectDialog, SnapshotDiffDialog
from ..widgets import ScrollableFrame

class CommandActionsController:
    def _run_selected_command(self):
        project = self._validate_project_settings()
        index = self._selected_command_index()
        if not project or index is None:
            return
        spec = project.commands[index]
        cwd = project.working_dir.resolve()
        if not cwd.is_dir():
            self._show_error("Command cannot run", FileNotFoundError(f"Working directory does not exist: {cwd}"))
            return

        # Manual Windows batch files get a real console window.  This makes
        # interactive scripts, PAUSE, prompts, and very short failures visible
        # instead of leaving the GUI apparently stuck on "Running command…".
        try:
            terminal_run = run_windows_batch_in_terminal(spec.command, cwd, spec.label)
        except Exception as exc:
            self._show_error("Command could not start", exc)
            return
        if terminal_run is not None:
            self._command_runs.setdefault(spec.command_id, []).append(terminal_run)
            self._command_runs[spec.command_id] = self._command_runs[spec.command_id][-10:]
            self._log(f"=== Run command: {project.name} ===")
            self._log(f"$ {spec.label}: {spec.command}")
            self._log(f"Working directory: {cwd}")
            self._log(f"Started managed Windows command terminal, PID {terminal_run.pid}.")
            self._set_status(f"Started command: {spec.label}")
            self._refresh_commands(preferred_id=spec.command_id)
            if spec.auto_close_on_success:
                self.after(250, lambda command=spec, run=terminal_run: self._poll_manual_auto_close(command, run))
            return

        self._run_command_sequence(project, [spec], cwd, auto=False)

    def _poll_manual_auto_close(self, command: CommandSpec, run: WindowsTerminalRun) -> None:
        if self._closing or not run.window_open:
            return
        if not run.command_finished or run.exit_code is None:
            self.after(250, lambda: self._poll_manual_auto_close(command, run))
            return
        if run.exit_code != 0:
            return
        try:
            close_windows_terminal(run)
            self._log(f"Auto-closed successful command window: {command.label} (PID {run.pid}).")
            self._set_status(f"Auto-closed successful command: {command.label}")
            self.after(350, lambda: self._refresh_commands(preferred_id=command.command_id))
        except Exception as exc:
            self._log(f"Could not auto-close successful command {command.label}: {exc}")

    def _focus_selected_command_window(self):
        spec = self._selected_command()
        if not spec:
            return
        run = self._latest_command_run(spec.command_id, open_only=True)
        if run is None:
            self._update_command_action_buttons()
            return
        try:
            focus_windows_terminal(run)
            self._set_status(f"Focused command: {spec.label}")
        except Exception as exc:
            self._show_error("Could not focus command window", exc)

    def _copy_selected_command_output(self):
        spec = self._selected_command()
        if not spec:
            return
        run = self._latest_command_run(spec.command_id, open_only=True)
        if run is None:
            self._update_command_action_buttons()
            return
        try:
            output = read_windows_terminal_output(run)
        except Exception as exc:
            self._show_error("Could not read command output", exc)
            return
        if not output:
            messagebox.showinfo("No command output", "No output has been captured from this command yet.", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(output)
        self.update_idletasks()
        self._set_status(f"Copied output: {spec.label} ({len(output):,} characters)")

    def _read_command_error_output(self, run: WindowsTerminalRun) -> str | None:
        output = read_windows_terminal_output(run)
        if not output:
            return ""
        return self._rust_error_tail(output)

    def _copy_selected_command_error_output(self):
        spec = self._selected_command()
        if not spec:
            return
        run = self._latest_command_run(spec.command_id, open_only=True)
        if run is None:
            self._update_command_action_buttons()
            return
        try:
            error_output = self._read_command_error_output(run)
        except Exception as exc:
            self._show_error("Could not read command output", exc)
            return
        if error_output == "":
            messagebox.showinfo("No command output", "No output has been captured from this command yet.", parent=self)
            return
        if error_output is None:
            messagebox.showinfo(
                "No error output found",
                "No recognizable error output was found yet. Expected a failing QA gate, traceback, exception, Rust/Cargo error, panic/fatal line, or cargo fmt Diff output.",
                parent=self,
            )
            return
        self.clipboard_clear()
        self.clipboard_append(error_output)
        self.update_idletasks()
        self._set_status(f"Copied error output: {spec.label} ({len(error_output):,} characters)")

    @staticmethod
    def _rust_error_tail(output: str) -> str | None:
        """Return the first recognizable failure through EOF.

        The copied error payload is intentionally *not* just the last traceback
        or final wrapper line.  Find the earliest actionable failure marker and
        preserve everything after it so later failures, summaries, resume hints,
        exit codes, and the managed-terminal footer stay available to ChatGPT.

        Two diagnostics need a little earlier context: ``cargo fmt -- --check``
        should include its command plus every ``Diff in ...`` block, and explicit
        QA gate findings should include the command that launched that gate.
        """
        ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
        clean = ansi.sub("", output)
        if not clean:
            return None

        # cargo fmt prints actionable Diff blocks before its eventual ERROR.
        first_diff = re.search(r"(?im)^\s*Diff in ", clean)
        if first_diff is not None:
            preceding = clean[: first_diff.start()]
            fmt_matches = list(
                re.finditer(r"(?im)^.*\bcargo(?:\s+\+\S+)?\s+fmt\b.*--check.*$", preceding)
            )
            start = fmt_matches[-1].start() if fmt_matches else first_diff.start()
            return clean[start:]

        # Explicit gate failures can have the useful offender list before the
        # generic ERROR wrapper. Include the immediately preceding ==> command.
        gate = re.search(
            r"(?im)^\s*(?:(?:CRAP\s+gate|[A-Z][A-Z0-9 _/-]*\bgate):\s*FAIL\b|Required command not found:)",
            clean,
        )
        if gate is not None:
            preceding = clean[: gate.start()]
            command_matches = list(re.finditer(r"(?im)^\s*==>.*$", preceding))
            start = command_matches[-1].start() if command_matches else gate.start()
            return clean[start:]

        # General case: choose the *earliest* failure signal. Patterns are
        # deliberately allowed away from column zero because captured console
        # output can occasionally flatten line breaks into runs of spaces.
        patterns = (
            # unittest/runner progress, e.g. "test_name ... ERROR".
            re.compile(r"(?im)^.*?\.\.\.\s+(?:ERROR|FAIL)\s*$"),
            # Python unittest summary headings and generic tool wrappers.
            re.compile(r"(?<![A-Za-z0-9_])ERROR:\s+"),
            # Rust/cargo diagnostics (case-insensitive for wrappers).
            re.compile(r"(?i)(?<![A-Za-z0-9_])error(?:\[[A-Za-z0-9_]+\])?:\s+"),
            re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
            # Exception terminal lines.
            re.compile(
                r"(?im)^\s*(?:[A-Za-z_][\w.]*?(?:Error|Exception)|Exception|KeyboardInterrupt|SystemExit):\s"
            ),
            re.compile(r"(?i)(?<![A-Za-z0-9_])(?:panic:|fatal:)\s*"),
            re.compile(r"(?i)thread\s+['\"].*?panicked at"),
            # Common test/build failure summaries when no earlier ERROR exists.
            re.compile(r"(?im)^\s*FAIL:\s+"),
            re.compile(r"(?im)^\s*FAILED(?:\s*\(|\s+TEST\s+SUMMARY|\s*$)"),
            re.compile(r"(?im)^\s*make:\s+\*\*\*"),
        )
        starts = [match.start() for pattern in patterns if (match := pattern.search(clean)) is not None]
        if not starts:
            return None
        return clean[min(starts):]

    def _close_selected_command_window(self):
        spec = self._selected_command()
        if not spec:
            return
        run = self._latest_command_run(spec.command_id, open_only=True)
        if run is None:
            self._update_command_action_buttons()
            return
        try:
            close_windows_terminal(run)
            self._log(f"Close requested for command window: {spec.label} (PID {run.pid})")
            self._set_status(f"Closing command: {spec.label}")
            self._update_command_action_buttons()
            self.after(350, self._refresh_commands)
        except Exception as exc:
            self._show_error("Could not close command window", exc)

    def _close_all_command_windows(self):
        # One auto-run terminal can represent several commands.  Deduplicate by
        # process id so Close All counts and closes each actual window once.
        unique_runs = {}
        for runs in self._command_runs.values():
            for run in runs:
                if run.window_open:
                    unique_runs[run.pid] = run
        open_runs = list(unique_runs.values())
        if not open_runs:
            self._set_status("No running command windows")
            return
        if not messagebox.askyesno(
            "Close all running commands",
            f"Close {len(open_runs)} managed command window(s)?\n\nAny command still running in those windows will be stopped.",
            parent=self,
        ):
            return
        failures = []
        for run in open_runs:
            try:
                close_windows_terminal(run)
            except Exception as exc:
                failures.append(str(exc))
        self._log(f"Close requested for {len(open_runs) - len(failures)} managed command window(s).")
        if failures:
            messagebox.showwarning(
                "Some windows could not be closed",
                "\n".join(failures[:5]),
                parent=self,
            )
        self._set_status("Closing all managed command windows")
        self.after(350, self._refresh_commands)

    def _select_active_command_for_current_project(self) -> None:
        project = self._current_project()
        if project is None or not hasattr(self, "commands_tree"):
            return
        active = self._active_project_command_run(project)
        if active is not None:
            command, _run = active
            self._refresh_commands(preferred_id=command.command_id)
            return

        # Entering the Commands tab should not present a wall of disabled-looking
        # controls when commands already exist.  With no active terminal, select
        # the first saved command so Edit/Remove/Run/Copy/Reorder are immediately
        # available.  Window-only actions remain disabled until a terminal exists.
        if project.commands:
            self._refresh_commands(preferred_id=project.commands[0].command_id)
        else:
            self._refresh_commands()

    def _active_project_command_output(self) -> tuple[CommandSpec, str] | None:
        project = self._current_project()
        active = self._active_project_command_run(project) if project else None
        if active is None:
            self._set_status("No active command window is available for this project")
            return None
        command, run = active
        self._refresh_commands(preferred_id=command.command_id)
        try:
            output = read_windows_terminal_output(run)
        except Exception as exc:
            self._show_error("Could not read command output", exc)
            return None
        if not output:
            messagebox.showinfo("No command output", "No output has been captured from this command yet.", parent=self)
            return None
        return command, output

    def _copy_active_project_command_output(self) -> None:
        result = self._active_project_command_output()
        if result is None:
            return
        command, output = result
        self.clipboard_clear()
        self.clipboard_append(output)
        self.update_idletasks()
        self._set_status(f"Copied output: {command.label} ({len(output):,} characters)")

    def _active_project_command_error_output(self) -> tuple[CommandSpec, str] | None:
        project = self._current_project()
        active = self._active_project_command_run(project) if project else None
        if active is None:
            self._set_status("No active command window is available for this project")
            return None
        command, run = active
        self._refresh_commands(preferred_id=command.command_id)
        try:
            error_output = self._read_command_error_output(run)
        except Exception as exc:
            self._show_error("Could not read command output", exc)
            return None
        if error_output == "":
            messagebox.showinfo("No command output", "No output has been captured from this command yet.", parent=self)
            return None
        if error_output is None:
            messagebox.showinfo(
                "No error output found",
                "No recognizable error output was found yet. Expected a failing QA gate, traceback, exception, Rust/Cargo error, panic/fatal line, or cargo fmt Diff output.",
                parent=self,
            )
            return None
        return command, error_output

    def _copy_active_project_command_error_output(self) -> None:
        result = self._active_project_command_error_output()
        if result is None:
            return
        command, error_output = result
        self.clipboard_clear()
        self.clipboard_append(error_output)
        self.update_idletasks()
        self._set_status(f"Copied error output: {command.label} ({len(error_output):,} characters)")


    def _stop_project_commands(self):
        project = self._current_project()
        if not project:
            return
        runs = self._project_command_runs(project)
        if not runs:
            self._set_status(f"No managed command windows are open for {project.name}")
            return
        if not messagebox.askyesno(
            "Close all project commands",
            f"Close {len(runs)} managed command window(s) for {project.name}?\n\nAny command still running will be stopped.",
            parent=self,
        ):
            return
        failures = []
        for run in runs:
            try:
                close_windows_terminal(run)
            except Exception as exc:
                failures.append(str(exc))
        self._set_status(f"Closing {len(runs) - len(failures)} command window(s) for {project.name}")
        if failures:
            messagebox.showwarning("Some command windows could not be closed", "\n".join(failures[:5]), parent=self)
        self.after(350, self._refresh_commands)
        self.after(400, self._update_project_status_cells)

    def _focus_project_commands(self):
        project = self._current_project()
        if not project:
            return
        runs = self._project_command_runs(project, running_only=True)
        if not runs:
            self._set_status(f"No running managed commands for {project.name}")
            return
        failures = 0
        for run in runs:
            try:
                focus_windows_terminal(run)
            except Exception:
                failures += 1
        self._set_status(f"Focused {len(runs) - failures}/{len(runs)} running command window(s) for {project.name}")

    def _run_command_sequence(self, project: Project, commands: list[CommandSpec], cwd: Path, auto: bool):
        if not cwd.is_dir():
            self._show_error("Command cannot run", FileNotFoundError(f"Working directory does not exist: {cwd}"))
            return

        # Manual commands belong in a real managed terminal window on Windows.
        # Ordered post-extraction auto-runs use AutoRunController so commands
        # sharing an order can run concurrently; this method remains the manual
        # path and the portable non-Windows fallback.
        try:
            terminal_run = run_windows_commands_in_terminal(
                [(spec.label, spec.command) for spec in commands],
                cwd,
                f"{project.name} - {'Auto-run' if auto else 'Command'}",
            )
        except Exception as exc:
            self._show_error("Command could not start", exc)
            return

        if terminal_run is not None:
            for spec in commands:
                self._command_runs.setdefault(spec.command_id, []).append(terminal_run)
                self._command_runs[spec.command_id] = self._command_runs[spec.command_id][-10:]
            self._log(f"=== {'Auto-run' if auto else 'Run'} terminal: {project.name} ===")
            for spec in commands:
                self._log(f"$ {spec.label}: {spec.command}")
            self._log(f"Working directory: {cwd}")
            self._log(f"Started managed Windows command terminal, PID {terminal_run.pid}.")
            self._set_status(
                f"Started {'auto-run' if auto else 'command'} terminal: "
                + (commands[0].label if len(commands) == 1 else f"{len(commands)} commands")
            )
            self._refresh_commands(preferred_id=commands[0].command_id if commands else None)
            return

        def work():
            self._thread_log(f"=== {'Auto-run' if auto else 'Run'} commands: {project.name} ===")
            results: list[tuple[str, int]] = []
            for spec in commands:
                self._thread_log(f"$ {spec.label}: {spec.command}")
                code = run_command_streaming(spec.command, cwd, self._thread_log)
                results.append((spec.label, code))
                self._thread_log(f"[{spec.label}] exit code {code}")
                if code != 0 and auto:
                    self._thread_log("Auto-run sequence stopped because a command failed.")
                    break
            return results

        def done(results):
            failed = [(label, code) for label, code in results if code != 0]
            if failed:
                self._set_status(f"Command failed: {failed[0][0]} ({failed[0][1]})")
                self.notebook.select(self.activity_tab)
                messagebox.showwarning("Command finished with error", f"{failed[0][0]} exited with code {failed[0][1]}.\nSee Activity for output.", parent=self)
            else:
                self._set_status("Command(s) completed")

        self._run_worker("Running command…", work, done)

