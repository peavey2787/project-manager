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

def _reset_timeout_retry(watch: ChatWatch) -> None:
    watch.timeout_retry_attempts = 0
    watch.timeout_retry_last_attempt = 0.0
    watch.timeout_retry_clicked = False
    watch.error_text = ""


def _merge_snapshot_metadata(
    watch: ChatWatch, snapshot: ChatAccessibilitySnapshot, snapshot_doc: str, event_authoritative: bool
) -> bool:
    if snapshot_doc:
        if not watch.document_key or (watch.document_key.startswith("title:") and snapshot_doc.startswith("url:")):
            watch.document_key = snapshot_doc
        watch.document_name = snapshot.document_name or watch.document_name
    watch.download_count = max(watch.download_count, snapshot.download_count)
    if snapshot.worked_for_text:
        watch.worked_for_text = snapshot.worked_for_text
    if snapshot.response_fingerprint:
        watch.response_fingerprint = snapshot.response_fingerprint
        watch.response_text_length = snapshot.response_text_length
    decisive = snapshot.composer_state in {"stop", "send", "voice"}
    retained_exact = snapshot.backend == "msaa-retained"
    if decisive and (retained_exact or not event_authoritative or snapshot_doc or not watch.document_key):
        watch.composer_state = snapshot.composer_state
        if retained_exact or not event_authoritative:
            watch.accessibility_backend = snapshot.backend
        watch.composer_name = snapshot.composer_name
    if snapshot.error_text and not watch.error_text:
        watch.error_text = snapshot.error_text
    return retained_exact


def _mark_snapshot_terminal(watch: ChatWatch, state: ChatState, response_document: str, snapshot_doc: str) -> None:
    document = response_document or snapshot_doc
    if document:
        watch.last_response_document_key = document
    watch.state = state
    watch.saw_progress = False
    watch.acknowledged = False
    watch.completed_at = time.time()
    watch.in_progress_since = 0.0
    watch.active_response_document_key = ""
    watch.active_response_source_hwnd = 0
    watch.active_response_object_id = 0
    watch.active_response_child_id = 0

