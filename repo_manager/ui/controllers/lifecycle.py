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

class LifecycleController:
    def _install_runtime_diagnostics(self):
        """Enable a persistent fatal-error trace without changing normal UX.

        A Tk app that simply disappears can be an out-of-memory kill or a native
        access violation in a ctypes accessibility call, neither of which reaches
        the usual messagebox error path. ``faulthandler`` gives the next such
        failure a concrete stack trace while the bounded Activity log/queue below
        prevents routine project output from growing memory without limit.
        """
        try:
            if os.name == "nt":
                base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ProjectRepoManager"
            else:
                base = Path.home() / ".local" / "state" / "project-repo-manager"
            base.mkdir(parents=True, exist_ok=True)
            handle = (base / "crash.log").open("a", encoding="utf-8", buffering=1)
            handle.write(f"\n=== Project Repo Manager start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            faulthandler.enable(file=handle, all_threads=True)
            self._fatal_log_handle = handle
        except Exception:
            self._fatal_log_handle = None

    def _restore_app_window_state(self) -> None:
        if self._closing:
            return
        try:
            if self.config_data.window_state == "zoomed":
                self.state("zoomed")
        except tk.TclError:
            pass

    def _on_app_window_configure(self, event) -> None:
        if self._closing or event.widget is not self:
            return
        try:
            state = str(self.state()).casefold()
            if state == "normal":
                geometry = self.geometry()
                if geometry:
                    self.config_data.window_geometry = geometry
            if state in {"normal", "zoomed"}:
                self.config_data.window_state = state
        except tk.TclError:
            return
        if self._window_save_after_id is not None:
            try:
                self.after_cancel(self._window_save_after_id)
            except tk.TclError:
                pass
        try:
            self._window_save_after_id = self.after(500, self._save_window_geometry_now)
        except tk.TclError:
            self._window_save_after_id = None

    def _save_window_geometry_now(self) -> None:
        self._window_save_after_id = None
        if self._closing:
            return
        self._save_config()

    def _capture_app_window_state(self) -> None:
        try:
            state = str(self.state()).casefold()
            if state == "normal":
                self.config_data.window_geometry = self.geometry()
            if state in {"normal", "zoomed"}:
                self.config_data.window_state = state
        except tk.TclError:
            pass

    def _capture_all_url_window_geometries(self) -> None:
        if os.name != "nt":
            return
        foreground = get_windows_foreground_hwnd()
        for url_id, runs in list(self._browser_runs.items()):
            found = self._url_spec_global(url_id)
            if found is None:
                continue
            _project, item = found
            live = [run for run in runs if windows_hwnd_exists(run.hwnd)]
            if not live:
                continue
            target = next((run for run in live if run.hwnd == foreground), live[-1])
            self._remember_url_window_geometry(item, target)
            self._remember_url_window_identity(item, target)

    def _runtime_housekeeping(self):
        """Drop dead runtime references so long sessions stay bounded."""
        now = time.monotonic()
        if now - self._last_runtime_housekeeping < 5.0:
            return
        self._last_runtime_housekeeping = now

        if os.name == "nt":
            # Explicit Discover URL overrides are session-scoped. Drop an
            # override only when its actual HWND is gone; background URL reads
            # are intentionally not allowed to invalidate it.
            for hwnd in list(self._manual_browser_bindings):
                if not windows_hwnd_exists(hwnd):
                    self._manual_browser_bindings.pop(hwnd, None)

            geometry_changed = False
            foreground = get_windows_foreground_hwnd()
            for url_id, runs in list(self._browser_runs.items()):
                live: list[WindowsBrowserRun] = []
                for run in runs:
                    if windows_hwnd_exists(run.hwnd):
                        live.append(run)
                    else:
                        self._chat_watch_manager.remove(run.hwnd)
                if live:
                    self._browser_runs[url_id] = live
                    found = self._url_spec_global(url_id)
                    if found is not None:
                        _project, item = found
                        target = next((run for run in live if run.hwnd == foreground), live[-1])
                        if self._remember_url_window_geometry(item, target):
                            geometry_changed = True
                        if target.browser_id and item.last_browser_id != target.browser_id:
                            item.last_browser_id = target.browser_id
                            geometry_changed = True
                else:
                    self._browser_runs.pop(url_id, None)
            if geometry_changed:
                self._save_config()

        valid_command_ids = {
            command.command_id
            for project in self.config_data.projects
            for command in project.commands
        }
        for command_id, runs in list(self._command_runs.items()):
            # Lists are kept small per command, and deleted-command history is
            # discarded once none of its managed terminals remain open.
            self._command_runs[command_id] = runs[-10:]
            if command_id not in valid_command_ids and not any(run.window_open for run in runs):
                self._command_runs.pop(command_id, None)

    def _run_worker(self, status: str, work, done=None):
        self._worker_count += 1
        self._set_status(status)
        self._set_actions_enabled(False)

        def runner():
            try:
                result = work()
            except Exception as exc:
                self._ui_queue.put(("failed", exc, None))
            else:
                self._ui_queue.put(("done", result, done))

        threading.Thread(target=runner, daemon=True).start()

    def _worker_done(self, result, callback):
        self._worker_count = max(0, self._worker_count - 1)
        if self._worker_count == 0:
            self._set_actions_enabled(True)
        self._update_download_chatgpt_button()
        if callback:
            try:
                callback(result)
            except Exception as exc:
                self._show_error("Operation completed but follow-up failed", exc)

    def _worker_failed(self, exc: Exception):
        self._worker_count = max(0, self._worker_count - 1)
        if self._worker_count == 0:
            self._set_actions_enabled(True)
        self._update_download_chatgpt_button()
        self._set_status("Operation failed")
        self._show_error("Operation failed", exc)

    def _set_actions_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for button in getattr(self, "_main_action_buttons", []):
            button.configure(state=state)
        if not enabled:
            self.download_chatgpt_btn.configure(state="disabled")
        else:
            self._update_download_chatgpt_button()
            self._update_paste_prompt_button()
            self._update_paste_note_button()

    def _set_status(self, text: str):
        self.status_var.set(text)
        self.update_idletasks()

    def _thread_log(self, text: str):
        # Extraction can emit thousands of paths much faster than Tk can paint
        # them. Never let that producer create an unbounded in-memory queue.
        try:
            self._log_queue.put_nowait(text)
        except queue.Full:
            try:
                self._log_queue.get_nowait()
            except queue.Empty:
                pass
            self._dropped_log_lines += 1
            try:
                self._log_queue.put_nowait(text)
            except queue.Full:
                pass

    def _drain_log_queue(self):
        if self._closing:
            return
        drained = 0
        while drained < MAX_LOG_DRAIN_PER_TICK:
            try:
                self._log(self._log_queue.get_nowait())
            except queue.Empty:
                break
            drained += 1
        if self._dropped_log_lines:
            dropped = self._dropped_log_lines
            self._dropped_log_lines = 0
            self._log(f"[Activity log throttled: {dropped:,} older queued line(s) discarded]")
        try:
            while True:
                kind, value, callback = self._ui_queue.get_nowait()
                if kind == "chat_event":
                    self._on_chat_event(value)
                elif kind == "browser_focus":
                    self._on_browser_focus_observation(value)
                elif kind == "browser_seen":
                    self._on_browser_seen_observation(value)
                elif kind == "browser_discovery_candidates":
                    self._auto_bind_discovery_candidates(list(value or []), reason=str(callback or "Browser"))
                elif kind == "browser_initial_reconcile_done":
                    # This queue item is emitted after every initial browser_seen
                    # item, so reaching it means _browser_runs is authoritative
                    # for the startup snapshot.
                    self._browser_initial_reconcile_complete = True
                    self._log(f"Initial browser reconciliation complete ({int(value or 0)} supported browser window candidate(s) inspected).")
                    self._ensure_missing_urls_now()
                elif kind == "ensure_url_opened":
                    self._on_ensure_url_opened(value)
                elif kind == "ensure_url_failed":
                    url_id, label, detail = value
                    if self._ensure_open_last_error.get(str(url_id)) != str(detail):
                        self._ensure_open_last_error[str(url_id)] = str(detail)
                        self._log(f"Could not restore ensured URL {label}: {detail}")
                elif kind == "ensure_batch_done":
                    self._ensure_open_worker_active = False
                elif kind == "extract_finished":
                    self._extracting_project_ids.discard(str(value))
                    self._update_project_status_cells()
                    self._update_download_status_label()
                elif kind == "chat_download_finished":
                    self._downloading_project_ids.discard(str(value))
                    self._update_project_status_cells()
                elif kind == "failed":
                    self._worker_failed(value)
                else:
                    self._worker_done(value, callback)
        except queue.Empty:
            pass
        try:
            self.after(100 if self._log_queue.empty() else 20, self._drain_log_queue)
        except tk.TclError:
            pass

    def _log(self, text: str):
        if self._closing or not hasattr(self, "log_text"):
            return
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text + "\n")
            self._activity_log_lines += max(1, text.count("\n") + 1)
            if self._activity_log_lines > MAX_ACTIVITY_LINES:
                remove = min(2_000, self._activity_log_lines - MAX_ACTIVITY_LINES + 1_000)
                self.log_text.delete("1.0", f"{remove + 1}.0")
                self._activity_log_lines = max(0, self._activity_log_lines - remove)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._activity_log_lines = 0

    def _show_error(self, title: str, exc: Exception):
        self._log(f"ERROR: {exc}")
        messagebox.showerror(title, str(exc), parent=self)

    def _save_config(self):
        try:
            save_config(self.config_data)
        except Exception as exc:
            self._log(f"ERROR saving config: {exc}")

    def _on_close(self):
        if self._closing:
            return
        self._capture_app_window_state()
        self._capture_all_url_window_geometries()
        self._closing = True
        if self._window_save_after_id is not None:
            try:
                self.after_cancel(self._window_save_after_id)
            except tk.TclError:
                pass
            self._window_save_after_id = None
        if self._autosave_after_id is not None:
            try:
                self.after_cancel(self._autosave_after_id)
            except tk.TclError:
                pass
            self._autosave_after_id = None
        self._commit_form_to_project(self._loaded_project_index)
        self._save_config()
        self._browser_discovery_stop.set()
        thread = self._browser_discovery_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        try:
            if hasattr(self, "_file_server_session"):
                self._file_server_session.stop()
            self._chat_watch_manager.stop()
        finally:
            try:
                if self._fatal_log_handle is not None:
                    self._fatal_log_handle.write(f"=== normal shutdown {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                    self._fatal_log_handle.flush()
                    faulthandler.disable()
                    self._fatal_log_handle.close()
            except Exception:
                pass
            self.destroy()

