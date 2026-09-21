from __future__ import annotations

import os
from tkinter import messagebox

from ..browser.facade import close_windows_browser, windows_hwnd_exists


class ChatGptWindowActionsController:
    def _chatgpt_url_entries(self, project):
        for group in project.url_groups:
            for item in group.urls:
                if item.watch_chat and self._is_chatgpt_url_value(item.url):
                    yield group, item

    def _open_project_chatgpt_windows(self) -> None:
        project = self._current_project()
        if project is None:
            return
        entries = list(self._chatgpt_url_entries(project))
        if not entries:
            messagebox.showinfo(
                "No ChatGPT URL",
                "This project has no URL configured as a monitored ChatGPT URL.",
                parent=self,
            )
            return
        opened = 0
        already_open = 0
        unavailable = 0
        for group, item in entries:
            live = [run for run in self._browser_runs.get(item.url_id, []) if os.name != "nt" or windows_hwnd_exists(run.hwnd)]
            if live:
                already_open += 1
                continue
            if os.name == "nt":
                browser = self._preferred_browser_for_url(item)
                if browser is None:
                    unavailable += 1
                    continue
                self._open_url_in_browser(group.group_id, item.url_id, browser.browser_id)
            else:
                self._open_url(group.group_id, item.url_id)
            opened += 1
        if opened == 0:
            if unavailable:
                self._set_status("No supported browser is available for the configured ChatGPT URL")
            else:
                self._set_status(f"ChatGPT URL already open for {project.name}")
        elif already_open:
            self._log(f"ChatGPT open action skipped {already_open} already-open managed window(s).")

    def _close_project_chatgpt_windows(self) -> None:
        project = self._current_project()
        if project is None:
            return
        runs = self._project_browser_runs(project, chatgpt_only=True)
        if not runs:
            self._set_status(f"No managed ChatGPT windows are open for {project.name}")
            return
        if not messagebox.askyesno(
            "Close ChatGPT URL",
            f"Close {len(runs)} managed ChatGPT window(s) for {project.name}?",
            parent=self,
        ):
            return
        failures: list[str] = []
        closed_hwnds: set[int] = set()
        geometry_changed = False
        for item, run in runs:
            try:
                if self._remember_url_window_geometry(item, run):
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
        self._set_status(f"Closed {len(closed_hwnds)} ChatGPT window(s) for {project.name}")
        if failures:
            messagebox.showwarning("Some ChatGPT windows could not be closed", "\n".join(failures[:5]), parent=self)
