from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def default_downloads() -> str:
    return str(Path.home() / "Downloads")


def default_github_root() -> str:
    candidates = [
        Path.home() / "GitHub",
        Path.home() / "github",
        Path.home() / "Documents" / "GitHub",
        Path.home() / "source" / "repos",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


DEFAULT_EXCLUDED_DIRS = [
    "target", "build", "dist", "out", "bin", "obj", "artifacts",
    "node_modules", ".next", ".nuxt", ".svelte-kit", ".parcel-cache", ".vite",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".gradle", ".idea", ".vs", "coverage",
]


@dataclass
class CommandSpec:
    label: str = "Command"
    command: str = ""
    auto_run: bool = False
    auto_run_order: int = 0
    completion_mode: str = "exit_code"
    completion_delay_seconds: float = 5.0
    auto_close_on_success: bool = False
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class NoteSpec:
    label: str = "Note"
    text: str = ""
    note_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class PromptSpec:
    label: str = "Prompt"
    text: str = ""
    prompt_id: str = field(default_factory=lambda: uuid.uuid4().hex)


def default_prompts() -> list[PromptSpec]:
    return [
        PromptSpec(
            label="Security / vulnerability review",
            text="Are there any vulnerabilities or potential vulnerabilities/weaknesses/flaws/gaps/etc.? (Look extra close/deep at any serializers/parsers/etc.)",
        ),
        PromptSpec(
            label="Test quality review",
            text="Are any of the tests tautological/vacuous/false positive/useless/etc.?",
        ),
        PromptSpec(
            label="Production readiness review",
            text="Is the code-base Production‑ready and ready for consumers? Enterprise‑grade? Mathematically sound (formal verification aside)? If not, what’s missing?",
        ),
        PromptSpec(
            label="Guideline compliance review",
            text=(
                "Is the repo violating any of the guidelines below? If so, recommend steps to remediate.\n\n"
                "Guidelines\n\n"
                "Organized file/folder structure\n\n"
                "Clear separation of concerns\n\n"
                "Consistent naming\n\n"
                "Single Responsibility Principle (SRP)\n\n"
                "Low CRAP/CC score, aim for <= 12 CC\n\n"
                "No legacy, deprecated, duplicated, or unused code; stay DRY\n\n"
                "When a file becomes too large, refactor it so it still follows SRP\n\n"
                "When a folder accumulates too many files, group and reorganize\n\n"
                "Use facades to encapsulate complex subsystems, but expose pure functions or direct structs for simple, single-purpose utilities"
            ),
        ),
    ]


@dataclass
class UrlSpec:
    label: str = "Link"
    url: str = "https://"
    watch_chat: bool = False
    ensure_open: bool = False
    last_browser_id: str = ""
    window_x: int = 0
    window_y: int = 0
    window_width: int = 0
    window_height: int = 0
    window_maximized: bool = False
    last_hwnd: int = 0
    last_owner_pid: int = 0
    url_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class UrlGroup:
    name: str = "Links"
    collapsed: bool = False
    urls: list[UrlSpec] = field(default_factory=list)
    group_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class Project:
    name: str = "New Project"
    project_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    match_string: str = "project"
    downloads_dir: str = field(default_factory=default_downloads)
    extract_parent: str = field(default_factory=default_downloads)
    repo_root: str = field(default_factory=default_github_root)
    delete_older_zips: bool = True
    preserve_git_on_extract: bool = True
    confirm_extract_newest: bool = True
    auto_extract_when_ready: bool = False
    auto_stop_commands_before_extract: bool = False
    preserve_git_on_replace: bool = True
    zip_exclude_git: bool = True
    excluded_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_DIRS))
    commands: list[CommandSpec] = field(default_factory=list)
    url_groups: list[UrlGroup] = field(default_factory=list)
    snapshot: list[str] = field(default_factory=list)
    last_extracted_zip: str = ""
    last_extracted_zip_size: int = 0
    last_extracted_zip_mtime_ns: int = 0
    last_created_zip: str = ""
    file_server_protocol: str = "http"
    file_server_port: int = 8000
    file_server_cert_mode: str = "generated"
    file_server_cert_path: str = ""
    file_server_key_path: str = ""
    prompts: list[PromptSpec] = field(default_factory=default_prompts)
    notes: list[NoteSpec] = field(default_factory=list)
    selected_prompt_id: str = ""
    selected_note_id: str = ""

    @property
    def working_dir(self) -> Path:
        return Path(os.path.expandvars(os.path.expanduser(self.extract_parent))) / self.match_string

    @property
    def snapshot_excluded_dirs(self) -> list[str]:
        """Common build/output folders plus any project-specific exclusions."""
        seen: set[str] = set()
        result: list[str] = []
        for name in [*DEFAULT_EXCLUDED_DIRS, *self.excluded_dirs]:
            value = str(name).strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result


@dataclass
class AppConfig:
    schema_version: int = 16
    selected_project: int = 0
    theme: str = "dark"
    window_geometry: str = "1280x820"
    window_state: str = "normal"
    projects: list[Project] = field(default_factory=list)
