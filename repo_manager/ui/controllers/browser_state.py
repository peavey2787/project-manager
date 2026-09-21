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

class BrowserStateController:
    @staticmethod
    def _is_chatgpt_url_value(value: str) -> bool:
        lowered = value.strip().casefold()
        return "chatgpt.com/" in lowered or lowered.rstrip("/").endswith("chatgpt.com")

    def _project_url_specs(self, project: Project):
        for group in project.url_groups:
            for item in group.urls:
                yield item

    def _url_spec_global(self, url_id: str) -> tuple[Project, UrlSpec] | None:
        for project in self.config_data.projects:
            for item in self._project_url_specs(project):
                if item.url_id == url_id:
                    return project, item
        return None

    def _remember_url_window_geometry(self, item: UrlSpec, run: WindowsBrowserRun) -> bool:
        if os.name != "nt" or not run.hwnd or not windows_hwnd_exists(run.hwnd):
            return False
        try:
            geometry = get_windows_window_geometry(run.hwnd)
        except Exception:
            geometry = None
        if geometry is None:
            return False
        x, y, width, height, maximized = geometry
        before = (item.window_x, item.window_y, item.window_width, item.window_height, item.window_maximized)
        after = (int(x), int(y), int(width), int(height), bool(maximized))
        if before == after:
            return False
        item.window_x, item.window_y, item.window_width, item.window_height, item.window_maximized = after
        return True

    def _restore_url_window_geometry(self, item: UrlSpec, run: WindowsBrowserRun) -> None:
        if os.name != "nt" or item.window_width <= 0 or item.window_height <= 0:
            return
        try:
            set_windows_window_geometry(
                run.hwnd,
                item.window_x,
                item.window_y,
                item.window_width,
                item.window_height,
                maximized=item.window_maximized,
            )
        except Exception as exc:
            self._log(f"Window geometry restore warning for {item.label}: {exc}")

    def _remember_url_window_identity(self, item: UrlSpec, run: WindowsBrowserRun) -> bool:
        """Persist the exact live browser HWND/PID used by Focus/Close.

        The HWND remains valid when PRM is restarted while the browser stays
        open.  Persisting this association lets startup restore the same runtime
        object before Firefox has materialized a background address-bar
        accessibility tree.
        """
        if os.name != "nt" or not run.hwnd or not windows_hwnd_exists(run.hwnd):
            return False
        before = (item.last_hwnd, item.last_owner_pid, item.last_browser_id)
        item.last_hwnd = int(run.hwnd)
        item.last_owner_pid = int(run.owner_pid or 0)
        if run.browser_id:
            item.last_browser_id = run.browser_id
        after = (item.last_hwnd, item.last_owner_pid, item.last_browser_id)
        return before != after

    def _clear_url_window_identity(self, item: UrlSpec) -> bool:
        before = (item.last_hwnd, item.last_owner_pid)
        item.last_hwnd = 0
        item.last_owner_pid = 0
        return before != (0, 0)

    def _restore_persisted_browser_windows(self) -> None:
        """Reattach browser windows that survived a PRM restart.

        This runs synchronously before the accessibility-based discovery thread.
        It uses the same HWND-backed ``_browser_runs`` structure as Focus/Close
        and Ensure Open, so an already-managed window is authoritative from the
        first ensure-open poll.  Accessibility discovery subsequently verifies
        the active URL and corrects an association if the user navigated the
        window while PRM was closed.
        """
        if os.name != "nt":
            return
        changed = False
        restored = 0
        for project in self.config_data.projects:
            for item in self._project_url_specs(project):
                if not item.last_hwnd:
                    continue
                run = recover_windows_browser_window(
                    item.last_hwnd,
                    item.last_owner_pid,
                    label=item.label,
                    url=self._normalized_url_value(item),
                    browser_id=item.last_browser_id,
                    browsers=self._available_browsers,
                )
                if run is None:
                    changed = self._clear_url_window_identity(item) or changed
                    continue
                # If background accessibility is already readable, validate the
                # URL immediately.  If it is dormant, keep the exact HWND/PID
                # association and let the foreground/full discovery pass verify
                # it later instead of opening a duplicate.
                try:
                    observed = inspect_windows_browser_window(run.hwnd, self._available_browsers)
                except Exception:
                    observed = None
                if observed is not None:
                    if self._browser_url_match_score(observed.url, item.url) <= 0:
                        changed = self._clear_url_window_identity(item) or changed
                        continue
                    run = observed
                    run.label = item.label
                self._browser_runs.setdefault(item.url_id, []).append(run)
                self._remember_url_window_identity(item, run)
                if item.watch_chat and self._is_chatgpt_url_value(item.url):
                    self._chat_watch_manager.add(
                        project.project_id, item.url_id, run.hwnd, item.label, self._normalized_url_value(item)
                    )
                restored += 1
        if changed:
            self._save_config()
        if restored:
            self._log(f"Reattached {restored} browser window(s) from the previous PRM session.")
            self._refresh_urls()
            self._update_project_status_cells()
            self._update_download_chatgpt_button()

    @staticmethod
    def _browser_url_parts(value: str) -> tuple[str, str, str, str]:
        text = str(value or "").strip()
        if not text:
            return ("", "", "", "")
        if "://" not in text:
            text = "https://" + text
        try:
            parsed = urlsplit(text)
        except Exception:
            return ("", "", "", "")
        scheme = parsed.scheme.casefold()
        host = parsed.netloc.casefold()
        if scheme not in {"http", "https"} or not host:
            return ("", "", "", "")
        if host.endswith(":80") and scheme == "http":
            host = host[:-3]
        elif host.endswith(":443") and scheme == "https":
            host = host[:-4]
        path = parsed.path.rstrip("/") or "/"
        return (scheme, host, path, parsed.query)

    @classmethod
    def _browser_url_match_score(cls, active: str, saved: str) -> int:
        active_parts = cls._browser_url_parts(active)
        saved_parts = cls._browser_url_parts(saved)
        if not active_parts[1] or not saved_parts[1]:
            return 0
        a_scheme, a_host, a_path, a_query = active_parts
        s_scheme, s_host, s_path, s_query = saved_parts
        if a_host != s_host or a_path != s_path:
            return 0
        score = 100
        if a_scheme == s_scheme:
            score += 10
        if a_query == s_query:
            score += 20
        elif s_query:
            return 0
        return score

    def _match_saved_browser_url(self, active_url: str) -> tuple[Project, UrlSpec] | None:
        current = self._current_project()
        ranked: list[tuple[int, int, int, Project, UrlSpec]] = []
        for project_index, project in enumerate(self.config_data.projects):
            for group in project.url_groups:
                for item in group.urls:
                    score = self._browser_url_match_score(active_url, item.url)
                    if not score:
                        continue
                    current_bonus = 1 if current is not None and current.project_id == project.project_id else 0
                    ranked.append((score, current_bonus, -project_index, project, item))
        if not ranked:
            return None
        ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
        return ranked[0][3], ranked[0][4]

    def _remove_discovered_browser_hwnd(self, hwnd: int, *, except_url_id: str = "") -> bool:
        changed = False
        config_changed = False
        for url_id, runs in list(self._browser_runs.items()):
            kept = [
                run
                for run in runs
                if not (run.discovered and run.hwnd == int(hwnd) and url_id != except_url_id)
            ]
            if len(kept) != len(runs):
                changed = True
                found = self._url_spec_global(url_id)
                if found is not None:
                    _project, saved_item = found
                    if saved_item.last_hwnd == int(hwnd):
                        if kept:
                            config_changed = self._remember_url_window_identity(saved_item, kept[-1]) or config_changed
                        else:
                            config_changed = self._clear_url_window_identity(saved_item) or config_changed
            if kept:
                self._browser_runs[url_id] = kept
            else:
                self._browser_runs.pop(url_id, None)
        if config_changed:
            self._save_config()
        return changed

    def _on_browser_focus_observation(self, run: WindowsBrowserRun) -> None:
        """Bind a user-opened browser HWND and follow its project selection."""
        self._on_browser_observation(run, select_project=True)

    def _on_browser_seen_observation(self, run: WindowsBrowserRun) -> None:
        """Bind a background/manual browser HWND without changing selection."""
        self._on_browser_observation(run, select_project=False)

    def _prefer_discovered_window_over_recent_ensured_duplicate(self, item: UrlSpec, discovered: WindowsBrowserRun) -> None:
        """Close a startup ensure-open duplicate when the real window arrives late.

        Firefox can lazily expose a background window's address-bar accessibility
        tree.  Even with the startup barrier and preflight scan, that can make an
        already-open window become discoverable only just after Ensure Open has
        launched a replacement.  During a short grace interval we know exactly
        which HWND PRM created, so it is safe to close that one and keep the
        user's discovered window.
        """
        recent = self._recent_ensured_launches.get(item.url_id)
        if recent is None:
            return
        ensured_hwnd, launched_at = recent
        if time.monotonic() - launched_at > 20.0:
            self._recent_ensured_launches.pop(item.url_id, None)
            return
        if int(discovered.hwnd) == int(ensured_hwnd):
            return
        ensured_run = next(
            (candidate for candidate in self._browser_runs.get(item.url_id, []) if candidate.hwnd == int(ensured_hwnd)),
            None,
        )
        if ensured_run is None:
            self._recent_ensured_launches.pop(item.url_id, None)
            return
        try:
            close_windows_browser(ensured_run)
        except Exception as exc:
            self._log(f"Could not close duplicate ensured browser window for {item.label}: {exc}")
            return
        self._browser_runs[item.url_id] = [
            candidate for candidate in self._browser_runs.get(item.url_id, []) if candidate.hwnd != int(ensured_hwnd)
        ]
        if not self._browser_runs[item.url_id]:
            self._browser_runs.pop(item.url_id, None)
        self._recent_ensured_launches.pop(item.url_id, None)
        self._log(f"Kept already-open URL window and closed duplicate restore window: {item.label}")

    def _on_browser_observation(self, run: WindowsBrowserRun, *, select_project: bool) -> None:
        # A Discover URL selection is authoritative for this PRM session. The
        # browser may expose a temporarily stale/blank accessibility URL while
        # backgrounded, so normal polling must not immediately steal the HWND.
        manual_url_id = self._manual_browser_bindings.get(int(run.hwnd))
        if manual_url_id:
            manual_match = self._url_spec_global(manual_url_id)
            if manual_match is not None:
                project, item = manual_match
                self._attach_browser_run_to_url(project, item, run, select_project=select_project)
                return
            self._manual_browser_bindings.pop(int(run.hwnd), None)

        match = self._match_saved_browser_url(run.url)
        if match is None:
            changed = self._remove_discovered_browser_hwnd(run.hwnd)
            # A watcher attached to a manually/currently focused ChatGPT tab
            # must not remain bound after that browser navigates elsewhere.
            watch = next((w for w in self._chat_watch_manager.watches() if w.hwnd == run.hwnd), None)
            if watch and not any(
                existing.hwnd == run.hwnd and not existing.discovered
                for runs in self._browser_runs.values()
                for existing in runs
            ):
                self._chat_watch_manager.remove(run.hwnd)
            if changed:
                self._refresh_urls()
                self._update_project_status_cells()
                self._update_download_chatgpt_button()
            return

        project, item = match
        self._attach_browser_run_to_url(project, item, run, select_project=select_project)

    def _attach_browser_run_to_url(
        self,
        project: Project,
        item: UrlSpec,
        run: WindowsBrowserRun,
        *,
        select_project: bool = False,
    ) -> None:
        if run.discovered:
            self._prefer_discovered_window_over_recent_ensured_duplicate(item, run)
        changed = self._remove_discovered_browser_hwnd(run.hwnd, except_url_id=item.url_id)
        existing = next((candidate for candidate in self._browser_runs.get(item.url_id, []) if candidate.hwnd == run.hwnd), None)
        if existing is None:
            run.label = item.label
            run.discovered = True
            self._browser_runs.setdefault(item.url_id, []).append(run)
            changed = True
        elif existing.discovered:
            # Preserve the last readable URL when a manually bound/background
            # window temporarily resolves only as an HWND candidate.
            if run.url:
                existing.url = run.url
            existing.browser_id = run.browser_id
            existing.browser_name = run.browser_name
            existing.executable = run.executable
            existing.owner_pid = run.owner_pid
            existing.label = item.label
            if run.window_title:
                existing.window_title = run.window_title

        current_watch = next((w for w in self._chat_watch_manager.watches() if w.hwnd == run.hwnd), None)
        should_watch = bool(item.watch_chat and self._is_chatgpt_url_value(item.url))
        if current_watch is not None and (current_watch.url_id != item.url_id or not should_watch):
            self._chat_watch_manager.remove(run.hwnd)
            current_watch = None
        if should_watch and current_watch is None:
            self._chat_watch_manager.add(project.project_id, item.url_id, run.hwnd, item.label, item.url)

        geometry_changed = self._remember_url_window_geometry(item, run)
        identity_changed = self._remember_url_window_identity(item, run)
        if geometry_changed or identity_changed:
            self._save_config()

        current_project = self._current_project()
        if select_project and (current_project is None or current_project.project_id != project.project_id):
            self._select_project_status_label(project.project_id)
            changed = False  # selection refreshes the URLs tab itself
        elif changed and current_project is not None and current_project.project_id == project.project_id:
            self._refresh_urls()
            self._update_download_chatgpt_button()
        self._update_project_status_cells()

    def _preferred_browser_for_url(self, item: UrlSpec) -> WindowsBrowserSpec | None:
        if not self._available_browsers:
            return None
        if item.last_browser_id:
            browser = self._browser_by_id(item.last_browser_id)
            if browser is not None:
                return browser
        firefox = next((browser for browser in self._available_browsers if browser.browser_id == "firefox"), None)
        return firefox or self._available_browsers[0]

    def _project_browser_runs(self, project: Project, *, chatgpt_only: bool = False) -> list[tuple[UrlSpec, WindowsBrowserRun]]:
        result: list[tuple[UrlSpec, WindowsBrowserRun]] = []
        for item in self._project_url_specs(project):
            if chatgpt_only and not self._is_chatgpt_url_value(item.url):
                continue
            for run in self._browser_runs.get(item.url_id, []):
                if os.name != "nt" or windows_hwnd_exists(run.hwnd):
                    result.append((item, run))
        return result

    def _focus_project_chatgpt_windows(self):
        project = self._current_project()
        if not project:
            return
        runs = self._project_browser_runs(project, chatgpt_only=True)
        if not runs:
            self._set_status(f"No managed ChatGPT windows are open for {project.name}")
            return
        failures = 0
        for _item, run in runs:
            try:
                focus_windows_browser(run)
            except Exception:
                failures += 1
        self._set_status(f"Focused {len(runs) - failures}/{len(runs)} ChatGPT window(s) for {project.name}")

    def _close_project_url_windows(self):
        project = self._current_project()
        if not project:
            return
        runs = self._project_browser_runs(project)
        if not runs:
            self._set_status(f"No managed URL windows are open for {project.name}")
            return
        if not messagebox.askyesno(
            "Close all project URL windows",
            f"Close {len(runs)} managed URL window(s) for {project.name}?",
            parent=self,
        ):
            return
        failures = []
        closed_hwnds: set[int] = set()
        geometry_changed = False
        for url_item, run in runs:
            try:
                if self._remember_url_window_geometry(url_item, run):
                    geometry_changed = True
                if run.browser_id and url_item.last_browser_id != run.browser_id:
                    url_item.last_browser_id = run.browser_id
                    geometry_changed = True
                close_windows_browser(run)
                closed_hwnds.add(run.hwnd)
                self._chat_watch_manager.remove(run.hwnd)
            except Exception as exc:
                failures.append(str(exc))
        if geometry_changed:
            self._save_config()
        for item in self._project_url_specs(project):
            kept = [run for run in self._browser_runs.get(item.url_id, []) if run.hwnd not in closed_hwnds]
            if kept:
                self._browser_runs[item.url_id] = kept
            else:
                self._browser_runs.pop(item.url_id, None)
        self._refresh_urls()
        self._update_project_status_cells()
        self._update_download_chatgpt_button()
        self._set_status(f"Closed {len(closed_hwnds)} URL window(s) for {project.name}")
        if failures:
            messagebox.showwarning("Some URL windows could not be closed", "\n".join(failures[:5]), parent=self)

