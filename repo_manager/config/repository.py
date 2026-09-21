from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..domain.models import (
    AppConfig,
    CommandSpec,
    DEFAULT_EXCLUDED_DIRS,
    NoteSpec,
    Project,
    PromptSpec,
    UrlGroup,
    UrlSpec,
    default_downloads,
    default_github_root,
    default_prompts,
)
from .migrations import SCHEMA_VERSION, migrate_payload

APP_NAME = "Project Repo Manager"


def config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "project-repo-manager" / "config.json"


def _auto_run_order(data: Any) -> int:
    try:
        return max(0, min(9, int(data)))
    except (TypeError, ValueError):
        return 0




def _server_port(data: Any) -> int:
    try:
        return max(1, min(65535, int(data)))
    except (TypeError, ValueError):
        return 8000

def _completion_mode(data: Any) -> str:
    value = str(data or "exit_code").strip().casefold()
    return "delay" if value == "delay" else "exit_code"


def _completion_delay_seconds(data: Any) -> float:
    try:
        value = float(data)
    except (TypeError, ValueError):
        return 5.0
    return max(0.1, min(86400.0, value))

def _command_from(data: Any) -> CommandSpec:
    if not isinstance(data, dict):
        return CommandSpec()
    return CommandSpec(
        label=str(data.get("label", "Command")),
        command=str(data.get("command", "")),
        auto_run=bool(data.get("auto_run", False)),
        auto_run_order=_auto_run_order(data.get("auto_run_order", 0)),
        completion_mode=_completion_mode(data.get("completion_mode", "exit_code")),
        completion_delay_seconds=_completion_delay_seconds(data.get("completion_delay_seconds", 5.0)),
        auto_close_on_success=bool(data.get("auto_close_on_success", False)),
        command_id=str(data.get("command_id") or uuid.uuid4().hex),
    )



def _note_from(data: Any) -> NoteSpec:
    if not isinstance(data, dict):
        return NoteSpec()
    return NoteSpec(
        label=str(data.get("label", "Note")),
        text=str(data.get("text", "")),
        note_id=str(data.get("note_id") or uuid.uuid4().hex),
    )


def _prompt_from(data: Any) -> PromptSpec:
    if not isinstance(data, dict):
        return PromptSpec()
    return PromptSpec(
        label=str(data.get("label", "Prompt")),
        text=str(data.get("text", "")),
        prompt_id=str(data.get("prompt_id") or uuid.uuid4().hex),
    )


def _is_chatgpt_url(value: str) -> bool:
    lowered = value.strip().casefold()
    return "chatgpt.com/" in lowered or lowered.rstrip("/").endswith("chatgpt.com")


def _url_from(data: Any) -> UrlSpec:
    if not isinstance(data, dict):
        return UrlSpec()
    url = str(data.get("url", "https://"))
    return UrlSpec(
        label=str(data.get("label", "Link")),
        url=url,
        watch_chat=bool(data.get("watch_chat", _is_chatgpt_url(url))),
        ensure_open=bool(data.get("ensure_open", False)),
        last_browser_id=str(data.get("last_browser_id", "")),
        window_x=int(data.get("window_x", 0) or 0),
        window_y=int(data.get("window_y", 0) or 0),
        window_width=max(0, int(data.get("window_width", 0) or 0)),
        window_height=max(0, int(data.get("window_height", 0) or 0)),
        window_maximized=bool(data.get("window_maximized", False)),
        last_hwnd=max(0, int(data.get("last_hwnd", 0) or 0)),
        last_owner_pid=max(0, int(data.get("last_owner_pid", 0) or 0)),
        url_id=str(data.get("url_id") or uuid.uuid4().hex),
    )


def _group_from(data: Any) -> UrlGroup:
    if not isinstance(data, dict):
        return UrlGroup()
    return UrlGroup(
        name=str(data.get("name", "Links")),
        collapsed=bool(data.get("collapsed", False)),
        urls=[_url_from(item) for item in data.get("urls", []) if isinstance(item, dict)],
        group_id=str(data.get("group_id") or uuid.uuid4().hex),
    )


