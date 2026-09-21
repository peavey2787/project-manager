from __future__ import annotations

from pathlib import Path


def test_startup_uses_same_all_window_candidate_discovery_as_manual_discover() -> None:
    discovery = Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8")
    lifecycle = Path("repo_manager/ui/controllers/lifecycle.py").read_text(encoding="utf-8")
    url_discovery = Path("repo_manager/ui/url_discovery.py").read_text(encoding="utf-8")

    startup = discovery.index("initial_candidates = enumerate_windows_browser_discovery_rows")
    barrier = discovery.index('("browser_initial_reconcile_done", len(initial_candidates), None)', startup)
    candidates = discovery.index('("browser_discovery_candidates", initial_candidates, "Startup")', startup)
    assert startup < candidates < barrier
    assert 'elif kind == "browser_discovery_candidates"' in lifecycle
    assert "_auto_bind_discovery_candidates" in url_discovery
    assert "_is_strong_discovery_match" in url_discovery
    assert "Never guess between two equally plausible unreadable windows" in url_discovery


def test_forced_reconcile_also_includes_unreadable_discovery_candidates() -> None:
    discovery = Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8")
    assert "if force_full_scan:\n                    candidates = enumerate_windows_browser_discovery_rows" in discovery
    assert '("browser_discovery_candidates", candidates, "Forced")' in discovery


def test_discover_dialog_has_reload_and_selection_flash() -> None:
    dialog = Path("repo_manager/ui/dialogs/browser_window.py").read_text(encoding="utf-8")
    controller = Path("repo_manager/ui/url_discovery.py").read_text(encoding="utf-8")
    highlight = Path("repo_manager/platform/windows/window_highlight.py").read_text(encoding="utf-8")

    assert 'text="Reload"' in dialog
    assert "reload_callback=request_reload" in controller
    assert "<<TreeviewSelect>>" in dialog
    assert "flash_windows_hwnd_border(run.hwnd)" in dialog
    assert "PATINVERT" in highlight
    assert "DWMWA_EXTENDED_FRAME_BOUNDS" in highlight
    assert "DwmGetWindowAttribute" in highlight
    assert "SetThreadDpiAwarenessContext" in highlight
    assert "GetAncestor" in highlight
    assert "SetForegroundWindow" not in highlight
