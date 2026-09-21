from __future__ import annotations

import unittest
from pathlib import Path

from repo_manager.domain import UrlSpec
from repo_manager.config.repository import _url_from


class PersistedBrowserIdentityTests(unittest.TestCase):
    def test_url_runtime_identity_defaults_off(self) -> None:
        item = UrlSpec()
        self.assertEqual(item.last_hwnd, 0)
        self.assertEqual(item.last_owner_pid, 0)

    def test_url_runtime_identity_round_trips_loader(self) -> None:
        item = _url_from({
            "label": "Project A",
            "url": "https://chatgpt.com/c/test",
            "last_hwnd": 12345,
            "last_owner_pid": 678,
            "last_browser_id": "firefox",
        })
        self.assertEqual(item.last_hwnd, 12345)
        self.assertEqual(item.last_owner_pid, 678)
        self.assertEqual(item.last_browser_id, "firefox")

    def test_startup_reattaches_persisted_hwnd_before_discovery(self) -> None:
        source = Path("repo_manager/ui/app.py").read_text(encoding="utf-8")
        start = source.index("self._chat_watch_manager.start()")
        restore = source.index("self._restore_persisted_browser_windows()", start)
        discover = source.index("self._start_browser_discovery()", restore)
        self.assertLess(restore, discover)
        self.assertIn("recover_windows_browser_window(", (Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8")))

    def test_ensure_worker_rechecks_same_runtime_state_as_focus(self) -> None:
        source = (Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8"))
        worker = source.index("def worker():", source.index("def _ensure_missing_urls_now"))
        attached = source.index("self._browser_runs.get(url_id, [])", worker)
        launch = source.index("run = launch_windows_browser", attached)
        self.assertLess(attached, launch)
        self.assertIn("attached window already exists, not launching duplicate", source)

    def test_recover_persisted_window_requires_same_pid(self) -> None:
        source = Path("repo_manager/browser/discovery.py").read_text(encoding="utf-8")
        start = source.index("def recover_windows_browser_window")
        end = source.index("def enumerate_windows_browser_windows", start)
        body = source[start:end]
        self.assertIn("current_pid != int(owner_pid)", body)
        self.assertIn("Path(spec.executable).name.casefold() != exe_name", body)


if __name__ == "__main__":
    unittest.main()