class ChatPollingMixin:
    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                current = dict(self._watches)

            if current:
                # Keep the WinEvent hook alive. If it crashed or Windows tore
                # it down, restart it automatically and preserve the watch set.
                now = time.monotonic()
                if not self._event_monitor.active and now - self._last_event_restart >= 3.0:
                    self._last_event_restart = now
                    self._event_monitor.start()
                    self._sync_event_watch_set()
                    if not self._event_monitor.active:
                        message = self._event_monitor.startup_error or "WinEvent accessibility hook is not active"
                        self._event_monitor_ready_reported = False
                        if message != self._last_event_error:
                            self._last_event_error = message
                            sample = ChatWatch(**vars(next(iter(current.values()))))
                            sample.error_text = message
                            self._event_sink(ChatEvent("event_monitor_error", sample, sample.state))
                    else:
                        self._last_event_error = ""
                        if not self._event_monitor_ready_reported:
                            self._event_monitor_ready_reported = True
                            sample = ChatWatch(**vars(next(iter(current.values()))))
                            self._event_sink(ChatEvent("event_monitor_ready", sample, sample.state))

                callback_error = self._event_monitor.last_callback_error
                if callback_error and callback_error != self._last_event_error:
                    self._last_event_error = callback_error
                    sample = ChatWatch(**vars(next(iter(current.values()))))
                    sample.error_text = callback_error
                    self._event_sink(ChatEvent("event_callback_error", sample, sample.state))

                dead = [hwnd for hwnd in current if not windows_hwnd_exists(hwnd)]
                if dead:
                    for hwnd in dead:
                        with self._lock:
                            removed = self._watches.pop(hwnd, None)
                        if removed:
                            self._event_sink(ChatEvent("closed", ChatWatch(**vars(removed)), removed.state))
                        current.pop(hwnd, None)
                    self._sync_event_watch_set()

                # Active composer polling: once Stop answering has been observed,
                # do NOT wait for Firefox to emit another accessibility event.
                # First ask the *retained exact document/composer* living on the
                # WinEvent COM thread. This remains tied to the originating tab
                # even when the user activates another Firefox tab.
                running = [
                    ChatWatch(**vars(watch))
                    for watch in current.values()
                    if (watch.state == ChatState.IN_PROGRESS or watch.saw_progress)
                ]
                for watch in running:
                    retained = None
                    try:
                        retained = self._event_monitor.probe_retained(watch.hwnd, timeout=1.2)
                    except Exception:
                        retained = None
                    if retained is not None:
                        snapshot = ChatAccessibilitySnapshot(
                            hwnd=watch.hwnd,
                            available=bool(retained.available),
                            in_progress=retained.composer_state == "stop",
                            composer_state=retained.composer_state,
                            error_text=retained.error_text,
                            response_fingerprint=retained.response_fingerprint,
                            response_text_length=retained.response_text_length,
                            download_count=retained.download_count,
                            backend="msaa-retained",
                            composer_name=retained.composer_name,
                            document_name=retained.document_name,
                            document_value=retained.document_value,
                            document_key=retained.document_key,
                            worked_for_text=retained.worked_for_text,
                            latest_response_complete=retained.latest_response_complete,
                        )
                        self._apply_snapshots({watch.hwnd: snapshot})
                        if retained.composer_state in {"send", "voice"}:
                            continue

                    # Secondary direct tuple probe retained for Firefox builds
                    # where the live event object itself updates correctly.
                    direct = None
                    if watch.active_response_source_hwnd:
                        try:
                            direct = probe_event_object_msaa(
                                watch.hwnd,
                                watch.active_response_source_hwnd,
                                watch.active_response_object_id,
                                watch.active_response_child_id,
                            )
                        except Exception:
                            direct = None
                    if direct is not None and direct.name.strip().casefold() in {
                        "stop answering", "send prompt", "start voice"
                    }:
                        self._on_msaa_event(direct)
                        # Send/Voice on the exact object has already completed it.
                        if direct.name.strip().casefold() in {"send prompt", "start voice"}:
                            continue

                    # ChatGPT can replace the Stop button object entirely with a
                    # Start Voice button. In that case, probe only the exact
                    # response document learned at Stop time—never another tab.
                    with self._lock:
                        live = self._watches.get(watch.hwnd)
                        still_running = bool(
                            live and (live.state == ChatState.IN_PROGRESS or live.saw_progress)
                        )
                        expected_doc = live.active_response_document_key if live else ""
                        preferred_source = live.active_response_source_hwnd if live else 0
                    if still_running and expected_doc:
                        try:
                            row = probe_composer_msaa(
                                watch.hwnd,
                                expected_document_key=expected_doc,
                                preferred_source_hwnd=preferred_source,
                            )
                        except Exception:
                            row = None
                        if row is not None:
                            snapshot = ChatAccessibilitySnapshot(
                                hwnd=watch.hwnd,
                                available=bool(row.available),
                                in_progress=row.composer_state == "stop",
                                composer_state=row.composer_state,
                                error_text=row.error_text,
                                response_fingerprint=row.response_fingerprint,
                                response_text_length=row.response_text_length,
                                download_count=row.download_count,
                                backend="msaa-active-probe",
                                composer_name=row.composer_name,
                                document_name=row.document_name,
                                document_value=row.document_value,
                                document_key=row.document_key,
                                worked_for_text=row.worked_for_text,
                                latest_response_complete=row.latest_response_complete,
                            )
                            self._apply_snapshots({watch.hwnd: snapshot})

                # Broad discovery is deliberately slower than the exact active
                # probe above. It finds initial Send/Voice/Stop state for newly
                # opened windows without repeatedly walking every Firefox tree.
                now = time.monotonic()
                if current and now >= self._next_full_scan:
                    self._next_full_scan = now + 1.5
                    try:
                        snapshots = inspect_chatgpt_windows(current.keys())
                        self._last_scan_error = ""
                        self._apply_snapshots(snapshots)
                    except Exception as exc:
                        message = str(exc)
                        if message != self._last_scan_error:
                            self._last_scan_error = message
                            sample = next(iter(current.values()))
                            self._event_sink(
                                ChatEvent(
                                    "monitor_error",
                                    ChatWatch(**vars(sample)),
                                    sample.state,
                                )
                            )

            self._wake.wait(self._interval)
            self._wake.clear()

    def _apply_snapshots(self, snapshots: dict[int, ChatAccessibilitySnapshot]) -> None:
        events: list[ChatEvent] = []
        timeout_retry_hwnds: list[int] = []
        with self._lock:
            for hwnd, watch in list(self._watches.items()):
                snapshot = snapshots.get(hwnd)
                if snapshot is None:
                    continue

                previous = watch.state
                was_initialized = watch.initialized
                event_authoritative = watch.accessibility_backend == "winevent"

                # Reject a snapshot from a different tab/document inside the same
                # Firefox HWND. A tab switch must never mean the watched response
                # finished. URL identities are strongest; title identities are
                # used when Firefox does not expose the document URL.
                snapshot_doc = snapshot.document_key
                if snapshot_doc.startswith("url:") and watch.expected_document_key.startswith("url:"):
                    if snapshot_doc != watch.expected_document_key:
                        continue
                if snapshot_doc and watch.document_key and snapshot_doc != watch.document_key:
                    if not (watch.document_key.startswith("title:") and snapshot_doc.startswith("url:")):
                        continue
                if (watch.state == ChatState.IN_PROGRESS or watch.saw_progress) and watch.active_response_document_key:
                    if snapshot_doc and snapshot_doc != watch.active_response_document_key:
                        continue

                retained_exact = _merge_snapshot_metadata(
                    watch, snapshot, snapshot_doc, event_authoritative
                )

                if is_message_delivery_timeout(snapshot.error_text):
                    watch.initialized = True
                    watch.error_text = snapshot.error_text
                    _mark_snapshot_terminal(watch, ChatState.ERROR, watch.active_response_document_key or watch.document_key, snapshot_doc)
                    if not watch.timeout_retry_clicked and watch.timeout_retry_attempts < 3:
                        timeout_retry_hwnds.append(int(hwnd))

                elif not snapshot.available:
                    if not watch.initialized and not event_authoritative:
                        watch.state = ChatState.UNAVAILABLE

                elif not watch.initialized:
                    watch.initialized = True
                    watch.state = ChatState.IN_PROGRESS if snapshot.in_progress else ChatState.IDLE
                    watch.saw_progress = snapshot.in_progress
                    if snapshot.in_progress:
                        _reset_timeout_retry(watch)
                        watch.active_response_document_key = snapshot_doc or watch.document_key
                        watch.active_response_source_hwnd = 0
                        watch.active_response_object_id = 0
                        watch.active_response_child_id = 0
                    watch.in_progress_since = time.monotonic() if snapshot.in_progress else 0.0

                else:
                    # A periodic tree scan is allowed to complete a response
                    # only when it can prove it is looking at the same tab. A
                    # top-level Firefox HWND alone is never enough because the
                    # user may have selected another tab in that window.
                    response_document = watch.active_response_document_key or watch.document_key
                    same_response_document = retained_exact or bool(
                        response_document and snapshot_doc and snapshot_doc == response_document
                    )
                    if (
                        not same_response_document
                        and snapshot_doc.startswith("url:")
                        and watch.expected_document_key.startswith("url:")
                    ):
                        same_response_document = snapshot_doc == watch.expected_document_key

                    # Firefox sometimes exposes the complete finalized response
                    # structure in the managed window tree while omitting the
                    # containing-document identity from that exact scan. We have
                    # already rejected any *explicitly different* document above.
                    # Therefore a full newest-response signature is authoritative
                    # for an already-running watch even when snapshot_doc is blank.
                    structural_completion = bool(
                        snapshot.latest_response_complete
                        and snapshot.worked_for_text
                        and not snapshot_doc
                        and (previous == ChatState.IN_PROGRESS or watch.saw_progress)
                    )

                    if snapshot.in_progress and (same_response_document or not watch.active_response_document_key):
                        watch.state = ChatState.IN_PROGRESS
                        watch.saw_progress = True
                        watch.acknowledged = False
                        _reset_timeout_retry(watch)
                        if previous != ChatState.IN_PROGRESS:
                            watch.worked_for_text = ""
                        if not watch.active_response_document_key:
                            watch.active_response_document_key = snapshot_doc or watch.document_key
                            watch.active_response_source_hwnd = 0
                            watch.active_response_object_id = 0
                            watch.active_response_child_id = 0
                        if previous != ChatState.IN_PROGRESS or watch.in_progress_since <= 0.0:
                            watch.in_progress_since = time.monotonic()
                    elif snapshot.error_text and (watch.saw_progress or previous == ChatState.IN_PROGRESS) and same_response_document:
                        _mark_snapshot_terminal(watch, ChatState.ERROR, response_document, snapshot_doc)
                    elif ((previous == ChatState.IN_PROGRESS or watch.saw_progress)
                          and (snapshot.composer_state in {"send", "voice"} or snapshot.latest_response_complete)
                          and (same_response_document or structural_completion)):
                        if structural_completion:
                            watch.composer_state = snapshot.composer_state or "voice"
                            watch.composer_name = snapshot.composer_name or "Start Voice"
                            watch.accessibility_backend = snapshot.backend
                        _mark_snapshot_terminal(watch, ChatState.SUCCESS, response_document, snapshot_doc)
                    elif previous == ChatState.IN_PROGRESS or watch.saw_progress:
                        # Unknown or identity-less scan result is not completion.
                        watch.state = ChatState.IN_PROGRESS
                    elif not event_authoritative and previous == ChatState.UNAVAILABLE:
                        watch.state = ChatState.IDLE
                        watch.in_progress_since = 0.0

                if not was_initialized and watch.initialized:
                    events.append(ChatEvent("initialized", ChatWatch(**vars(watch)), previous))
                if watch.state != previous:
                    events.append(ChatEvent("state", ChatWatch(**vars(watch)), previous))
                # Unchanged polling snapshots do not need to be queued to Tk.
                # The GUI reads current watch state directly for flashing/time
                # thresholds, so suppressing heartbeat-style update events keeps
                # memory usage bounded when the main thread is temporarily busy.

        for hwnd in dict.fromkeys(timeout_retry_hwnds):
            self._attempt_timeout_retry(hwnd)

        for event in events:
            self._event_sink(event)

