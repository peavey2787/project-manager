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

class CommandStateController:
    @staticmethod
    def _output_has_decisive_command_error(output: str) -> bool:
        """Recognize live failure diagnostics without waiting for wrapper exit.

        Keep this intentionally anchored to diagnostic lines so ordinary prose
        containing words such as "error" does not turn a healthy run red.
        """
        ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
        clean_output = ansi.sub("", output)
        # Console capture can occasionally flatten logical lines into a single
        # space-padded row. Detect explicit failure tokens in the full capture
        # before applying the stricter line-oriented diagnostics below.
        if re.search(r"(?<![A-Za-z0-9_])ERROR:\s+", clean_output):
            return True
        if re.search(r"(?im)^.*?\.\.\.\s+(?:ERROR|FAIL)\s*$", clean_output):
            return True
        rust_error = re.compile(r"^\s*error(?:\[[A-Za-z0-9_]+\])?:\s", re.IGNORECASE)
        gate_failure = re.compile(
            r"^\s*(?:ERROR:\s*)?(?:.*\bgate failed with exit code\s+[1-9]\d*|QUALITY GATES FAILED(?:\s+with exit code\s+[1-9]\d*)?)",
            re.IGNORECASE,
        )
        gate_result_failure = re.compile(
            r"^\s*(?:CRAP\s+gate|[A-Z][A-Z0-9 _/-]*\bgate):\s*FAIL\b",
            re.IGNORECASE,
        )
        python_exception = re.compile(
            r"^\s*(?:[A-Za-z_][\w.]*?(?:Error|Exception)|Exception|KeyboardInterrupt|SystemExit):\s",
            re.IGNORECASE,
        )
        panic_or_fatal = re.compile(r"^\s*(?:thread\s+['\"].*panicked at|panic:|fatal:)\s*", re.IGNORECASE)
        for line in clean_output.splitlines():
            if (
                rust_error.match(line)
                or gate_failure.match(line)
                or gate_result_failure.match(line)
                or line.lstrip().startswith("Traceback (most recent call last):")
                or python_exception.match(line)
                or panic_or_fatal.match(line)
                or line.lstrip().startswith("Diff in ")
            ):
                return True
        return False

    def _command_run_failed(self, run: WindowsTerminalRun) -> bool:
        if run.pid in self._command_error_pids:
            return True
        code = run.exit_code
        if code is not None and code != 0:
            self._command_error_pids.add(run.pid)
            self._acknowledged_command_pids.discard(run.pid)
            return True
        return False

    def _refresh_command_error_flags(self) -> None:
        """Detect errors from live captured output while commands are running."""
        unique: dict[int, WindowsTerminalRun] = {}
        for runs in self._command_runs.values():
            for run in runs:
                if run.window_open:
                    unique[run.pid] = run
        for run in unique.values():
            if run.pid in self._command_error_pids:
                continue
            code = run.exit_code
            if code is not None and code != 0:
                self._command_error_pids.add(run.pid)
                self._acknowledged_command_pids.discard(run.pid)
                continue
            try:
                # Reading the tail is sufficient for first detection; once a
                # failure is seen the PID stays cached until its terminal closes.
                with run.output_path.open("rb") as handle:
                    handle.seek(0, 2)
                    size = handle.tell()
                    handle.seek(max(0, size - 262144))
                    text = handle.read().decode("utf-8", errors="replace")
            except OSError:
                continue
            if self._output_has_decisive_command_error(text):
                self._command_error_pids.add(run.pid)
                # A newly observed failure is a new attention event even if
                # the user acknowledged this PID earlier while it was healthy.
                self._acknowledged_command_pids.discard(run.pid)

    def _command_window_status(self, command_id: str) -> str:
        runs = self._command_runs.get(command_id, [])
        open_runs = [run for run in runs if run.window_open]
        if not open_runs:
            return ""
        failed = sum(1 for run in open_runs if self._command_run_failed(run))
        active = sum(1 for run in open_runs if not run.command_finished and not self._command_run_failed(run))
        finished = len(open_runs) - active - failed
        if failed:
            if len(open_runs) == 1:
                return "Error - window open"
            parts = [f"Error {failed}"]
            if active:
                parts.append(f"running {active}")
            if finished:
                parts.append(f"finished {finished}")
            return " / ".join(parts)
        if active and finished:
            return f"Running {active} / finished {finished}"
        if active == 1:
            return "Running"
        if active > 1:
            return f"Running ×{active}"
        if finished == 1:
            return "Finished - window open"
        return f"Finished - {finished} windows open"

    def _latest_command_run(self, command_id: str, *, open_only: bool = False) -> WindowsTerminalRun | None:
        for run in reversed(self._command_runs.get(command_id, [])):
            if not open_only or run.window_open:
                return run
        return None

    @staticmethod
    def _set_ttk_button_enabled(button: ttk.Button | None, enabled: bool) -> None:
        """Change a ttk button state only when the logical state changes.

        Re-applying ``state=normal`` every 500 ms resets the native ttk hover
        state on Windows, which made buttons visibly flash while the pointer was
        stationary. Avoid touching the widget when it is already enabled/disabled.
        """
        if button is None:
            return
        desired = "normal" if enabled else "disabled"
        try:
            current = str(button.cget("state"))
        except tk.TclError:
            return
        if current != desired:
            button.configure(state=desired)

    def _update_command_action_buttons(self) -> None:
        """Keep command-window controls synchronized with the actual HWND/process.

        A managed CMD/Windows Terminal can be closed manually, outside this
        application. The polling loop calls this method every 500 ms so stale
        Focus/Copy/Close controls become disabled without requiring a click.
        """
        buttons = getattr(self, "command_action_buttons", {})
        spec = self._selected_command() if hasattr(self, "commands_tree") else None
        has_selection = spec is not None
        open_run = self._latest_command_run(spec.command_id, open_only=True) if spec else None
        has_open_window = open_run is not None

        for name in ("Edit", "Remove", "Run Now", "Copy Command", "Move Up", "Move Down"):
            button = buttons.get(name)
            self._set_ttk_button_enabled(button, has_selection)

        # Window/output controls require a live managed terminal. Copy Command
        # is intentionally selection-scoped instead because it copies the saved
        # Command / Script value and does not depend on a running process.
        for name in ("Focus", "Copy Output", "Copy Error Output", "Close"):
            button = buttons.get(name)
            self._set_ttk_button_enabled(button, has_open_window)

        if hasattr(self, "close_all_commands_btn"):
            any_open = any(run.window_open for runs in self._command_runs.values() for run in runs)
            self._set_ttk_button_enabled(self.close_all_commands_btn, any_open)

    def _poll_command_windows(self):
        if self._closing or not self.winfo_exists():
            return

        # Resolve/cache each visible managed terminal HWND as soon as Windows
        # creates it. Windows Terminal may decorate the title, so the resolver
        # matches the run's unique token rather than requiring exact text.
        for runs in self._command_runs.values():
            for run in runs:
                if run.window_open and not run.hwnd:
                    try:
                        resolve_windows_terminal_window(run)
                    except Exception:
                        pass

        live_pids = {
            run.pid
            for runs in self._command_runs.values()
            for run in runs
            if run.window_open
        }
        self._acknowledged_command_pids.intersection_update(live_pids)
        self._command_error_pids.intersection_update(live_pids)
        self._refresh_command_error_flags()

        if hasattr(self, "commands_tree"):
            project = self._current_project()
            if project:
                for command in project.commands:
                    if self.commands_tree.exists(command.command_id):
                        values = list(self.commands_tree.item(command.command_id, "values"))
                        if len(values) >= 4:
                            status = self._command_window_status(command.command_id)
                            if values[2] != status:
                                values[2] = status
                                self.commands_tree.item(command.command_id, values=values)
        self._update_command_action_buttons()
        self._update_project_status_cells()
        try:
            self.after(500, self._poll_command_windows)
        except tk.TclError:
            pass

    def _active_project_command_run(self, project: Project) -> tuple[CommandSpec, WindowsTerminalRun] | None:
        """Return the command currently executing in the newest active window.

        Auto-run sequences share one terminal across several CommandSpec rows.
        When possible, inspect the captured terminal output and select the row
        whose ``===== label =====`` header appeared most recently.
        """
        runs_to_commands: dict[int, tuple[WindowsTerminalRun, list[CommandSpec]]] = {}
        for command in project.commands:
            for run in self._command_runs.get(command.command_id, []):
                if not run.window_open:
                    continue
                entry = runs_to_commands.setdefault(run.pid, (run, []))
                if all(existing.command_id != command.command_id for existing in entry[1]):
                    entry[1].append(command)
        if not runs_to_commands:
            return None
        ordered = sorted(
            runs_to_commands.values(),
            key=lambda entry: (0 if entry[0].command_finished else 1, float(entry[0].started_at)),
            reverse=True,
        )
        run, commands = ordered[0]
        if not commands:
            return None
        if not run.command_finished and len(commands) > 1:
            try:
                output = read_windows_terminal_output(run) or ""
            except Exception:
                output = ""
            if output:
                ranked = [
                    (output.rfind(f"===== {command.label} ====="), command)
                    for command in commands
                ]
                ranked.sort(key=lambda row: row[0], reverse=True)
                if ranked and ranked[0][0] >= 0:
                    return ranked[0][1], run
        return commands[-1 if run.command_finished else 0], run

    def _project_command_runs(self, project: Project, *, running_only: bool = False) -> list[WindowsTerminalRun]:
        command_ids = {command.command_id for command in project.commands}
        unique: dict[int, WindowsTerminalRun] = {}
        for command_id in command_ids:
            for run in self._command_runs.get(command_id, []):
                if not run.window_open:
                    continue
                if running_only and run.command_finished:
                    continue
                unique[run.pid] = run
        return list(unique.values())

