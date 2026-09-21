from __future__ import annotations

import os
from tkinter import messagebox

from ..browser.facade import (
    WindowsBrowserRun,
    enumerate_windows_browser_discovery_rows,
    enumerate_windows_browser_windows,
    get_windows_foreground_hwnd,
)
from .dialogs.browser_window import BrowserWindowDiscoveryDialog


class UrlDiscoveryController:
    """One-shot saved-URL discovery and explicit window binding."""

    def _discovery_guess_score(self, item, run: WindowsBrowserRun, foreground: int) -> int:
        """Rank a discovered HWND without pretending weak heuristics are proof."""
        score = self._browser_url_match_score(run.url, item.url) * 100
        if item.last_hwnd and int(item.last_hwnd) == int(run.hwnd):
            score += 50_000
        if item.last_browser_id and item.last_browser_id == run.browser_id:
            score += 80
        title = (run.window_title or "").casefold()
        host = self._browser_url_parts(item.url)[1]
        if host and host in title:
            score += 60
        for token in {part.casefold() for part in item.label.split() if len(part) >= 4}:
            if token in title:
                score += 20
        if int(run.hwnd) == int(foreground or 0):
            score += 5
        return score

    def _rank_discovery_windows(self, item, runs: list[WindowsBrowserRun]) -> tuple[int | None, int]:
        if not runs:
            return None, 0
        foreground = get_windows_foreground_hwnd()
        ranked = [
            (self._discovery_guess_score(item, run, foreground), index)
            for index, run in enumerate(runs)
        ]
        best_score, best_index = max(ranked)
        return (best_index if best_score > 0 else None), best_score

    def _is_strong_discovery_match(self, item, run: WindowsBrowserRun, score: int) -> bool:
        """Allow unattended binding only when discovery has real identity evidence."""
        if self._browser_url_match_score(run.url, item.url) > 0:
            return True
        if item.last_hwnd and int(item.last_hwnd) == int(run.hwnd):
            return True
        # 100 points requires more than merely sharing the same browser.  In
        # practice this is browser identity plus a matching title/host token,
        # which is the same evidence that makes the manual dialog pick the
        # correct unreadable window on startup.
        return score >= 100

    def _auto_bind_discovery_candidates(self, runs: list[WindowsBrowserRun], *, reason: str) -> int:
        """Use Discover-URL candidates to recover strong already-open bindings."""
        if os.name != "nt" or not runs:
            return 0
        foreground = get_windows_foreground_hwnd()
        choices: list[tuple[int, str, int, object, object, WindowsBrowserRun]] = []
        for project in self.config_data.projects:
            for item in self._project_url_specs(project):
                ranked = sorted(
                    (
                        (self._discovery_guess_score(item, run, foreground), run)
                        for run in runs
                    ),
                    key=lambda row: row[0],
                    reverse=True,
                )
                if not ranked:
                    continue
                best_score, best_run = ranked[0]
                if best_score <= 0 or not self._is_strong_discovery_match(item, best_run, best_score):
                    continue
                exact_identity = (
                    self._browser_url_match_score(best_run.url, item.url) > 0
                    or bool(item.last_hwnd and int(item.last_hwnd) == int(best_run.hwnd))
                )
                second_score = ranked[1][0] if len(ranked) > 1 else -1
                # Never guess between two equally plausible unreadable windows.
                # Exact URL/HWND identity is decisive; title/browser heuristics
                # are accepted only when one candidate is strictly best.
                if not exact_identity and second_score >= best_score:
                    continue
                choices.append((best_score, item.url_id, int(best_run.hwnd), project, item, best_run))
        choices.sort(key=lambda row: row[0], reverse=True)

        used_urls: set[str] = set()
        used_hwnds: set[int] = set()
        attached = 0
        for _score, url_id, hwnd, project, item, run in choices:
            if url_id in used_urls or hwnd in used_hwnds:
                continue
            self._attach_browser_run_to_url(project, item, run, select_project=False)
            used_urls.add(url_id)
            used_hwnds.add(hwnd)
            attached += 1

        if attached:
            self._save_config()
            self._refresh_urls()
            self._update_project_status_cells()
            self._update_download_chatgpt_button()
            self._log(f"{reason} Discover URL reconciliation attached {attached} already-open browser window(s).")
        return attached

    def _clear_manual_browser_binding_for_url(self, url_id: str) -> None:
        for hwnd, bound_url_id in list(self._manual_browser_bindings.items()):
            if bound_url_id == url_id:
                self._manual_browser_bindings.pop(hwnd, None)

    def _rediscover_url_windows(
        self,
        url_id: str,
        *,
        announce: bool = False,
        ensure_after: bool = False,
    ) -> None:
        """Immediately rescan already-open windows after a saved URL changes."""
        if os.name != "nt":
            return
        found = self._url_spec_global(url_id)
        if found is None:
            return
        _project, item = found
        expected_url = item.url

        def work():
            return enumerate_windows_browser_windows(
                self._available_browsers,
                use_uia_fallback=True,
            )

        def done(runs: list[WindowsBrowserRun]):
            current = self._url_spec_global(url_id)
            if current is None:
                return
            current_project, current_item = current
            if current_item.url != expected_url:
                return
            matches = [
                run
                for run in runs
                if self._browser_url_match_score(run.url, current_item.url) > 0
            ]
            for run in matches:
                self._attach_browser_run_to_url(current_project, current_item, run)
            if announce:
                if matches:
                    self._set_status(
                        f"Discovered {len(matches)} already-open browser window(s) for {current_item.label}"
                    )
                else:
                    self._set_status(f"No readable matching browser window found for {current_item.label}")
            self._refresh_urls()
            self._update_project_status_cells()
            self._update_download_chatgpt_button()
            if ensure_after:
                # The one-shot scan has completed, so Ensure Open may now decide
                # whether a launch is actually necessary.
                self.after(100, self._ensure_missing_urls_now)

        self._run_worker("Discovering already-open browser windows…", work, done)

    def _discover_url(self, group_id: str, url_id: str) -> None:
        if os.name != "nt":
            messagebox.showinfo(
                "Discover URL",
                "Browser-window discovery is currently available on Windows.",
                parent=self,
            )
            return
        item = self._url_by_id(group_id, url_id)
        project = self._current_project()
        if item is None or project is None:
            return

        def work():
            return enumerate_windows_browser_discovery_rows(self._available_browsers)

        def done(runs: list[WindowsBrowserRun]):
            current = self._url_spec_global(url_id)
            if current is None:
                return
            current_project, current_item = current
            if not runs:
                messagebox.showinfo(
                    "Discover URL",
                    "No supported browser windows are currently open.",
                    parent=self,
                )
                return

            recommended_index, _best_score = self._rank_discovery_windows(current_item, runs)

            def request_reload(apply_rows) -> None:
                def reload_work():
                    return enumerate_windows_browser_discovery_rows(self._available_browsers)

                def reload_done(new_runs: list[WindowsBrowserRun]):
                    latest = self._url_spec_global(url_id)
                    if latest is None:
                        apply_rows(new_runs, None)
                        return
                    _latest_project, latest_item = latest
                    recommended, _score = self._rank_discovery_windows(latest_item, new_runs)
                    apply_rows(new_runs, recommended)

                self._run_worker("Reloading open browser windows…", reload_work, reload_done)

            dialog = BrowserWindowDiscoveryDialog(
                self,
                label=current_item.label,
                saved_url=current_item.url,
                windows=runs,
                recommended_index=recommended_index,
                reload_callback=request_reload,
            )
            if dialog.result is None:
                return
            selected = dialog.result

            # One HWND can be explicitly bound to only one saved URL. Likewise,
            # replacing a saved URL's manual choice must not leave the old HWND
            # authoritative in the background discovery loop.
            self._clear_manual_browser_binding_for_url(url_id)
            self._manual_browser_bindings.pop(int(selected.hwnd), None)
            for other_hwnd, other_url_id in list(self._manual_browser_bindings.items()):
                if other_url_id == url_id:
                    self._manual_browser_bindings.pop(other_hwnd, None)
            self._manual_browser_bindings[int(selected.hwnd)] = url_id

            for previous in self._browser_runs.get(url_id, []):
                self._chat_watch_manager.remove(previous.hwnd)
            self._browser_runs.pop(url_id, None)
            self._attach_browser_run_to_url(
                current_project,
                current_item,
                selected,
                select_project=False,
            )
            self._save_config()
            self._refresh_urls()
            self._update_project_status_cells()
            self._update_download_chatgpt_button()
            self._set_status(
                f"Bound {current_item.label} to {selected.browser_name} window {selected.hwnd}"
            )

        self._run_worker("Finding all open browser windows…", work, done)