def _project_from(
    data: Any,
    *,
    legacy_prompts: list[dict[str, Any]] | None = None,
    legacy_notes: list[dict[str, Any]] | None = None,
) -> Project:
    if not isinstance(data, dict):
        return Project()
    excluded = data.get("excluded_dirs", DEFAULT_EXCLUDED_DIRS)
    if not isinstance(excluded, list):
        excluded = list(DEFAULT_EXCLUDED_DIRS)
    snapshot = data.get("snapshot", [])
    if not isinstance(snapshot, list):
        snapshot = []

    if "prompts" in data and isinstance(data.get("prompts"), list):
        prompts = [_prompt_from(item) for item in data.get("prompts", []) if isinstance(item, dict)]
    elif legacy_prompts is not None:
        prompts = [_prompt_from(item) for item in legacy_prompts if isinstance(item, dict)]
    else:
        prompts = default_prompts()

    if "notes" in data and isinstance(data.get("notes"), list):
        notes = [_note_from(item) for item in data.get("notes", []) if isinstance(item, dict)]
    elif legacy_notes is not None:
        notes = [_note_from(item) for item in legacy_notes if isinstance(item, dict)]
    else:
        notes = []

    return Project(
        name=str(data.get("name", "New Project")),
        project_id=str(data.get("project_id") or uuid.uuid4().hex),
        match_string=str(data.get("match_string", "project")),
        downloads_dir=str(data.get("downloads_dir", default_downloads())),
        extract_parent=str(data.get("extract_parent", data.get("downloads_dir", default_downloads()))),
        repo_root=str(data.get("repo_root", default_github_root())),
        delete_older_zips=bool(data.get("delete_older_zips", True)),
        preserve_git_on_extract=bool(data.get("preserve_git_on_extract", True)),
        confirm_extract_newest=bool(data.get("confirm_extract_newest", True)),
        auto_extract_when_ready=bool(data.get("auto_extract_when_ready", data.get("auto_extract", False))),
        auto_stop_commands_before_extract=bool(data.get("auto_stop_commands_before_extract", False)),
        preserve_git_on_replace=bool(data.get("preserve_git_on_replace", True)),
        zip_exclude_git=bool(data.get("zip_exclude_git", True)),
        excluded_dirs=[str(x) for x in excluded if str(x).strip()],
        commands=[_command_from(item) for item in data.get("commands", []) if isinstance(item, dict)],
        url_groups=[_group_from(item) for item in data.get("url_groups", []) if isinstance(item, dict)],
        snapshot=[str(x) for x in snapshot],
        last_extracted_zip=str(data.get("last_extracted_zip", "")),
        last_extracted_zip_size=int(data.get("last_extracted_zip_size", 0) or 0),
        last_extracted_zip_mtime_ns=int(data.get("last_extracted_zip_mtime_ns", 0) or 0),
        last_created_zip=str(data.get("last_created_zip", "")),
        file_server_protocol=str(data.get("file_server_protocol", "http") or "http").casefold(),
        file_server_port=_server_port(data.get("file_server_port", 8000)),
        file_server_cert_mode=("custom" if str(data.get("file_server_cert_mode", "generated")).casefold() == "custom" else "generated"),
        file_server_cert_path=str(data.get("file_server_cert_path", "")),
        file_server_key_path=str(data.get("file_server_key_path", "")),
        prompts=prompts,
        notes=notes,
        selected_prompt_id=str(data.get("selected_prompt_id", "")),
        selected_note_id=str(data.get("selected_note_id", "")),
    )


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return AppConfig(schema_version=SCHEMA_VERSION)
    try:
        data = migrate_payload(json.loads(path.read_text(encoding="utf-8")))
        raw_legacy_prompts = data.get("prompts")
        legacy_prompts = raw_legacy_prompts if isinstance(raw_legacy_prompts, list) else None
        raw_legacy_notes = data.get("notes")
        legacy_notes = raw_legacy_notes if isinstance(raw_legacy_notes, list) else None
        projects = [
            _project_from(item, legacy_prompts=legacy_prompts, legacy_notes=legacy_notes)
            for item in data.get("projects", [])
            if isinstance(item, dict)
        ]
        selected = int(data.get("selected_project", 0)) if projects else 0
        selected = max(0, min(selected, len(projects) - 1)) if projects else 0
        theme = str(data.get("theme", "dark")).casefold()
        if theme not in {"dark", "light"}:
            theme = "dark"
        state = str(data.get("window_state", "normal")).casefold()
        if state not in {"normal", "zoomed"}:
            state = "normal"
        return AppConfig(
            schema_version=SCHEMA_VERSION,
            selected_project=selected,
            theme=theme,
            window_geometry=str(data.get("window_geometry", "1280x820") or "1280x820"),
            window_state=state,
            projects=projects,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return AppConfig(schema_version=SCHEMA_VERSION)


def save_config(config: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    payload["schema_version"] = SCHEMA_VERSION
    fd, temp_name = tempfile.mkstemp(prefix="config-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass
