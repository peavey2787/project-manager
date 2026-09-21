from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_manager.browser.discovery import resolve_windows_browser_window_candidates
from repo_manager.browser.models import WindowsBrowserRun


def _candidate(hwnd: int = 101) -> WindowsBrowserRun:
    return WindowsBrowserRun(
        process=None,
        label="",
        url="",
        executable=r"C:\\Browser\\firefox.exe",
        browser_id="firefox",
        browser_name="Firefox",
        hwnd=hwnd,
        owner_pid=10,
        discovered=True,
        window_title="ChatGPT - Firefox",
    )


def test_uia_fallback_resolves_background_candidate_when_msaa_is_unreadable() -> None:
    candidate = _candidate()
    with (
        patch("repo_manager.browser.discovery.inspect_windows_browser_window", return_value=None),
        patch(
            "repo_manager.platform.windows.browser_uia.read_browser_active_urls_uia",
            return_value={candidate.hwnd: "https://chatgpt.com/c/already-open"},
        ),
    ):
        resolved, unresolved = resolve_windows_browser_window_candidates(
            [candidate], use_uia_fallback=True
        )
    assert unresolved == []
    assert len(resolved) == 1
    assert resolved[0].url == "https://chatgpt.com/c/already-open"
    assert resolved[0].window_title == "ChatGPT - Firefox"


def test_startup_edit_and_project_selection_request_strong_reconciliation() -> None:
    discovery = Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8")
    url_discovery = Path("repo_manager/ui/url_discovery.py").read_text(encoding="utf-8")
    urls = Path("repo_manager/ui/controllers/urls.py").read_text(encoding="utf-8")
    settings = Path("repo_manager/ui/controllers/project_settings.py").read_text(encoding="utf-8")

    startup = discovery.index("initial_candidates = enumerate_windows_browser_discovery_rows")
    assert "enumerate_windows_browser_discovery_rows" in discovery[startup : startup + 220]
    assert "self._rediscover_url_windows(" in urls
    assert "use_uia_fallback=True" in url_discovery
    assert "self._request_browser_reconcile()" in settings


def test_discover_url_lists_all_supported_windows_and_preserves_manual_override() -> None:
    url_discovery = Path("repo_manager/ui/url_discovery.py").read_text(encoding="utf-8")
    state = Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8")
    dialog = Path("repo_manager/ui/dialogs/browser_window.py").read_text(encoding="utf-8")

    assert "enumerate_windows_browser_discovery_rows" in url_discovery
    assert "self._manual_browser_bindings[int(selected.hwnd)] = url_id" in url_discovery
    assert "manual_url_id = self._manual_browser_bindings.get(int(run.hwnd))" in state
    assert "(unreadable - select manually if this is the right window)" in dialog


def test_left_panel_keeps_main_actions_while_auto_extract_remains_in_project_settings() -> None:
    layout = Path("repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8")
    actions = Path("repo_manager/ui/main_actions.py").read_text(encoding="utf-8")
    assert "height=9" in layout
    assert "self.main_actions_scroll = ScrollableFrame(left)" in layout
    assert "self._build_main_actions(self.main_actions_scroll.inner)" in layout
    assert 'text="Auto extract when ready"' in layout
    assert 'text="Auto extract when ready"' not in actions
    assert 'text="Main actions"' in actions

