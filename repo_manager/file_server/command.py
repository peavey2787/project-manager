from __future__ import annotations

import os
import sys
from pathlib import Path


def _py_literal(value: str) -> str:
    """Return a single-quoted Python literal safe inside cmd.exe's -c string."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_folder_server_command(
    root: Path,
    *,
    port: int,
    https: bool = False,
    cert_path: Path | None = None,
    key_path: Path | None = None,
    python_executable: str | None = None,
) -> str:
    """Build one shell line that serves *root* without depending on PRM imports."""
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Selected server folder does not exist: {root}")
    if not 1 <= int(port) <= 65535:
        raise ValueError("Server port must be between 1 and 65535.")
    executable = str(python_executable or sys.executable)
    if not https:
        return (
            f'"{executable}" -m http.server {int(port)} --bind 0.0.0.0 '
            f'--directory "{root}"'
        )
    if cert_path is None:
        raise ValueError("HTTPS requires a certificate path.")
    cert = Path(cert_path).resolve()
    key = Path(key_path).resolve() if key_path is not None else None
    code = (
        "import functools,http.server,ssl;"
        f"root={_py_literal(str(root))};port={int(port)};"
        "handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=root);"
        "server=http.server.ThreadingHTTPServer(('0.0.0.0',port),handler);"
        "ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);"
        f"ctx.load_cert_chain(certfile={_py_literal(str(cert))},keyfile={_py_literal(str(key)) if key else 'None'});"
        "server.socket=ctx.wrap_socket(server.socket,server_side=True);"
        "print(f'Serving HTTPS on 0.0.0.0:{port} from {root}',flush=True);"
        "server.serve_forever()"
    )
    return f'"{executable}" -c "{code}"'


__all__ = ["build_folder_server_command"]
