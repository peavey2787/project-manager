from __future__ import annotations
import os
import queue
import threading
import tkinter as tk
from ..browser.facade import WindowsBrowserRun, WindowsBrowserSpec, detect_windows_browsers
from ..chat import ChatWatchManager
from ..commands.facade import WindowsTerminalRun
from ..config import load_config
from ..domain import AppConfig
from .constants import *
from .controllers.archive import ArchiveController
from .controllers.browser_state import BrowserStateController
from .controllers.browser_discovery import BrowserDiscoveryController
from .controllers.browser_ensure import BrowserEnsureController
from .controllers.chat_status import ChatStatusController
from .controllers.command_state import CommandStateController
from .controllers.command_editor import CommandEditorController
from .controllers.command_actions import CommandActionsController
from .controllers.layout import LayoutController
from .controllers.lifecycle import LifecycleController
from .controllers.project_settings import ProjectSettingsController
from .controllers.project_sidebar import ProjectSidebarController
from .controllers.project_shell import ProjectShellController
from .controllers.urls import UrlsController
from .url_discovery import UrlDiscoveryController
from .auto_run import AutoRunController, AutoRunJob
from .prompts import PromptsController
from .notes import NotesController
from .files import FilesController
from .file_chat_actions import FileChatActionsController
from .main_actions import MainActionsController
from .command_toolbar import CommandToolbarController
from .chatgpt_windows import ChatGptWindowActionsController

class ProjectRepoManagerApp(FileChatActionsController, FilesController, MainActionsController, CommandToolbarController, ChatGptWindowActionsController, NotesController, PromptsController, AutoRunController, ProjectShellController, LifecycleController, LayoutController, ProjectSettingsController, ProjectSidebarController, BrowserStateController, BrowserDiscoveryController, BrowserEnsureController, ChatStatusController, ArchiveController, CommandStateController, CommandEditorController, CommandActionsController, UrlDiscoveryController, UrlsController, tk.Tk):
    def __init__(self):
        super().__init__()
        self.config_data: AppConfig = load_config()
        self.title("Project Repo Manager v0.0.49")
        try:
            self.geometry(self.config_data.window_geometry or "1280x820")
        except tk.TclError:
            self.geometry("1280x820")
        self.minsize(1000, 680)

        self._loading_form = False
        self._closing = False
        self._worker_count = 0
        self._autosave_after_id: str | None = None
        self._window_save_after_id: str | None = None
        self._log_queue: queue.Queue[str] = queue.Queue(maxsize=MAX_PENDING_LOG_LINES)
        self._dropped_log_lines = 0
        self._activity_log_lines = 0
        self._ui_queue: queue.Queue[tuple[str, object, object]] = queue.Queue()
        self._fatal_log_handle = None
        self._install_runtime_diagnostics()
        self._project_drag_index: int | None = None
        self._loaded_project_index: int | None = None
        self._command_runs: dict[str, list[WindowsTerminalRun]] = {}
        self._auto_run_jobs: dict[str, AutoRunJob] = {}
        # PIDs acknowledged by an explicit project/Main-actions interaction.
        # This suppresses only the 28-minute overdue flashing for that existing
        # run; a newly launched process gets a new PID and can alert normally.
        self._acknowledged_command_pids: set[int] = set()
        # Managed command PIDs for which a decisive failure has already been
        # observed from a non-zero exit code or live Rust/Cargo/QA output.
        self._command_error_pids: set[int] = set()
        # Projects currently being extracted by a background worker.  This is
        # surfaced in the Commands status column because extraction is the
        # project operation that precedes auto-run commands.
        self._extracting_project_ids: set[str] = set()
        self._downloading_project_ids: set[str] = set()
        self._project_status_labels: dict[tuple[str, str], tk.Label] = {}
        self._browser_runs: dict[str, list[WindowsBrowserRun]] = {}
        self._chat_detected_state_by_url: dict[str, str] = {}
        self._chat_debug_lines = 0
        # Explicit Discover URL choices are authoritative for this PRM session.
        self._manual_browser_bindings: dict[int, str] = {}
        self._available_browsers: list[WindowsBrowserSpec] = detect_windows_browsers() if os.name == "nt" else []
        self._browser_discovery_stop = threading.Event()
        self._browser_reconcile_requested = threading.Event()
        self._browser_discovery_thread: threading.Thread | None = None
        # Ensure-open must never run until the first complete browser-window
        # reconciliation has finished *and* its observations have been applied
        # on the Tk thread.  A fixed startup delay is racy on machines where
        # Firefox accessibility enumeration takes several seconds.
        self._browser_initial_reconcile_complete = os.name != "nt"
        self._browser_initial_reconcile_started = False
        self._last_discovered_foreground: tuple[int, str] = (0, "")
        self._archive_status_by_project: dict[str, str] = {}
        # Remember the exact ready archive already attempted by auto-extract so
        # a failed extraction does not retry forever every polling interval.
        # A new filename/size/mtime automatically re-arms the feature.
        self._auto_extract_attempted: dict[str, tuple[str, int, int]] = {}
        self._ensure_open_worker_active = False
        self._ensure_open_last_error: dict[str, str] = {}
        # URL id -> (HWND, launch monotonic time) for windows created by the
        # ensure-open worker.  If browser discovery shortly afterwards exposes a
        # different pre-existing window for the same saved URL, prefer the user's
        # original window and close only this recent ensured duplicate.
        self._recent_ensured_launches: dict[str, tuple[int, float]] = {}
        self._flash_phase = False
        self._foreground_watch_hwnd = 0
        self._foreground_watch_since = 0.0
        self._last_runtime_housekeeping = 0.0
        self._chat_watch_manager = ChatWatchManager(
            lambda event: self._ui_queue.put(("chat_event", event, None))
        )

        self._build_style()
        self._build_ui()
        self._apply_theme(self.config_data.theme, persist=False)
        self._refresh_projects(select=self.config_data.selected_project)
        self.after(100, self._drain_log_queue)
        self.after(500, self._poll_command_windows)
        self.after(450, self._flash_status_indicators)
        self.after(400, self._poll_chat_foreground)
        self.after(DOWNLOAD_STATUS_POLL_MS, self._poll_download_status)
        self._chat_watch_manager.start()
        self._restore_persisted_browser_windows()
        self._start_browser_discovery()
        self.bind("<Configure>", self._on_app_window_configure, add=True)
        if self.config_data.window_state == "zoomed":
            self.after_idle(self._restore_app_window_state)
        # Ensure-open is gated by the explicit initial browser reconciliation
        # completion signal; this timer merely starts the periodic poll loop.
        self.after(500, self._poll_ensure_open_urls)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

