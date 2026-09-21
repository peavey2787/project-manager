from pathlib import Path


def _app_source() -> str:
    return (Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8"))


def test_exhaustive_browser_preflight_is_strictly_passive():
    source = _app_source()
    start = source.index("def _exhaustive_browser_preflight")
    end = source.index("def _ensure_missing_urls_now", start)
    body = source[start:end]
    assert "focus_windows_hwnd" not in body
    assert "SetForegroundWindow" not in body
    assert "_activate_windows_hwnd" not in body
    assert "enumerate_windows_browser_window_candidates" in body
    assert "resolve_windows_browser_window_candidates" in body
    assert "use_uia_fallback=True" in body


def test_background_app_code_does_not_import_generic_hwnd_focus_helper():
    source = _app_source()
    # Explicit Focus actions use focus_windows_browser / focus_windows_terminal.
    # The generic HWND activator was only present for the bad background sweep.
    assert "focus_windows_hwnd," not in source


def test_ensure_open_defers_on_unreadable_same_browser_windows():
    source = _app_source()
    start = source.index("def _ensure_missing_urls_now")
    end = source.index("def _poll_ensure_open_urls", start)
    body = source[start:end]
    assert "same_browser_unresolved" in body
    assert "continue" in body
    assert "launch_windows_browser" in body
    assert body.index("same_browser_unresolved") < body.index("launch_windows_browser")
