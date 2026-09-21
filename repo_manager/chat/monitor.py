from __future__ import annotations
import os
import threading
import time
from typing import Callable
from ..platform.windows.msaa import MsaaWinEventMonitor, invoke_chatgpt_retry_msaa
from .models import ChatEvent, ChatState, ChatWatch
from .registry import ChatRegistryMixin
from .event_router import ChatEventRouterMixin
from .polling import ChatPollingMixin

class ChatWatchManager(ChatRegistryMixin, ChatEventRouterMixin, ChatPollingMixin):
    """Coordinate ChatGPT window registry, accessibility events, and fallback polling."""
    def __init__(self, event_sink: Callable[[ChatEvent], None], interval: float = 0.5):
        self._event_sink = event_sink
        self._interval = max(0.2, float(interval))
        self._lock = threading.RLock()
        self._watches: dict[int, ChatWatch] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_scan_error = ""
        self._last_event_error = ""
        self._last_event_restart = 0.0
        self._event_monitor_ready_reported = False
        self._next_full_scan = 0.0
        self._event_monitor = MsaaWinEventMonitor(self._on_msaa_event)

    def _attempt_timeout_retry(self, hwnd: int) -> bool:
        """Click ChatGPT Retry once the timeout UI is proven for this window.

        Firefox may expose the timeout text before the Retry text leaf.  Permit
        up to three spaced attempts; after a successful click, do not invoke it
        again until a new response lifecycle resets the retry bookkeeping.
        """
        now = time.monotonic()
        with self._lock:
            watch = self._watches.get(int(hwnd))
            if watch is None or watch.timeout_retry_clicked:
                return False
            if watch.timeout_retry_attempts >= 3:
                return False
            if watch.timeout_retry_last_attempt and now - watch.timeout_retry_last_attempt < 1.0:
                return False
            watch.timeout_retry_attempts += 1
            watch.timeout_retry_last_attempt = now

        clicked = bool(invoke_chatgpt_retry_msaa(int(hwnd)))
        if clicked:
            with self._lock:
                watch = self._watches.get(int(hwnd))
                if watch is not None:
                    watch.timeout_retry_clicked = True
                    current = ChatWatch(**vars(watch))
                else:
                    current = None
            if current is not None:
                self._event_sink(ChatEvent("retry_clicked", current, current.state))
        return clicked

    def start(self) -> None:
        if os.name != "nt" or (self._thread and self._thread.is_alive()):
            return
        self._event_monitor.start()
        self._thread = threading.Thread(
            target=self._run,
            name="chatgpt-accessibility-watch",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._event_monitor.stop()

