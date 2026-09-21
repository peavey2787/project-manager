from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GeneratedCertificate:
    cert_path: Path
    key_path: Path


def _state_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ProjectRepoManager"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "project-repo-manager"
    return base / "certificates"


def generated_certificate_paths() -> GeneratedCertificate:
    root = _state_dir()
    return GeneratedCertificate(root / "lan-self-signed.crt.pem", root / "lan-self-signed.key.pem")


def _openssl_executable() -> str:
    found = shutil.which("openssl")
    if found:
        return found
    if os.name == "nt":
        candidates = []
        git = shutil.which("git") or shutil.which("git.exe")
        if git:
            git_root = Path(git).resolve().parent.parent
            candidates.extend([
                git_root / "usr" / "bin" / "openssl.exe",
                git_root / "mingw64" / "bin" / "openssl.exe",
            ])
        for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(env_name)
            if value:
                base = Path(value)
                candidates.extend([
                    base / "Git" / "usr" / "bin" / "openssl.exe",
                    base / "Git" / "mingw64" / "bin" / "openssl.exe",
                    base / "Programs" / "Git" / "usr" / "bin" / "openssl.exe",
                    base / "Programs" / "Git" / "mingw64" / "bin" / "openssl.exe",
                ])
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(
        "OpenSSL was not found. Install OpenSSL (Git for Windows also includes it), "
        "or choose Custom certificate and provide a PEM certificate/private key."
    )


def _certificate_names(lan_ip: str) -> tuple[str, str]:
    host = socket.gethostname().strip() or "localhost"
    san = ["DNS:localhost", f"DNS:{host}", "IP:127.0.0.1"]
    if lan_ip and lan_ip != "127.0.0.1":
        san.append(f"IP:{lan_ip}")
    return host, ",".join(san)


def generate_self_signed_certificate(lan_ip: str) -> GeneratedCertificate:
    paths = generated_certificate_paths()
    paths.cert_path.parent.mkdir(parents=True, exist_ok=True)
    openssl = _openssl_executable()
    host, san = _certificate_names(lan_ip)
    config = (
        "[req]\n"
        "distinguished_name=dn\n"
        "prompt=no\n"
        "x509_extensions=v3_req\n"
        "[dn]\n"
        f"CN={host}\n"
        "O=Project Repo Manager\n"
        "[v3_req]\n"
        f"subjectAltName={san}\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", encoding="utf-8", delete=False) as handle:
        handle.write(config)
        config_path = Path(handle.name)
    try:
        completed = subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-nodes",
                "-days",
                "3650",
                "-keyout",
                str(paths.key_path),
                "-out",
                str(paths.cert_path),
                "-config",
                str(config_path),
                "-extensions",
                "v3_req",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    finally:
        try:
            config_path.unlink()
        except OSError:
            pass
    if completed.returncode != 0 or not paths.cert_path.is_file() or not paths.key_path.is_file():
        detail = (completed.stderr or completed.stdout or "OpenSSL certificate generation failed").strip()
        raise RuntimeError(detail)
    try:
        os.chmod(paths.key_path, 0o600)
    except OSError:
        pass
    return paths
