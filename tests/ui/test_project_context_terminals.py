from __future__ import annotations

from pathlib import Path


def test_project_context_menu_offers_cmd_and_powershell() -> None:
    source = Path("repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8")
    assert 'label="Open Command Prompt Here"' in source
    assert 'label="Open PowerShell Here"' in source


def test_terminal_launchers_use_project_working_directory() -> None:
    source = Path("repo_manager/ui/controllers/project_shell.py").read_text(encoding="utf-8")
    assert "open_cmd_terminal(project.working_dir)" in source
    assert "open_powershell_terminal(project.working_dir)" in source
