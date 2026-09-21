from __future__ import annotations
import ctypes
import os
import queue
import threading
from .bindings import *
from .browser_url import *
from .constants import *
from .downloads import *
from .models import MsaaFocusEvent, MsaaWinEvent, _ActionTarget, _InvokeDownloadsCommand, _ProbeRetainedCommand
from .tree import *

class _POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]

class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", PVOID),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", DWORD),
        ("pt", _POINT),
        ("lPrivate", DWORD),
    ]

class MsaaWinEventMonitor:
    """Event-driven Firefox accessibility monitor.

    Tree walking is deliberately not required for the primary ChatGPT signal.
    Firefox raises WinEvents when accessible controls appear or their Name
    changes. We resolve the event source directly with AccessibleObjectFromEvent
    and map its HWND back to one of the managed top-level browser windows.
    """

    def __init__(self, callback):
        self._callback = callback
        self._lock = threading.RLock()
        self._watched: set[int] = set()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._hook = 0
        self._proc = None
        self._commands: queue.Queue[_InvokeDownloadsCommand] = queue.Queue()
        self._downloads: dict[int, list[_ActionTarget]] = {}
        # Retain the exact composer/document accessibles on the hook thread.
        # Firefox may delay a NameChange event until hover/focus, but these
        # live COM objects can still be queried proactively without touching a
        # different browser tab.
        self._active_composers: dict[int, int] = {}
        self._documents: dict[int, int] = {}
        self._document_keys: dict[int, str] = {}
        # Screen point at the center of the exact Stop-answering control. The
        # retained document can accHitTest this point to force Firefox to
        # materialize Send prompt / Start Voice without mouse hover.
        self._composer_points: dict[int, tuple[int, int]] = {}
        self._focus_events: dict[int, MsaaFocusEvent] = {}
        self._focus_sequence = 0
        self._source_to_top: dict[int, int] = {}
        self._startup_error = ""
        self._last_callback_error = ""

    def start(self) -> None:
        if os.name != "nt" or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._ready.clear()
        self._hook = 0
        self._thread_id = 0
        self._startup_error = ""
        self._last_callback_error = ""
        self._thread = threading.Thread(target=self._run, name="chatgpt-msaa-events", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=3.0):
            self._startup_error = "WinEvent hook startup timed out"

    @property
    def active(self) -> bool:
        return bool(self._hook and self._thread and self._thread.is_alive())

    @property
    def startup_error(self) -> str:
        return self._startup_error

    @property
    def last_callback_error(self) -> str:
        return self._last_callback_error

    def stop(self) -> None:
        if os.name != "nt":
            return
        self._stop.set()
        tid = self._thread_id
        if tid:
            try:
                _, _, _, user32 = _dlls()
                user32.PostThreadMessageW(DWORD(tid), WM_QUIT, 0, 0)
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def set_watched(self, hwnds: Iterable[int]) -> None:
        new_values = {int(value) for value in hwnds if int(value) > 0}
        with self._lock:
            removed = set(self._downloads) - new_values
            self._watched = new_values
            self._source_to_top = {
                source: top for source, top in self._source_to_top.items() if top in new_values
            }
            for hwnd in removed:
                self._release_downloads_locked(hwnd)
                self._release_retained_locked(hwnd)
                self._focus_events.pop(hwnd, None)

    def invoke_downloads(
        self,
        hwnd: int,
        match_terms: Iterable[str] = (),
        expected_document_key: str = "",
        timeout: float = 5.0,
    ) -> list[str]:
        if os.name != "nt" or not hwnd:
            return []
        command = _InvokeDownloadsCommand(
            hwnd=int(hwnd),
            match_terms=tuple(str(v).strip() for v in match_terms if str(v).strip()),
            expected_document_key=str(expected_document_key or ""),
            done=threading.Event(),
            clicked=[],
        )
        self._commands.put(command)
        tid = self._thread_id
        if tid:
            try:
                _, _, _, user32 = _dlls()
                user32.PostThreadMessageW(DWORD(tid), WM_MSAA_COMMAND, 0, 0)
            except Exception:
                pass
        command.done.wait(timeout=max(0.1, float(timeout)))
        return list(command.clicked)

    def cached_download_names(self, hwnd: int) -> list[str]:
        with self._lock:
            return [target.name for target in self._downloads.get(int(hwnd), [])]

    def focus_event(self, hwnd: int) -> MsaaFocusEvent | None:
        with self._lock:
            return self._focus_events.get(int(hwnd))

    def probe_retained(self, hwnd: int, timeout: float = 1.5) -> MsaaTreeSnapshot | None:
        """Probe the exact chat document retained when Stop answering appeared.

        The work is executed on the WinEvent thread, i.e. the COM apartment
        where Firefox supplied the accessibility objects.  This avoids both
        cross-tab rediscovery and Firefox's lazy hover-triggered event issue.
        """
        if os.name != "nt" or not hwnd or not self.active:
            return None
        command = _ProbeRetainedCommand(hwnd=int(hwnd), done=threading.Event())
        self._commands.put(command)
        tid = self._thread_id
        if tid:
            try:
                _, _, _, user32 = _dlls()
                user32.PostThreadMessageW(DWORD(tid), WM_MSAA_COMMAND, 0, 0)
            except Exception:
                pass
        command.done.wait(timeout=max(0.1, float(timeout)))
        return command.snapshot

    def clear_downloads(self, hwnd: int) -> None:
        self._clear_downloads(int(hwnd))

    def _release_downloads_locked(self, hwnd: int) -> None:
        targets = self._downloads.pop(int(hwnd), [])
        _release_actions(targets)

    def _release_retained_locked(self, hwnd: int) -> None:
        composer = self._active_composers.pop(int(hwnd), 0)
        document = self._documents.pop(int(hwnd), 0)
        self._document_keys.pop(int(hwnd), None)
        self._composer_points.pop(int(hwnd), None)
        if composer:
            _release(composer)
        if document:
            _release(document)

    def _remember_exact_chat_locked(self, hwnd: int, acc_ptr: int, child: VARIANT, document_key: str) -> None:
        """Retain the exact composer + containing document for one watched chat."""
        rect = _acc_location(acc_ptr, child)
        concrete = _materialize_accessible_child(acc_ptr, child)
        document = _containing_document_accessible(acc_ptr, child)
        old_composer = self._active_composers.pop(int(hwnd), 0)
        old_document = self._documents.pop(int(hwnd), 0)
        if old_composer:
            _release(old_composer)
        if old_document:
            _release(old_document)
        if concrete:
            self._active_composers[int(hwnd)] = concrete
        if document:
            self._documents[int(hwnd)] = document
            if not document_key:
                self_child = _self_variant()
                document_key = make_document_key(
                    _acc_name(document, self_child).strip(),
                    _acc_value(document, self_child).strip(),
                )
        if document_key:
            self._document_keys[int(hwnd)] = document_key
        if rect:
            left, top, width, height = rect
            self._composer_points[int(hwnd)] = (left + width // 2, top + height // 2)

    def _clear_downloads(self, hwnd: int) -> None:
        with self._lock:
            self._release_downloads_locked(hwnd)

    def _remember_download(self, hwnd: int, target: _ActionTarget) -> bool:
        with self._lock:
            rows = self._downloads.setdefault(int(hwnd), [])
            # WinEvents can repeat for show/name/state changes. Keep one
            # retained target per accessible Download name for this response.
            for existing in rows:
                if existing.name.casefold() == target.name.casefold():
                    return False
            rows.append(target)
            return True

    def _resolve_watched_top(self, event_hwnd: int) -> int:
        """Resolve an event HWND to a watched top-level browser HWND.

        This deliberately never guesses from the foreground window. Firefox can
        host multiple tabs in one process/window, and a foreground fallback can
        assign an event from one tab/window to another managed chat. Ambiguous
        events are returned with top_hwnd=0 and are routed later by document URL.
        """
        if not event_hwnd:
            return 0
        _, _, _, user32 = _dlls()
        with self._lock:
            watched = tuple(self._watched)
            learned = self._source_to_top.get(int(event_hwnd), 0)
        if not watched:
            return 0
        if learned in watched:
            return learned
        if int(event_hwnd) in watched:
            return int(event_hwnd)
        roots: list[int] = []
        for mode in (GA_ROOT, GA_ROOTOWNER):
            try:
                root = int(user32.GetAncestor(PVOID(int(event_hwnd)), mode) or 0)
            except Exception:
                root = 0
            if root and root not in roots:
                roots.append(root)
        for root in roots:
            if root in watched:
                with self._lock:
                    self._source_to_top[int(event_hwnd)] = root
                return root
        for candidate in watched:
            try:
                if user32.IsChild(PVOID(candidate), PVOID(int(event_hwnd))):
                    with self._lock:
                        self._source_to_top[int(event_hwnd)] = candidate
                    return candidate
            except Exception:
                continue
        return 0

    def _accessible_event_target(self, hwnd: int, object_id: int, child_id: int):
        return _accessible_from_event_target(hwnd, object_id, child_id)

    def _handle_event(self, event_id: int, event_hwnd: int, object_id: int, child_id: int) -> None:
        # HIDE/DESTROY frequently arrive after the target has already become
        # unavailable through AccessibleObjectFromEvent.  Forward the raw
        # identity anyway; ChatWatchManager can correlate it with the exact
        # Stop-answering tuple that started the response.  Do not guess a
        # completion here because hiding can also happen when switching tabs.
        if event_id in {EVENT_OBJECT_HIDE, EVENT_OBJECT_DESTROY}:
            top = self._resolve_watched_top(event_hwnd)
            try:
                self._callback(
                    MsaaWinEvent(
                        top_hwnd=top,
                        event_id=int(event_id),
                        source_hwnd=int(event_hwnd),
                        object_id=int(object_id),
                        child_id=int(child_id),
                        name="",
                        role=0,
                        default_action="",
                    )
                )
            except Exception:
                pass
            # Still try to resolve the object below.  Firefox sometimes keeps
            # it readable long enough to provide a useful final name.
        acc_ptr = 0
        child = VARIANT()
        retained = False
        try:
            acc_ptr, child = self._accessible_event_target(event_hwnd, object_id, child_id)
            if not acc_ptr:
                return
            name = _acc_name(acc_ptr, child).strip()
            if not name:
                return
            lower = name.casefold()
            top = self._resolve_watched_top(event_hwnd)
            role = _acc_role(acc_ptr, child)
            default_action = _acc_default_action(acc_ptr, child)

            if event_id == EVENT_OBJECT_FOCUS and top:
                with self._lock:
                    self._focus_sequence += 1
                    self._focus_events[top] = MsaaFocusEvent(
                        sequence=self._focus_sequence,
                        top_hwnd=top,
                        source_hwnd=int(event_hwnd),
                        object_id=int(object_id),
                        child_id=int(child_id),
                        name=name,
                        role=role,
                        default_action=default_action,
                    )

            interesting = (
                lower in {
                    "stop answering", "send prompt", "start voice",
                    "response actions", "copy response",
                }
                or lower.startswith("download")
                or lower.startswith("worked for ")
                or bool(re.search(
                    r"(there was an error|something went wrong|network error|error generating|failed to generate|try again later|unable to load|rate limit|you.ve reached)",
                    lower,
                ))
            )
            if not interesting:
                return
            document_name, document_value, document_key = _document_identity(acc_ptr, child)
            if not top:
                try:
                    oleacc, _, _, _ = _dlls()
                    accessible_hwnd = PVOID()
                    hr = int(oleacc.WindowFromAccessibleObject(PVOID(acc_ptr), ctypes.byref(accessible_hwnd)))
                    if hr >= 0 and accessible_hwnd.value:
                        top = self._resolve_watched_top(int(accessible_hwnd.value))
                except Exception:
                    top = 0

            if lower == "stop answering" and top:
                with self._lock: self._remember_exact_chat_locked(top, acc_ptr, child, document_key)

            if lower.startswith("download") and top:
                retained_target = _ActionTarget(
                    index=int(event_id),
                    name=name,
                    role=role,
                    acc_ptr=acc_ptr,
                    child_id=int(child.value.lVal) if child.vt == VT_I4 else CHILDID_SELF,
                    document_key=document_key,
                )
                if self._remember_download(top, retained_target):
                    retained = True

            try:
                self._callback(
                    MsaaWinEvent(
                        top_hwnd=top,
                        event_id=int(event_id),
                        source_hwnd=int(event_hwnd),
                        object_id=int(object_id),
                        child_id=int(child_id),
                        name=name,
                        role=role,
                        default_action=default_action,
                        document_name=document_name,
                        document_value=document_value,
                        document_key=document_key,
                    )
                )
            except Exception:
                pass
        finally:
            try:
                _, oleaut32, _, _ = _dlls()
                oleaut32.VariantClear(ctypes.byref(child))
            except Exception:
                pass
            if acc_ptr and not retained:
                _release(acc_ptr)

    def _process_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                if isinstance(command, _ProbeRetainedCommand):
                    with self._lock:
                        composer = self._active_composers.get(command.hwnd, 0)
                        document = self._documents.get(command.hwnd, 0)
                        document_key = self._document_keys.get(command.hwnd, "")
                        composer_point = self._composer_points.get(command.hwnd)

                    # Fast path: ask the exact retained composer first.
                    if composer:
                        name = _acc_name(composer, _self_variant()).strip()
                        lower = name.casefold()
                        if lower in {"stop answering", "send prompt", "start voice"}:
                            command.snapshot = MsaaTreeSnapshot(
                                source_hwnd=command.hwnd,
                                object_id=0,
                                available=True,
                                composer_state=(
                                    "stop"
                                    if lower == "stop answering"
                                    else "send"
                                    if lower == "send prompt"
                                    else "voice"
                                ),
                                composer_name=name,
                                error_text="",
                                response_fingerprint="",
                                response_text_length=0,
                                download_count=0,
                                document_name="",
                                document_value="",
                                document_key=document_key,
                                elements=(),
                            )
                            # If the exact retained object has already changed
                            # back to Send/Voice, no document walk is needed.
                            if lower in {"send prompt", "start voice"}:
                                continue

                    # Firefox can leave the old Stop accessible cached until
                    # accessibility hit-testing reaches the replacement control
                    # (the user-observed mouse-hover behavior). Force that
                    # refresh without moving the mouse by hit-testing the exact
                    # retained chat document at the former Stop button center.
                    if document and composer_point:
                        hit_name, _hit_role = _hit_test_composer_in_document(document, composer_point)
                        hit_lower = hit_name.casefold()
                        if hit_lower in {"stop answering", "send prompt", "start voice"}:
                            command.snapshot = MsaaTreeSnapshot(
                                source_hwnd=command.hwnd,
                                object_id=0,
                                available=True,
                                composer_state=(
                                    "stop" if hit_lower == "stop answering"
                                    else "send" if hit_lower == "send prompt"
                                    else "voice"
                                ),
                                composer_name=hit_name,
                                error_text="",
                                response_fingerprint="",
                                response_text_length=0,
                                download_count=0,
                                document_name="",
                                document_value="",
                                document_key=document_key,
                                elements=(),
                            )
                            if hit_lower in {"send prompt", "start voice"}:
                                continue

                    # Firefox can replace the Stop object instead of renaming
                    # it. Walk the retained *same document* rather than the
                    # currently active tab/window to find the replacement.
                    if document:
                        _add_ref(document)
                        actions: list[_ActionTarget] = []
                        try:
                            elements, actions = _walk_tree(document, max_nodes=100000, capture_actions=True)
                            snap = _tree_snapshot(command.hwnd, OBJID_CLIENT, elements, actions)
                            if document_key and not snap.document_key:
                                snap = MsaaTreeSnapshot(
                                    **{**vars(snap), "document_key": document_key}
                                )
                            command.snapshot = snap
                        finally:
                            _release_actions(actions)
                    continue

                with self._lock:
                    rows = list(self._downloads.get(command.hwnd, []))
                if command.expected_document_key:
                    rows = [row for row in rows if row.document_key == command.expected_document_key]
                pool = [row for row in rows if _matches_terms(row.name, command.match_terms)] if command.match_terms else rows
                if not pool:
                    pool = rows
                clicked: list[str] = []
                seen: set[tuple[str, int, int]] = set()
                for row in pool:
                    key = (row.name, row.acc_ptr, row.child_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    if _invoke_target(row):
                        clicked.append(row.name)
                command.clicked[:] = clicked
            except Exception as exc:
                # A failed probe/download command must never tear down the
                # WinEvent message loop.  Surface it through the same
                # diagnostic channel used by callback failures and let the
                # watcher retry on the next cycle.
                self._last_callback_error = f"{type(exc).__name__}: {exc}"
            finally:
                command.done.set()

    def _run(self) -> None:
        if os.name != "nt":
            self._ready.set()
            return

        self._startup_error = ""
        try:
            self._run_hook_loop()
        except Exception as exc:
            self._startup_error = f"{type(exc).__name__}: {exc}"
            self._hook = 0
            self._thread_id = 0
            self._proc = None
            self._ready.set()

    def _run_hook_loop(self) -> None:
        _, _, _, user32 = _dlls()
        with _com_scope():
            # threading.get_native_id() is the Win32 thread id on Windows and
            # is exactly what PostThreadMessageW expects. GetCurrentThreadId
            # belongs to kernel32.dll, not user32.dll; using the Python API
            # avoids that DLL-binding failure entirely.
            self._thread_id = int(threading.get_native_id())
            # Force creation of this thread's Win32 message queue before the
            # hook is reported ready. This makes PostThreadMessageW reliable
            # for stop/download commands immediately after startup.
            bootstrap_msg = _MSG()
            user32.PeekMessageW(ctypes.byref(bootstrap_msg), None, 0, 0, PM_NOREMOVE)

            CALLBACK = WINFUNCTYPE(None, PVOID, DWORD, PVOID, LONG, LONG, DWORD, DWORD)

            @CALLBACK
            def proc(_hook, event, hwnd, object_id, child_id, _event_thread, _event_time):
                if self._stop.is_set():
                    return
                try:
                    self._handle_event(int(event), int(hwnd or 0), int(object_id), int(child_id))
                except Exception as exc:
                    # A malformed/unavailable accessibility object must never
                    # kill the hook callback. Preserve the latest diagnostic
                    # and continue receiving subsequent Firefox events.
                    self._last_callback_error = f"{type(exc).__name__}: {exc}"

            self._proc = proc  # keep callback alive for the duration of the hook
            hook = user32.SetWinEventHook(
                DWORD(EVENT_OBJECT_CREATE),
                DWORD(EVENT_OBJECT_VALUECHANGE),
                None,
                ctypes.cast(proc, PVOID),
                0,
                0,
                DWORD(WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS),
            )
            self._hook = int(hook or 0)
            if not self._hook:
                raise OSError("SetWinEventHook returned NULL")

            # GetMessageW creates/uses this thread's message queue. Mark the
            # hook ready only after installation succeeded; callers can then
            # trust active=True instead of racing a half-started thread.
            self._ready.set()
            msg = _MSG()
            try:
                while not self._stop.is_set():
                    result = int(user32.GetMessageW(ctypes.byref(msg), None, 0, 0))
                    if result == -1:
                        raise OSError("GetMessageW failed")
                    if result == 0:
                        break
                    if msg.message == WM_MSAA_COMMAND:
                        self._process_commands()
                        continue
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                self._process_commands()
                try:
                    user32.UnhookWinEvent(PVOID(self._hook))
                except Exception:
                    pass
                with self._lock:
                    for hwnd in list(self._downloads):
                        self._release_downloads_locked(hwnd)
                    for hwnd in list(set(self._active_composers) | set(self._documents)):
                        self._release_retained_locked(hwnd)
                self._hook = 0
                self._thread_id = 0
                self._proc = None

def debug_accessible_names_msaa(hwnd: int, limit: int = 120) -> list[str]:
    """Return a concise diagnostic list from the best MSAA tree for an HWND."""
    snapshot = inspect_window_msaa(hwnd)
    if snapshot is None:
        return []
    names = [element.name for element in snapshot.elements if element.name]
    composer = [name for name in names if name.lower() in {"stop answering", "send prompt", "start voice"}]
    downloads = [name for name in names if name.lower().startswith("download")]
    tail = names[-max(0, int(limit)) :]
    ordered: list[str] = []
    for name in composer + downloads + tail:
        if name not in ordered:
            ordered.append(name)
    return ordered[:limit]

