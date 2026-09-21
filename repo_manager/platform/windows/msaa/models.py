from __future__ import annotations
import threading
from dataclasses import dataclass

@dataclass(frozen=True)
class MsaaElement:
    index: int
    name: str
    role: int
    default_action: str

@dataclass(frozen=True)
class MsaaTreeSnapshot:
    source_hwnd: int
    object_id: int
    available: bool
    composer_state: str
    composer_name: str
    error_text: str
    response_fingerprint: str
    response_text_length: int
    download_count: int
    document_name: str
    document_value: str
    document_key: str
    elements: tuple[MsaaElement, ...]
    worked_for_text: str = ""
    latest_response_complete: bool = False

@dataclass
class _ActionTarget:
    index: int
    name: str
    role: int
    acc_ptr: int
    child_id: int
    document_key: str = ""

@dataclass(frozen=True)
class MsaaWinEvent:
    top_hwnd: int
    event_id: int
    source_hwnd: int
    object_id: int
    child_id: int
    name: str
    role: int
    default_action: str
    document_name: str = ""
    document_value: str = ""
    document_key: str = ""

@dataclass
class _InvokeDownloadsCommand:
    hwnd: int
    match_terms: tuple[str, ...]
    expected_document_key: str
    done: threading.Event
    clicked: list[str]

@dataclass
class _ProbeRetainedCommand:
    hwnd: int
    done: threading.Event
    snapshot: MsaaTreeSnapshot | None = None

@dataclass(frozen=True)
class MsaaFocusEvent:
    sequence: int
    top_hwnd: int
    source_hwnd: int
    object_id: int
    child_id: int
    name: str
    role: int
    default_action: str

