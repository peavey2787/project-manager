from __future__ import annotations

import inspect
import unittest

from repo_manager.browser import launch as operations
from repo_manager.browser.models import WindowsBrowserRun


class FirefoxNormalProfileTests(unittest.TestCase):
    def test_firefox_launch_uses_native_new_window_without_remote_control_flags(self) -> None:
        source = inspect.getsource(operations.launch_windows_browser)
        self.assertIn('"-new-window"', source)
        self.assertNotIn("remote-debugging-port", source)
        self.assertNotIn("WebDriver", source)
        self.assertNotIn("BiDi", source)
        self.assertNotIn("RemoteAgent", source)

    def test_firefox_launch_does_not_use_synthetic_keyboard_input(self) -> None:
        source = inspect.getsource(operations.launch_windows_browser)
        self.assertNotIn("_navigate_firefox_hwnd", source)
        self.assertNotIn("_send_virtual_key_windows", source)
        self.assertIn("No cmd.exe quoting, no registry URL template", source)

    def test_browser_run_has_no_remote_control_state(self) -> None:
        fields = WindowsBrowserRun.__dataclass_fields__
        self.assertNotIn("bidi_context", fields)
        self.assertNotIn("dom_managed", fields)


if __name__ == "__main__":
    unittest.main()
