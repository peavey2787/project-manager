from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class ChatState(str, Enum):
    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    ERROR = "error"
    UNAVAILABLE = "unavailable"

@dataclass
class ChatWatch:
    project_id: str
    url_id: str
    hwnd: int
    label: str
    url: str = ""
    expected_document_key: str = ""
    document_key: str = ""
    document_name: str = ""
    active_response_document_key: str = ""
    active_response_source_hwnd: int = 0
    active_response_object_id: int = 0
    active_response_child_id: int = 0
    # Retain the last response's exact composer tuple after completion so the
    # user-triggered Download action can resolve that same chat document even
    # if another Firefox tab is active.
    last_response_source_hwnd: int = 0
    last_response_object_id: int = 0
    last_response_child_id: int = 0
    last_response_document_key: str = ""
    state: ChatState = ChatState.IDLE
    initialized: bool = False
    saw_progress: bool = False
    acknowledged: bool = False
    response_fingerprint: str = ""
    response_text_length: int = 0
    download_count: int = 0
    error_text: str = ""
    completed_at: float = 0.0
    in_progress_since: float = 0.0
    composer_state: str = "unknown"
    accessibility_backend: str = ""
    composer_name: str = ""
    worked_for_text: str = ""
    timeout_retry_attempts: int = 0
    timeout_retry_last_attempt: float = 0.0
    timeout_retry_clicked: bool = False

@dataclass(frozen=True)
class ChatEvent:
    kind: str
    watch: ChatWatch
    previous_state: ChatState

