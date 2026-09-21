from __future__ import annotations

import tempfile
from pathlib import Path

from repo_manager.file_server import build_folder_server_command


def test_http_folder_server_command_is_one_line_and_uses_selected_folder() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        command = build_folder_server_command(root, port=8123, python_executable=r"C:\Python314\python.exe")
        assert "\n" not in command and "\r" not in command
        assert command.startswith('"C:\\Python314\\python.exe" -m http.server 8123')
        assert "--bind 0.0.0.0" in command
        assert f'--directory "{root.resolve()}"' in command


def test_https_folder_server_command_is_one_line_and_embeds_cert_paths() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cert = root / "server cert.pem"
        key = root / "server key.pem"
        command = build_folder_server_command(
            root,
            port=9443,
            https=True,
            cert_path=cert,
            key_path=key,
            python_executable=r"C:\Python314\python.exe",
        )
        assert "\n" not in command and "\r" not in command
        assert "ThreadingHTTPServer" in command
        assert "PROTOCOL_TLS_SERVER" in command
        assert str(cert.resolve()).replace("\\", "\\\\") in command
        assert str(key.resolve()).replace("\\", "\\\\") in command
