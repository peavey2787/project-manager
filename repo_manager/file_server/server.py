from __future__ import annotations

import functools
import http.server
import socket
import ssl
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class ServerEndpoint:
    scheme: str
    port: int
    root: Path
    lan_url: str
    local_url: str


class _ThreadingServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    server_version = "ProjectRepoManagerHTTP/1.0"

    def log_message(self, format: str, *args) -> None:
        callback = getattr(self.server, "prm_log", None)
        if callback is not None:
            callback("File server: " + (format % args))


def local_lan_address() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        address = str(probe.getsockname()[0])
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    finally:
        probe.close()
    try:
        address = socket.gethostbyname(socket.gethostname())
        return address or "127.0.0.1"
    except OSError:
        return "127.0.0.1"


class FileServerSession:
    def __init__(self, log: LogFn | None = None):
        self._log = log
        self._server: _ThreadingServer | None = None
        self._thread: threading.Thread | None = None
        self.endpoint: ServerEndpoint | None = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def start(
        self,
        root: Path,
        *,
        port: int,
        https: bool = False,
        cert_path: Path | None = None,
        key_path: Path | None = None,
    ) -> ServerEndpoint:
        if self.running:
            raise RuntimeError("A file server is already running.")
        root = root.resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Selected server folder does not exist: {root}")
        if not 1 <= int(port) <= 65535:
            raise ValueError("Server port must be between 1 and 65535.")

        handler = functools.partial(_QuietHandler, directory=str(root))
        server = _ThreadingServer(("0.0.0.0", int(port)), handler)
        server.prm_log = self._log  # type: ignore[attr-defined]
        scheme = "https" if https else "http"
        try:
            if https:
                if cert_path is None:
                    raise ValueError("HTTPS requires a certificate.")
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(
                    certfile=str(cert_path),
                    keyfile=str(key_path) if key_path is not None else None,
                )
                server.socket = context.wrap_socket(server.socket, server_side=True)
        except Exception:
            server.server_close()
            raise

        actual_port = int(server.server_address[1])
        lan_ip = local_lan_address()
        endpoint = ServerEndpoint(
            scheme=scheme,
            port=actual_port,
            root=root,
            lan_url=f"{scheme}://{lan_ip}:{actual_port}/",
            local_url=f"{scheme}://127.0.0.1:{actual_port}/",
        )
        thread = threading.Thread(target=server.serve_forever, name="prm-file-server", daemon=True)
        self._server = server
        self._thread = thread
        self.endpoint = endpoint
        thread.start()
        if self._log:
            self._log(f"File server started: {endpoint.lan_url} -> {root}")
        return endpoint

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        endpoint = self.endpoint
        self._server = None
        self._thread = None
        self.endpoint = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        if self._log and endpoint is not None:
            self._log(f"File server stopped: {endpoint.lan_url}")
