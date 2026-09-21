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
from .state_machine import ERROR_RE, is_message_delivery_timeout

class ChatEventRouterMixin:
    def _route_msaa_event_locked(self, event: MsaaWinEvent) -> ChatWatch | None:
        """Route an event without ever guessing from foreground Firefox state."""
        direct = self._watches.get(int(event.top_hwnd)) if event.top_hwnd else None
        if direct is not None:
            return direct

        # Firefox can report an accessibility event from a content HWND which
        # has no Win32 parent relationship to the top-level browser window. In
        # that case, route only when the document identity uniquely matches one
        # managed ChatGPT URL. Never pick a foreground/only watched window.
        if event.document_key:
            matches: list[ChatWatch] = []
            for candidate in self._watches.values():
                keys = {
                    candidate.expected_document_key,
                    candidate.document_key,
                    candidate.active_response_document_key,
                }
                if event.document_key in keys:
                    matches.append(candidate)
            if len(matches) == 1:
                return matches[0]
        return None

    @staticmethod
    def _same_active_composer_object(watch: ChatWatch, event: MsaaWinEvent) -> bool:
        """Return True when *event* is from the composer object that started this response.

        ChatGPT keeps the same ``button#composer-submit-button`` while its
        accessible name changes from ``Stop answering`` back to ``Send prompt``.
        WinEvent gives us the source HWND/object id/child id for that exact
        accessible object, which is a stronger tab discriminator than the
        Firefox top-level HWND (one Firefox window can contain many tabs).
        """
        if not watch.active_response_source_hwnd:
            return False
        return (
            int(event.source_hwnd or 0) == int(watch.active_response_source_hwnd)
            and int(event.object_id) == int(watch.active_response_object_id)
            and int(event.child_id) == int(watch.active_response_child_id)
        )

    def _event_belongs_to_watch_locked(self, watch: ChatWatch, event: MsaaWinEvent, lower: str) -> bool:
        doc_key = event.document_key
        same_composer = self._same_active_composer_object(watch, event)

        # Once a response is running, the exact composer accessibility object is
        # authoritative. This fixes the common Firefox case where the containing
        # document URL/title is omitted from the final NameChange event.
        if lower in {"send prompt", "start voice"}:
            if watch.state == ChatState.IN_PROGRESS or watch.saw_progress:
                if same_composer:
                    return True
                if watch.active_response_document_key and doc_key:
                    return doc_key == watch.active_response_document_key
                # Do not finish from another tab merely because it shares the
                # same Firefox top-level HWND.
                return False

        if ERROR_RE.search(lower) and (watch.state == ChatState.IN_PROGRESS or watch.saw_progress):
            if watch.active_response_document_key and doc_key:
                return doc_key == watch.active_response_document_key
            # Error text is not the composer object, so identity-less errors are
            # ambiguous across tabs and must not mutate this watch.
            return False

        # If both sides expose a real URL identity, it must be the exact saved
        # ChatGPT conversation URL.
        if doc_key.startswith("url:") and watch.expected_document_key.startswith("url:"):
            if doc_key != watch.expected_document_key:
                return False

        # Once the managed chat's document is learned, a different document in
        # the same Firefox window is never allowed to mutate its state.
        if doc_key and watch.document_key and doc_key != watch.document_key:
            # Upgrade a title-only binding to the stronger URL binding when the
            # event is otherwise tied to this exact top-level HWND/source.
            if not (watch.document_key.startswith("title:") and doc_key.startswith("url:") and event.top_hwnd == watch.hwnd):
                return False

        if lower == "stop answering" and (watch.state == ChatState.IN_PROGRESS or watch.saw_progress):
            if same_composer:
                return True
            if watch.active_response_document_key and doc_key:
                return doc_key == watch.active_response_document_key
            # A Stop event from an unidentifiable second tab must not steal the
            # response currently associated with this managed URL.
            return False

        # Initial state/start events still require either the captured top-level
        # HWND or a unique document route.
        return bool(event.top_hwnd == watch.hwnd or doc_key)

    @staticmethod
    def _complete_response_watch(watch: ChatWatch, event: MsaaWinEvent, state: ChatState) -> None:
        if event.document_key:
            watch.last_response_document_key = event.document_key
        elif watch.active_response_document_key:
            watch.last_response_document_key = watch.active_response_document_key
        watch.state = state
        watch.saw_progress = False
        watch.acknowledged = False
        watch.completed_at = time.time()
        watch.in_progress_since = 0.0
        watch.active_response_document_key = ""
        watch.active_response_source_hwnd = 0
        watch.active_response_object_id = 0
        watch.active_response_child_id = 0

    def _on_msaa_event(self, event: MsaaWinEvent) -> None:
        retry_timeout_hwnd = 0
        with self._lock:
            watch = self._route_msaa_event_locked(event)
            if watch is None:
                return

            # A hide/destroy for the exact Stop-answering object is a strong
            # signal that its lifecycle changed. It is not by itself proof of
            # completion (switching tabs can hide accessibles), so wake the
            # exact retained-document probe immediately instead of mutating the
            # state from another tab.
            if event.event_id in {EVENT_OBJECT_HIDE, EVENT_OBJECT_DESTROY}:
                if self._same_active_composer_object(watch, event):
                    self._wake.set()
                return

            previous = watch.state
            lower = event.name.strip().casefold()
            # Firefox publishes a very large number of accessibility changes
            # unrelated to ChatGPT response state (layout, selection, live
            # regions, etc.). Older builds routed all of those into Tk as
            # ``update`` events, which could create a large queue while the UI
            # was busy with extraction/log painting. Ignore them at the source.
            relevant = (
                lower in {
                    "stop answering", "send prompt", "start voice",
                    "response actions", "copy response",
                }
                or lower.startswith("download")
                or lower.startswith("worked for ")
                or bool(ERROR_RE.search(lower))
            )
            if not relevant:
                return
            if not self._event_belongs_to_watch_locked(watch, event, lower):
                return

            watch.initialized = True
            watch.accessibility_backend = "winevent"
            if event.document_key:
                if not watch.document_key or (watch.document_key.startswith("title:") and event.document_key.startswith("url:")):
                    watch.document_key = event.document_key
                watch.document_name = event.document_name or watch.document_name

            if lower == "stop answering":
                # Clear retained Download controls only after this Stop event has
                # been proven to belong to this exact managed chat document.
                self._event_monitor.clear_downloads(watch.hwnd)
                watch.composer_state = "stop"
                watch.composer_name = event.name
                watch.state = ChatState.IN_PROGRESS
                watch.saw_progress = True
                watch.acknowledged = False
                watch.error_text = ""
                watch.timeout_retry_attempts = 0
                watch.timeout_retry_last_attempt = 0.0
                watch.timeout_retry_clicked = False
                watch.download_count = 0
                watch.worked_for_text = ""
                # Bind to identities actually observed for this tab. The saved
                # URL is an expectation, not proof that this WinEvent came from
                # that tab, so never substitute expected_document_key here.
                watch.active_response_document_key = event.document_key or watch.document_key
                watch.active_response_source_hwnd = int(event.source_hwnd or 0)
                watch.active_response_object_id = int(event.object_id)
                watch.active_response_child_id = int(event.child_id)
                watch.last_response_source_hwnd = watch.active_response_source_hwnd
                watch.last_response_object_id = watch.active_response_object_id
                watch.last_response_child_id = watch.active_response_child_id
                watch.last_response_document_key = watch.active_response_document_key
                if previous != ChatState.IN_PROGRESS or watch.in_progress_since <= 0.0:
                    watch.in_progress_since = time.monotonic()

            elif lower in {"send prompt", "start voice"}:
                same_composer = self._same_active_composer_object(watch, event)
                watch.accessibility_backend = "winevent-object" if same_composer else "winevent-document"
                watch.composer_state = "send" if lower == "send prompt" else "voice"
                watch.composer_name = event.name
                if previous == ChatState.IN_PROGRESS or watch.saw_progress:
                    self._complete_response_watch(watch, event, ChatState.SUCCESS)
                elif previous in {ChatState.IDLE, ChatState.UNAVAILABLE}:
                    watch.state = ChatState.IDLE

            elif lower.startswith("worked for ") or lower in {"response actions", "copy response"}:
                if lower.startswith("worked for "):
                    watch.worked_for_text = event.name[len("Worked for "):].strip()
                # These controls belong to the finalized assistant response.
                # Force an immediate exact tree scan so the complete structural
                # signature can be validated as one unit.
                self._next_full_scan = 0.0
                self._wake.set()

            elif lower.startswith("download"):
                watch.download_count = len(self._event_monitor.cached_download_names(watch.hwnd))

            elif ERROR_RE.search(lower):
                watch.error_text = event.name
                if previous == ChatState.IN_PROGRESS or watch.saw_progress:
                    self._complete_response_watch(watch, event, ChatState.ERROR)
                if is_message_delivery_timeout(event.name):
                    retry_timeout_hwnd = int(watch.hwnd)

            current = ChatWatch(**vars(watch))

        if retry_timeout_hwnd:
            self._attempt_timeout_retry(retry_timeout_hwnd)

        if current.state != previous:
            self._event_sink(ChatEvent("state", current, previous))
        elif lower in {"stop answering", "send prompt", "start voice"}:
            self._event_sink(ChatEvent("accessibility_event", current, previous))
        else:
            self._event_sink(ChatEvent("update", current, previous))

