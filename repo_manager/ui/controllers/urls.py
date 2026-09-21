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

class UrlsController:
    def _refresh_urls(self, *, reset_scroll: bool = False):
        if not hasattr(self, "url_groups_host"):
            return
        for child in self.url_groups_host.winfo_children():
            child.destroy()
        project = self._current_project()
        if not project:
            return
        if not project.url_groups:
            ttk.Label(self.url_groups_host, text="No URL groups yet. Click Add Group to create one.").pack(anchor="w", pady=8)
        else:
            for group in project.url_groups:
                self._render_url_group(group)

        # Dynamic content lives inside a canvas-backed ScrollableFrame. Force a
        # geometry/scrollregion refresh after rebuilding it; otherwise Tk can
        # retain the previous empty scrollregion and newly-added groups appear
        # to have vanished until the window is resized.
        self.url_groups_host.update_idletasks()
        self.urls_tab.inner.update_idletasks()
        bbox = self.urls_tab.canvas.bbox("all")
        if bbox:
            self.urls_tab.canvas.configure(scrollregion=bbox)
        if reset_scroll:
            self.urls_tab.canvas.yview_moveto(0.0)

    def _url_group_by_id(self, group_id: str) -> UrlGroup | None:
        project = self._current_project()
        if not project:
            return None
        return next((group for group in project.url_groups if group.group_id == group_id), None)

    def _url_by_id(self, group_id: str, url_id: str) -> UrlSpec | None:
        group = self._url_group_by_id(group_id)
        if not group:
            return None
        return next((item for item in group.urls if item.url_id == url_id), None)

    def _latest_browser_run(self, url_id: str) -> WindowsBrowserRun | None:
        runs = self._browser_runs.get(url_id, [])
        if os.name == "nt":
            runs = [run for run in runs if windows_hwnd_exists(run.hwnd)]
        if runs:
            self._browser_runs[url_id] = runs
            return runs[-1]
        self._browser_runs.pop(url_id, None)
        return None

    def _render_url_group(self, group: UrlGroup):
        outer = ttk.LabelFrame(self.url_groups_host, text=group.name, padding=8)
        outer.pack(fill="x", pady=(0, 10))
        header = ttk.Frame(outer)
        header.pack(fill="x")
        toggle_text = "▶ Show" if group.collapsed else "▼ Hide"
        ttk.Button(header, text=toggle_text, width=9, command=lambda gid=group.group_id: self._toggle_group(gid)).pack(side="left")
        ttk.Button(header, text="Add URL", command=lambda gid=group.group_id: self._add_url(gid)).pack(side="left", padx=(6, 0))
        ttk.Button(header, text="Rename", command=lambda gid=group.group_id: self._rename_url_group(gid)).pack(side="left", padx=(6, 0))
        ttk.Button(header, text="Remove Group", command=lambda gid=group.group_id: self._remove_url_group(gid)).pack(side="right")
        if group.collapsed:
            return
        if not group.urls:
            ttk.Label(outer, text="No URLs in this group. Click Add URL.").pack(anchor="w", pady=(8, 0))
            return
        for value in group.urls:
            row = ttk.Frame(outer)
            row.pack(fill="x", pady=(7, 0))
            ttk.Label(row, text=value.label, width=22).pack(side="left")
            link = ttk.Entry(row)
            link.insert(0, value.url)
            link.configure(state="readonly")
            link.pack(side="left", fill="x", expand=True, padx=(6, 6))
            ttk.Button(row, text="Edit", command=lambda gid=group.group_id, uid=value.url_id: self._edit_url(gid, uid)).pack(side="left", padx=(6, 0))
            ttk.Button(row, text="×", width=3, command=lambda gid=group.group_id, uid=value.url_id: self._remove_url(gid, uid)).pack(side="left", padx=(6, 0))

            controls = ttk.Frame(outer)
            controls.pack(fill="x", pady=(3, 8), padx=(28, 0))
            run = self._latest_browser_run(value.url_id)
            browser_open = bool(run and windows_hwnd_exists(run.hwnd)) if os.name == "nt" else False
            open_runs = self._browser_runs.get(value.url_id, []) if browser_open else []
            open_count = len(open_runs)
            open_text = "Not currently attached"
            if run and browser_open:
                open_text = f"{run.browser_name} • {open_count} open" if open_count > 1 else f"{run.browser_name} • open"
            ttk.Label(controls, text=open_text).pack(fill="x", anchor="w")
            if value.watch_chat and self._is_chatgpt_url_value(value.url):
                ttk.Label(controls, text="ChatGPT monitored").pack(fill="x", anchor="w", pady=(2, 0))
            if value.ensure_open:
                ttk.Label(controls, text="Ensure open").pack(fill="x", anchor="w", pady=(2, 0))
            detected = self._chat_detected_state_by_url.get(value.url_id, "")
            if detected:
                ttk.Label(controls, text=f"Detected state: {detected}").pack(fill="x", anchor="w", pady=(2, 0))

            def full_width_button(text, command, enabled=True):
                button = ttk.Button(controls, text=text, command=command)
                button.pack(fill="x", pady=(4, 0))
                if not enabled:
                    button.configure(state="disabled")
                return button

            if os.name == "nt" and self._available_browsers:
                for browser in self._available_browsers:
                    full_width_button(
                        f"Open in {browser.icon} {browser.name}",
                        lambda gid=group.group_id, uid=value.url_id, bid=browser.browser_id: self._open_url_in_browser(gid, uid, bid),
                    )
            elif os.name == "nt":
                ttk.Label(controls, text="No supported browsers detected").pack(fill="x", anchor="w", pady=(4, 0))
            else:
                full_width_button("Open in Browser", lambda gid=group.group_id, uid=value.url_id: self._open_url(gid, uid))

            if os.name == "nt":
                full_width_button("Discover URL", lambda gid=group.group_id, uid=value.url_id: self._discover_url(gid, uid))
            full_width_button("Focus", lambda uid=value.url_id: self._focus_url_window(uid), os.name == "nt" and browser_open)
            full_width_button("Close", lambda uid=value.url_id: self._close_url_window(uid), os.name == "nt" and browser_open)

            is_chat_watch = bool(value.watch_chat and self._is_chatgpt_url_value(value.url))
            if is_chat_watch:
                full_width_button(
                    "Download ChatGPT Files",
                    lambda uid=value.url_id: self._download_chatgpt_response_files(uid),
                    os.name == "nt" and browser_open,
                )
                full_width_button(
                    "Accessibility Items…",
                    lambda uid=value.url_id: self._open_chatgpt_accessibility_items(uid),
                    os.name == "nt" and browser_open,
                )

    def _add_url_group(self):
        project = self._current_project()
        if not project:
            return
        name = simpledialog.askstring("URL group", "Group name:", parent=self)
        if not name or not name.strip():
            return
        group = UrlGroup(name=name.strip())
        project.url_groups.append(group)
        self._save_config()
        self._refresh_urls(reset_scroll=True)
        self._set_status(f'Added URL group: {group.name}')

    def _toggle_group(self, group_id: str):
        group = self._url_group_by_id(group_id)
        if not group:
            return
        group.collapsed = not group.collapsed
        self._save_config()
        self._refresh_urls()

    def _rename_url_group(self, group_id: str):
        group = self._url_group_by_id(group_id)
        if not group:
            return
        name = simpledialog.askstring("Rename URL group", "Group name:", initialvalue=group.name, parent=self)
        if name and name.strip():
            group.name = name.strip()
            self._save_config()
            self._refresh_urls()
            self._update_project_status_cells()
            self._update_download_chatgpt_button()

    def _remove_url_group(self, group_id: str):
        project = self._current_project()
        group = self._url_group_by_id(group_id)
        if not project or not group:
            return
        if messagebox.askyesno("Remove group", f'Remove URL group "{group.name}" and all of its links?', parent=self):
            for item in group.urls:
                for run in self._browser_runs.get(item.url_id, []):
                    self._chat_watch_manager.remove(run.hwnd)
                self._browser_runs.pop(item.url_id, None)
                self._clear_manual_browser_binding_for_url(item.url_id)
            project.url_groups = [item for item in project.url_groups if item.group_id != group_id]
            self._save_config()
            self._refresh_urls(reset_scroll=True)
            self._update_project_status_cells()
            self._update_download_chatgpt_button()

    def _add_url(self, group_id: str):
        group = self._url_group_by_id(group_id)
        if not group:
            return
        dialog = UrlDialog(self)
        if dialog.result:
            group.urls.append(dialog.result)
            group.collapsed = False
            self._save_config()
            self._refresh_urls(reset_scroll=True)
            self._set_status(f'Added URL: {dialog.result.label}')
            if dialog.result.ensure_open:
                self.after(250, self._ensure_missing_urls_now)

    def _edit_url(self, group_id: str, url_id: str):
        item = self._url_by_id(group_id, url_id)
        if not item:
            return
        old_parts = self._browser_url_parts(item.url)
        dialog = UrlDialog(self, item)
        if not dialog.result:
            return
        group = self._url_group_by_id(group_id)
        if not group:
            return

        edited = dialog.result
        url_changed = old_parts != self._browser_url_parts(edited.url)
        for index, existing in enumerate(group.urls):
            if existing.url_id == url_id:
                group.urls[index] = edited
                break

        # An edited URL must be reconciled against the browser again.  When the
        # address changes, an old HWND association no longer proves Focus/Close
        # belongs to this saved URL, so detach it without closing the browser.
        if url_changed:
            for run in self._browser_runs.pop(url_id, []):
                self._chat_watch_manager.remove(run.hwnd)
            self._clear_manual_browser_binding_for_url(url_id)
            self._clear_url_window_identity(edited)
            self._recent_ensured_launches.pop(url_id, None)
        elif not edited.watch_chat or not self._is_chatgpt_url_value(edited.url):
            for run in self._browser_runs.get(url_id, []):
                self._chat_watch_manager.remove(run.hwnd)
        else:
            project = self._current_project()
            if project:
                for run in self._browser_runs.get(url_id, []):
                    if windows_hwnd_exists(run.hwnd):
                        run.label = edited.label
                        self._chat_watch_manager.add(
                            project.project_id, url_id, run.hwnd, edited.label, self._normalized_url_value(edited)
                        )

        self._save_config()
        self._refresh_urls()
        self._update_project_status_cells()
        self._update_download_chatgpt_button()
        self._request_browser_reconcile()
        # Do not wait for the background loop after an edit. Perform a strong
        # passive MSAA+UIA one-shot scan now so an already-open/background
        # browser window is rebound immediately. Ensure Open runs only after
        # this scan completes, avoiding duplicate browser launches.
        self._rediscover_url_windows(
            url_id,
            announce=url_changed,
            ensure_after=edited.ensure_open,
        )

    def _remove_url(self, group_id: str, url_id: str):
        group = self._url_group_by_id(group_id)
        if not group:
            return
        group.urls = [item for item in group.urls if item.url_id != url_id]
        for run in self._browser_runs.get(url_id, []):
            self._chat_watch_manager.remove(run.hwnd)
        self._browser_runs.pop(url_id, None)
        self._clear_manual_browser_binding_for_url(url_id)
        self._save_config()
        self._refresh_urls()
        self._update_project_status_cells()
        self._update_download_chatgpt_button()

    def _browser_by_id(self, browser_id: str) -> WindowsBrowserSpec | None:
        return next((browser for browser in self._available_browsers if browser.browser_id == browser_id), None)

    def _normalized_url_value(self, item: UrlSpec) -> str:
        value = item.url.strip()
        if "://" not in value:
            value = "https://" + value
        return value

    def _open_url(self, group_id: str, url_id: str):
        """Generic non-Windows URL opener retained for portability."""
        item = self._url_by_id(group_id, url_id)
        if not item:
            return
        value = self._normalized_url_value(item)
        webbrowser.open(value, new=2)
        self._set_status(f"Opened URL: {item.label}")

    def _open_url_in_browser(self, group_id: str, url_id: str, browser_id: str):
        item = self._url_by_id(group_id, url_id)
        project = self._current_project()
        browser = self._browser_by_id(browser_id)
        if not item or not project or not browser:
            if browser is None:
                messagebox.showerror("Browser unavailable", "That browser is no longer detected. Restart Project Repo Manager to refresh the browser list.", parent=self)
            return
        value = self._normalized_url_value(item)
        label = item.label
        project_id = project.project_id
        self._clear_manual_browser_binding_for_url(url_id)
        should_watch = bool(item.watch_chat and self._is_chatgpt_url_value(value))
        self._log(f"Opening exact URL in {browser.name}: {value}")

        def work():
            return launch_windows_browser(value, label=label, browser=browser)

        def done(run: WindowsBrowserRun):
            item.last_browser_id = browser.browser_id
            self._restore_url_window_geometry(item, run)
            self.after(300, lambda item=item, run=run: self._restore_url_window_geometry(item, run) if windows_hwnd_exists(run.hwnd) else None)
            self._browser_runs.setdefault(url_id, []).append(run)
            self._remember_url_window_identity(item, run)
            if should_watch:
                self._chat_watch_manager.add(project_id, url_id, run.hwnd, label, value)
                self._log(f"Watching ChatGPT response state via accessibility: {label} (HWND {run.hwnd})")
            self._save_config()
            self._refresh_urls()
            self._update_project_status_cells()
            self._update_download_chatgpt_button()
            self._set_status(f"Opened {label} in a new {browser.name} window")

        self._run_worker(f"Opening {label} in {browser.name}…", work, done)

    def _focus_url_window(self, url_id: str):
        if os.name != "nt":
            messagebox.showinfo("Focus browser", "Managed browser-window focus is currently available on Windows.", parent=self)
            return
        run = self._latest_browser_run(url_id)
        if not run or not windows_hwnd_exists(run.hwnd):
            messagebox.showinfo("Browser window", "No managed browser window is currently open for this URL. Click one of the browser buttons first.", parent=self)
            self._refresh_urls()
            return
        try:
            focus_windows_browser(run)
            self._set_status(f"Focused browser: {run.label}")
        except Exception as exc:
            self._show_error("Could not focus browser window", exc)

    def _close_url_window(self, url_id: str):
        if os.name != "nt":
            messagebox.showinfo("Close browser", "Managed browser-window close is currently available on Windows.", parent=self)
            return
        run = self._latest_browser_run(url_id)
        if not run or not windows_hwnd_exists(run.hwnd):
            self._browser_runs.pop(url_id, None)
            self._refresh_urls()
            return
        try:
            found = self._url_spec_global(url_id)
            if found is not None:
                _project, item = found
                if self._remember_url_window_geometry(item, run):
                    self._save_config()
            close_windows_browser(run)
            self._chat_watch_manager.remove(run.hwnd)
            runs = self._browser_runs.get(url_id, [])
            self._browser_runs[url_id] = [candidate for candidate in runs if candidate is not run]
            if not self._browser_runs[url_id]:
                self._browser_runs.pop(url_id, None)
                found = self._url_spec_global(url_id)
                if found is not None:
                    _project, saved_item = found
                    if self._clear_url_window_identity(saved_item):
                        self._save_config()
            self._set_status(f"Closed browser: {run.label}")
            self._update_project_status_cells()
            self._update_download_chatgpt_button()
            self.after(200, self._refresh_urls)
        except Exception as exc:
            self._show_error("Could not close browser window", exc)

