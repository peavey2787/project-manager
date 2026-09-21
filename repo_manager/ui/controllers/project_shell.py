from __future__ import annotations

from ...platform.windows.shell_terminal import open_cmd_terminal, open_powershell_terminal


class ProjectShellController:
    def _open_selected_cmd_terminal(self) -> None:
        project = self._validate_project_settings()
        if not project:
            return
        try:
            open_cmd_terminal(project.working_dir)
            self._set_status(f"Opened Command Prompt: {project.working_dir}")
        except Exception as exc:
            self._show_error("Open Command Prompt failed", exc)

    def _open_selected_powershell_terminal(self) -> None:
        project = self._validate_project_settings()
        if not project:
            return
        try:
            open_powershell_terminal(project.working_dir)
            self._set_status(f"Opened PowerShell: {project.working_dir}")
        except Exception as exc:
            self._show_error("Open PowerShell failed", exc)
