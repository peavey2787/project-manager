from __future__ import annotations

import socket
import ssl
import urllib.request
from pathlib import Path

from repo_manager.file_server import FileServerSession, generate_self_signed_certificate


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def test_http_server_serves_selected_folder(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello from prm", encoding="utf-8")
    session = FileServerSession()
    endpoint = session.start(tmp_path, port=_free_port())
    try:
        with urllib.request.urlopen(endpoint.local_url + "hello.txt", timeout=3) as response:
            assert response.read() == b"hello from prm"
    finally:
        session.stop()
    assert not session.running


def test_generated_certificate_can_serve_https(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "served"
    root.mkdir()
    (root / "secure.txt").write_text("secure", encoding="utf-8")
    cert = generate_self_signed_certificate("127.0.0.1")
    assert cert.cert_path.is_file()
    assert cert.key_path.is_file()

    session = FileServerSession()
    endpoint = session.start(
        root,
        port=_free_port(),
        https=True,
        cert_path=cert.cert_path,
        key_path=cert.key_path,
    )
    try:
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(endpoint.local_url + "secure.txt", context=context, timeout=3) as response:
            assert response.read() == b"secure"
    finally:
        session.stop()
