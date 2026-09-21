from __future__ import annotations

import types
import unittest
from unittest.mock import patch

from repo_manager.platform.windows import environment as operations


class _Key:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False


class WindowsEnvironmentRefreshTests(unittest.TestCase):
    def test_merges_current_registry_path_ahead_of_stale_process_path(self) -> None:
        fake = types.SimpleNamespace()
        fake.HKEY_LOCAL_MACHINE = object()
        fake.HKEY_CURRENT_USER = object()
        machine_key = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
        user_key = r"Environment"

        values = {
            (fake.HKEY_LOCAL_MACHINE, machine_key, "Path"): r"C:\Windows\System32;C:\Program Files\Common",
            (fake.HKEY_CURRENT_USER, user_key, "Path"): r"C:\Program Files (x86)\GnuWin32\bin;%USERPROFILE%\bin",
        }

        def open_key(root, subkey):
            return _Key((root, subkey))

        def query_value_ex(key, name):
            return values[(key.value[0], key.value[1], name)], 1

        fake.OpenKey = open_key
        fake.QueryValueEx = query_value_ex

        with (
            patch.object(operations.os, "name", "nt"),
            patch.dict(operations.os.environ, {
                "PATH": r"C:\OldOnly",
                "USERPROFILE": r"C:\Users\peave",
                "COMSPEC": r"C:\Windows\System32\cmd.exe",
            }, clear=True),
            patch.dict("sys.modules", {"winreg": fake}),
        ):
            env = operations.refreshed_windows_environment()

        path = env["PATH"]
        self.assertIn(r"C:\Program Files (x86)\GnuWin32\bin", path)
        self.assertIn(r"C:\Users\peave\bin", path)
        self.assertIn(r"C:\OldOnly", path)
        self.assertLess(
            path.index(r"C:\Program Files (x86)\GnuWin32\bin"),
            path.index(r"C:\OldOnly"),
        )


if __name__ == "__main__":
    unittest.main()
