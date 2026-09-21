from __future__ import annotations
import os
import threading
import time
from typing import Callable, Iterable
from ..browser.window_state import windows_hwnd_exists
from ..platform.windows.uia_fallback import ChatAccessibilitySnapshot, download_latest_chatgpt_response_links, inspect_chatgpt_windows
from ..platform.windows.msaa import MsaaWinEvent, MsaaWinEventMonitor, invoke_event_object_msaa, make_document_key, probe_composer_msaa, probe_event_object_msaa
from ..platform.windows.msaa.constants import EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE
from .models import ChatEvent, ChatState, ChatWatch
from .state_machine import ERROR_RE, classify_detected_state

class ChatRegistryMixin:
    def _sync_event_watch_set(self) -> None:
        with self._lock:
            handles = tuple(self._watches)
        self._event_monitor.set_watched(handles)

    def add(
        self,
        project_id: str,
        url_id: str,
        hwnd: int,
        label: str,
        url: str = "",
    ) -> None:
        if os.name != "nt" or not hwnd:
            return
        with self._lock:
            self._watches[int(hwnd)] = ChatWatch(
                project_id=project_id,
                url_id=url_id,
                hwnd=int(hwnd),
                label=label,
                url=str(url or ""),
                expected_document_key=make_document_key("", str(url or "")),
            )
        self._sync_event_watch_set()
        if not self._event_monitor.active:
            # Retry here as well as from the background watchdog. A previous
            # startup failure must not permanently disable ChatGPT monitoring.
            self._event_monitor.start()
        if not self._event_monitor.active:
            failed = ChatWatch(**vars(self._watches[int(hwnd)]))
            failed.error_text = self._event_monitor.startup_error or "WinEvent accessibility hook is not active"
            self._event_sink(ChatEvent("event_monitor_error", failed, ChatState.IDLE))
        elif not self._event_monitor_ready_reported:
            self._event_monitor_ready_reported = True
            ready = ChatWatch(**vars(self._watches[int(hwnd)]))
            self._event_sink(ChatEvent("event_monitor_ready", ready, ready.state))
        self._wake.set()

    def remove(self, hwnd: int) -> None:
        with self._lock:
            self._watches.pop(int(hwnd), None)
        self._sync_event_watch_set()
        self._wake.set()

    def acknowledge(self, hwnd: int) -> None:
        with self._lock:
            watch = self._watches.get(int(hwnd))
            if watch:
                watch.acknowledged = True

    def watches(self) -> list[ChatWatch]:
        with self._lock:
            return [ChatWatch(**vars(watch)) for watch in self._watches.values()]

    def watches_for_project(self, project_id: str) -> list[ChatWatch]:
        return [watch for watch in self.watches() if watch.project_id == project_id]

    def download_latest(self, hwnd: int, match_terms: Iterable[str] = ()) -> list[str]:
        """Invoke the newest response Download control through accessibility only.

        The user explicitly triggers this action. We do a fresh MSAA/IA2 scan
        of the managed ChatGPT document and, when available, anchor that scan to
        the exact composer/document identity retained from the response. No
        keyboard input, browser debugging protocol, or automation profile is
        involved. If the newest response cannot be proven to contain a current
        Download control, the action returns no match rather than invoking a
        cached historical artifact.
        """
        if os.name != "nt" or not windows_hwnd_exists(int(hwnd)):
            return []

        with self._lock:
            watch = self._watches.get(int(hwnd))
            document_key = ""
            anchor_source_hwnd = 0
            anchor_object_id = 0
            anchor_child_id = 0
            if watch is not None:
                document_key = (
                    watch.last_response_document_key
                    or watch.active_response_document_key
                    or watch.document_key
                )
                anchor_source_hwnd = (
                    watch.last_response_source_hwnd
                    or watch.active_response_source_hwnd
                )
                anchor_object_id = (
                    watch.last_response_object_id
                    or watch.active_response_object_id
                )
                anchor_child_id = (
                    watch.last_response_child_id
                    or watch.active_response_child_id
                )

        clicked = download_latest_chatgpt_response_links(
            int(hwnd),
            match_terms,
            expected_document_key=document_key,
            anchor_source_hwnd=anchor_source_hwnd,
            anchor_object_id=anchor_object_id,
            anchor_child_id=anchor_child_id,
        )
        # Never fall back to cached historical Download events. A fresh
        # structure-aware document scan either proves a Download belongs to the
        # newest assistant response or returns no match. Clicking an older
        # cached artifact is worse than reporting that no current Download was
        # found.
        return clicked


    def detect_current(self, hwnd: int) -> tuple[ChatState, ChatAccessibilitySnapshot | None, ChatWatch | None]:
        """Perform a fresh accessibility scan without mutating watcher history."""
        if os.name != "nt" or not windows_hwnd_exists(int(hwnd)):
            return ChatState.ERROR, None, None
        snapshots = inspect_chatgpt_windows([int(hwnd)])
        snapshot = snapshots.get(int(hwnd))
        with self._lock:
            watch = self._watches.get(int(hwnd))
            watch_copy = ChatWatch(**vars(watch)) if watch is not None else None
        prior = watch_copy.state if watch_copy is not None else ChatState.IDLE
        return classify_detected_state(snapshot, prior), snapshot, watch_copy

    def cached_download_names(self, hwnd: int) -> list[str]:
        return self._event_monitor.cached_download_names(int(hwnd))

    def _emit(self, kind: str, watch: ChatWatch, previous: ChatState) -> None:
        self._event_sink(ChatEvent(kind, ChatWatch(**vars(watch)), previous))

