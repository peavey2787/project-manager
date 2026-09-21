from __future__ import annotations
import os
import shutil
import subprocess
import sys
from pathlib import Path

def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.Popen(["explorer", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])

def open_in_vscode(path: Path) -> None:
    executable = shutil.which("code") or shutil.which("code.cmd")
    if not executable:
        raise FileNotFoundError("Visual Studio Code command-line launcher ('code') was not found in PATH.")
    subprocess.Popen([executable, str(path)])

