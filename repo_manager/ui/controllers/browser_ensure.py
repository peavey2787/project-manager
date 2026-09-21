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

class BrowserEnsureController:
    def _exhaustive_browser_preflight(self) -> tuple[list[WindowsBrowserRun], list[WindowsBrowserRun]]:
        """Passively inspect existing browser HWNDs before Ensure Open.

        Background discovery must never steal focus or activate a browser window.
        A supported browser HWND whose URL cannot be read passively is returned in
        ``unresolved``.  Ensure Open treats that as uncertainty and defers rather
        than risking a duplicate.  Only explicit user actions such as the Focus
        buttons are allowed to bring another window to the foreground.
        """
        candidates = enumerate_windows_browser_window_candidates(self._available_browsers)
        if not candidates:
            return [], []

        if self._closing or self._browser_discovery_stop.is_set():
            return [], candidates
        return resolve_windows_browser_window_candidates(
            candidates, self._available_browsers, use_uia_fallback=True
        )

    def _ensure_missing_urls_now(self) -> None:
        if self._closing or os.name != "nt" or self._ensure_open_worker_active:
            return
        # Never infer "missing" until startup browser reconciliation has been
        # completed and consumed by the Tk thread.
        if not self._browser_initial_reconcile_complete:
            return
        pending: list[tuple[str, str, str, str, WindowsBrowserSpec]] = []
        for project in self.config_data.projects:
            for item in self._project_url_specs(project):
                if not item.ensure_open:
                    continue
                live = [run for run in self._browser_runs.get(item.url_id, []) if windows_hwnd_exists(run.hwnd)]
                if live:
                    continue
                browser = self._preferred_browser_for_url(item)
                if browser is None:
                    continue
                pending.append((project.project_id, item.url_id, item.label, self._normalized_url_value(item), browser))
        if not pending:
            return
        self._ensure_open_worker_active = True

        def worker():
            try:
                # Defensive passive reconciliation immediately before any
                # launch. Unlike the ordinary readable-URL scan, this also keeps
                # supported browser HWNDs whose address bar is temporarily
                # unreadable. It never activates or focuses those windows.
                try:
                    preflight_seen, unresolved = self._exhaustive_browser_preflight()
                except Exception as exc:
                    self._thread_log(f"Ensure-open exhaustive browser preflight warning: {exc}")
                    preflight_seen, unresolved = [], []
                for seen in preflight_seen:
                    self._ui_queue.put(("browser_seen", seen, None))

                for project_id, url_id, label, value, browser in pending:
                    if self._browser_discovery_stop.is_set() or self._closing:
                        break
                    # _browser_runs is the authoritative runtime state used by
                    # the URLs tab's Focus/Close buttons.  Re-check it *again*
                    # here because a foreground/manual discovery can attach the
                    # real window after ``pending`` was built but before this
                    # worker reaches the launch.  The old code ignored that
                    # state and could launch a duplicate even while Focus worked.
                    attached = [
                        candidate
                        for candidate in self._browser_runs.get(url_id, [])
                        if windows_hwnd_exists(candidate.hwnd)
                    ]
                    if attached:
                        self._thread_log(f"Ensure-open: attached window already exists, not launching duplicate: {label}")
                        continue
                    # Use the fresh OS/browser observations directly rather than
                    # waiting for Tk to drain them.  If the saved URL is already
                    # open, this item is definitively not missing.
                    if any(self._browser_url_match_score(seen.url, value) > 0 for seen in preflight_seen):
                        self._thread_log(f"Ensure-open: already open, not launching duplicate: {label}")
                        continue

                    # Never equate an unreadable background browser HWND with an
                    # absent URL. If a supported window for the browser we would
                    # use cannot expose its address bar passively, defer this
                    # ensure pass. The normal foreground watcher will attach it
                    # if/when the user focuses it. A delayed restore is preferable
                    # to either stealing focus or creating an unwanted duplicate.
                    same_browser_unresolved = [
                        candidate for candidate in unresolved
                        if candidate.browser_id == browser.browser_id and windows_hwnd_exists(candidate.hwnd)
                    ]
                    if same_browser_unresolved:
                        self._thread_log(
                            f"Ensure-open: deferred {label}; {len(same_browser_unresolved)} existing "
                            f"{browser.name} window(s) have not exposed their URL yet."
                        )
                        continue
                    try:
                        run = launch_windows_browser(value, label=label, browser=browser)
                    except Exception as exc:
                        self._ui_queue.put(("ensure_url_failed", (url_id, label, str(exc)), None))
                    else:
                        self._ui_queue.put(("ensure_url_opened", (project_id, url_id, run), None))
                        # Include the new run in this batch's known-open set so a
                        # second saved entry for the same URL cannot duplicate it.
                        preflight_seen.append(run)
            finally:
                self._ui_queue.put(("ensure_batch_done", None, None))

        threading.Thread(target=worker, name="ensure-open-urls", daemon=True).start()

    def _poll_ensure_open_urls(self) -> None:
        if self._closing:
            return
        try:
            self._ensure_missing_urls_now()
        except Exception as exc:
            self._log(f"Ensure-open URL warning: {exc}")
        try:
            # While startup reconciliation is still running, check again soon;
            # after it completes the normal five-second maintenance cadence is
            # sufficient.
            delay = 5000 if self._browser_initial_reconcile_complete else 250
            self.after(delay, self._poll_ensure_open_urls)
        except tk.TclError:
            pass

    def _on_ensure_url_opened(self, payload) -> None:
        project_id, url_id, run = payload
        project = self._project_by_id(str(project_id))
        found = self._url_spec_global(str(url_id))
        if project is None or found is None:
            try:
                close_windows_browser(run)
            except Exception:
                pass
            return
        _owner, item = found
        existing = [candidate for candidate in self._browser_runs.get(item.url_id, []) if windows_hwnd_exists(candidate.hwnd)]
        if existing:
            # A manual/background discovery won the race while ensure-open was
            # launching. Keep the already-open window and close the duplicate.
            try:
                close_windows_browser(run)
            except Exception:
                pass
            return
        self._restore_url_window_geometry(item, run)
        self.after(300, lambda item=item, run=run: self._restore_url_window_geometry(item, run) if windows_hwnd_exists(run.hwnd) else None)
        self._browser_runs.setdefault(item.url_id, []).append(run)
        self._recent_ensured_launches[item.url_id] = (int(run.hwnd), time.monotonic())
        self._remember_url_window_identity(item, run)
        self._ensure_open_last_error.pop(item.url_id, None)
        if item.watch_chat and self._is_chatgpt_url_value(item.url):
            self._chat_watch_manager.add(project.project_id, item.url_id, run.hwnd, item.label, self._normalized_url_value(item))
        self._save_config()
        if self._current_project() is not None and self._current_project().project_id == project.project_id:
            self._refresh_urls()
        self._update_project_status_cells()
        self._update_download_chatgpt_button()
        self._log(f"Restored ensured URL: {item.label} in {run.browser_name}")

