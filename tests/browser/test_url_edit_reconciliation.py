from __future__ import annotations

from pathlib import Path


def test_url_edit_detaches_stale_binding_and_requests_reconciliation() -> None:
    source = Path("repo_manager/ui/controllers/urls.py").read_text(encoding="utf-8")
    start = source.index("    def _edit_url")
    end = source.index("    def _remove_url", start)
    edit = source[start:end]
    assert "url_changed = old_parts != self._browser_url_parts(edited.url)" in edit
    assert "self._browser_runs.pop(url_id, [])" in edit
    assert "self._clear_url_window_identity(edited)" in edit
    assert "self._request_browser_reconcile()" in edit


def test_url_edit_restarts_chat_watch_for_live_matching_windows() -> None:
    source = Path("repo_manager/ui/controllers/urls.py").read_text(encoding="utf-8")
    start = source.index("    def _edit_url")
    end = source.index("    def _remove_url", start)
    edit = source[start:end]
    assert "self._chat_watch_manager.add(" in edit
    assert "edited.watch_chat" in edit
    assert "self._is_chatgpt_url_value(edited.url)" in edit


def test_browser_discovery_has_explicit_full_rescan_request() -> None:
    source = Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8")
    assert "def _request_browser_reconcile" in source
    assert "force_full_scan = self._browser_reconcile_requested.is_set()" in source
