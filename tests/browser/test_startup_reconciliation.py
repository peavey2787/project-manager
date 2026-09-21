from __future__ import annotations

import unittest
from pathlib import Path

from repo_manager.ui.app import ProjectRepoManagerApp


class StartupBrowserReconcileTests(unittest.TestCase):
    def test_ensure_open_is_gated_by_initial_reconciliation(self) -> None:
        source = (Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/lifecycle.py").read_text(encoding="utf-8"))
        self.assertIn("if not self._browser_initial_reconcile_complete:", source)
        self.assertIn('(\"browser_initial_reconcile_done\", len(initial_candidates), None)', source)
        self.assertIn("self._browser_initial_reconcile_complete = True", source)

    def test_initial_scan_runs_before_discovery_sleep_loop(self) -> None:
        source = (Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8"))
        scan_pos = source.index("initial_candidates = enumerate_windows_browser_discovery_rows")
        loop_pos = source.index("while not self._browser_discovery_stop.wait(0.45)", scan_pos)
        self.assertLess(scan_pos, loop_pos)

    def test_ensure_worker_rescans_immediately_before_launch(self) -> None:
        source = (Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8"))
        self.assertIn("preflight_seen, unresolved = self._exhaustive_browser_preflight()", source)
        self.assertIn(
            "if any(self._browser_url_match_score(seen.url, value) > 0 for seen in preflight_seen):",
            source,
        )
        skip_pos = source.index("if any(self._browser_url_match_score(seen.url, value) > 0 for seen in preflight_seen):")
        launch_pos = source.index("run = launch_windows_browser", skip_pos)
        self.assertLess(skip_pos, launch_pos)

    def test_old_fixed_startup_delay_is_removed(self) -> None:
        source = (Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8"))
        self.assertNotIn("self.after(3200, self._poll_ensure_open_urls)", source)

    def test_recent_ensured_duplicate_is_closed_when_existing_window_appears(self) -> None:
        source = (Path("repo_manager/ui/controllers/browser_discovery.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_ensure.py").read_text(encoding="utf-8") + Path("repo_manager/ui/controllers/browser_state.py").read_text(encoding="utf-8"))
        self.assertIn("_prefer_discovered_window_over_recent_ensured_duplicate", source)
        self.assertIn("self._recent_ensured_launches[item.url_id]", source)
        self.assertIn("close_windows_browser(ensured_run)", source)


if __name__ == "__main__":
    unittest.main()
