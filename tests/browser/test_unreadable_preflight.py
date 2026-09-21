from pathlib import Path
import unittest


class UnreadableBrowserPreflightTests(unittest.TestCase):
    def test_ensure_preflight_keeps_unreadable_browser_passive(self) -> None:
        source = Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8")
        start = source.index("def _exhaustive_browser_preflight")
        end = source.index("def _ensure_missing_urls_now", start)
        body = source[start:end]
        self.assertIn("enumerate_windows_browser_window_candidates", body)
        self.assertIn("resolve_windows_browser_window_candidates", body)
        self.assertIn("use_uia_fallback=True", body)
        self.assertNotIn("focus_windows_hwnd", body)
        self.assertNotIn("SetForegroundWindow", body)

    def test_ensure_open_defers_if_same_browser_window_is_unresolved(self) -> None:
        source = Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8")
        start = source.index("def _ensure_missing_urls_now")
        end = source.index("def _poll_ensure_open_urls", start)
        body = source[start:end]
        self.assertIn("same_browser_unresolved", body)
        self.assertIn("Ensure-open: deferred", body)
        self.assertLess(body.index("same_browser_unresolved"), body.index("launch_windows_browser"))


if __name__ == "__main__":
    unittest.main()
