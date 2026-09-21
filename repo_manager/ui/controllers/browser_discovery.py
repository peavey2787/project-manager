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

class BrowserDiscoveryController:
    def _start_browser_discovery(self) -> None:
        """Track manually opened supported-browser windows without touching Tk off-thread."""
        if os.name != "nt" or self._browser_discovery_thread is not None:
            return
        self._browser_discovery_stop.clear()
        self._browser_discovery_thread = threading.Thread(
            target=self._browser_discovery_loop,
            name="browser-window-discovery",
            daemon=True,
        )
        self._browser_discovery_thread.start()

    def _request_browser_reconcile(self) -> None:
        """Ask the existing discovery worker for an immediate full browser scan."""
        if os.name == "nt":
            self._browser_reconcile_requested.set()

    def _browser_discovery_loop(self) -> None:
        """Reconcile manually opened browser windows without blocking Tk.

        Startup performs an immediate full scan before the normal polling loop.
        The ``browser_initial_reconcile_done`` marker is queued *after* every
        initial ``browser_seen`` observation.  Because ``_ui_queue`` is FIFO,
        Tk cannot mark reconciliation complete until those windows have already
        been attached.  This is the barrier used by Ensure Open.

        Foreground inspection then runs quickly so project selection follows the
        user.  Every few seconds all visible supported browser windows are
        inspected so Focus/Close and runtime reconciliation continue to work.
        """
        last_focus_key: tuple[int, str] = (0, "")

        # Do not sleep before the startup scan.  The old implementation waited
        # 450 ms and then relied on a completely unrelated 3.2-second ensure-open
        # delay; a slow accessibility scan could therefore lose the race.
        self._browser_initial_reconcile_started = True
        try:
            initial_candidates = enumerate_windows_browser_discovery_rows(self._available_browsers)
            initial = [run for run in initial_candidates if run.url]
        except Exception as exc:
            self._thread_log(f"Initial browser reconciliation warning: {exc}")
            initial_candidates = []
            initial = []
        for seen in initial:
            self._ui_queue.put(("browser_seen", seen, None))
        # The manual Discover URL path deliberately retains unreadable windows.
        # Run that same stronger candidate set at startup so persisted/title
        # identity can recover dormant tabs before Ensure Open considers launch.
        self._ui_queue.put(("browser_discovery_candidates", initial_candidates, "Startup"))
        # This marker must be enqueued by this same producer after all sightings
        # and heuristic candidate reconciliation.
        self._ui_queue.put(("browser_initial_reconcile_done", len(initial_candidates), None))

        next_full_scan = time.monotonic() + 2.5
        while not self._browser_discovery_stop.wait(0.45):
            if self._closing:
                break
            try:
                hwnd = get_windows_foreground_hwnd()
                observation = inspect_windows_browser_window(hwnd, self._available_browsers) if hwnd else None
            except Exception as exc:
                self._thread_log(f"Browser discovery warning: {exc}")
                observation = None
            if observation is None:
                last_focus_key = (0, "")
            else:
                key = (int(observation.hwnd), str(observation.url))
                if key != last_focus_key:
                    last_focus_key = key
                    self._ui_queue.put(("browser_focus", observation, None))

            now = time.monotonic()
            force_full_scan = self._browser_reconcile_requested.is_set()
            if force_full_scan:
                self._browser_reconcile_requested.clear()
            if now < next_full_scan and not force_full_scan:
                continue
            next_full_scan = now + 2.5
            try:
                if force_full_scan:
                    candidates = enumerate_windows_browser_discovery_rows(self._available_browsers)
                    observations = [run for run in candidates if run.url]
                else:
                    candidates = []
                    observations = enumerate_windows_browser_windows(
                        self._available_browsers, use_uia_fallback=False
                    )
            except Exception as exc:
                self._thread_log(f"Browser reconciliation warning: {exc}")
                continue
            for seen in observations:
                # Re-send each observed window on every reconciliation pass. The
                # UI-side attach path is idempotent, and this lets a URL that was
                # just added to PRM attach to an already-open background window.
                self._ui_queue.put(("browser_seen", seen, None))
            if force_full_scan:
                self._ui_queue.put(("browser_discovery_candidates", candidates, "Forced"))

