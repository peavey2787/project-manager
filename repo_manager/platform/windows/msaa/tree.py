from __future__ import annotations
import ctypes
import hashlib
import re
from typing import Iterable
from .bindings import *
from .browser_url import make_document_key, normalize_document_url
from .constants import *
from .models import MsaaElement, MsaaTreeSnapshot, _ActionTarget
from .response_structure import analyze_latest_response

def _acc_parent(acc_ptr: int) -> int:
    """Return an AddRef'd IAccessible parent pointer, or 0."""
    dispatch = PVOID()
    try:
        fn = _method(acc_ptr, 7, HRESULT, ctypes.POINTER(PVOID))
        hr = int(fn(PVOID(acc_ptr), ctypes.byref(dispatch)))
        if hr < 0 or not dispatch.value:
            return 0
        parent = _query_iaccessible(int(dispatch.value))
        return parent
    except Exception:
        return 0
    finally:
        if dispatch.value:
            _release(int(dispatch.value))

def _document_identity(acc_ptr: int, child: VARIANT, max_depth: int = 48) -> tuple[str, str, str]:
    """Find the containing document for an accessibility event/element.

    Firefox's MSAA event HWND is not guaranteed to uniquely identify a tab. The
    document's accessible URL/title is, so response state is bound to this identity
    instead of to whichever Firefox window happens to be foreground.
    """
    if not acc_ptr:
        return "", "", ""
    # Firefox exposes exactly the relation shown by its Accessibility
    # Inspector ("containing document") through IAccessible2. Prefer that
    # explicit relation over accParent, whose hierarchy may cross proxy/chrome
    # accessibles and is not guaranteed to identify the tab document.
    related = _ia2_containing_document(acc_ptr, child)
    if related[2]:
        return related
    current = int(acc_ptr)
    _add_ref(current)
    current_child = child
    fallback_name = ""
    fallback_value = ""
    try:
        for _ in range(max_depth):
            name = _acc_name(current, current_child).strip()
            value = _acc_value(current, current_child).strip()
            role = _acc_role(current, current_child)
            if name:
                # Keep the highest ancestor name as a title fallback. Starting
                # with the button name would make every event look like its own
                # document when Firefox omits ROLE_SYSTEM_DOCUMENT.
                fallback_name = name
            if normalize_document_url(value) and not fallback_value:
                fallback_value = value
            if role == ROLE_SYSTEM_DOCUMENT:
                key = make_document_key(name, value)
                if key:
                    return name, value, key

            # A simple child id belongs to the current accessible object. Move to
            # that object's SELF before walking to its parent.
            if current_child.vt == VT_I4 and int(current_child.value.lVal) != CHILDID_SELF:
                current_child = _self_variant()
                continue

            parent = _acc_parent(current)
            if not parent:
                break
            _release(current)
            current = parent
            current_child = _self_variant()

        key = make_document_key(fallback_name, fallback_value)
        return fallback_name, fallback_value, key
    finally:
        _release(current)

def _acc_default_action(acc_ptr: int, child: VARIANT) -> str:
    try:
        return _bstr_property(acc_ptr, child, 20)
    except Exception:
        return ""

def _acc_role(acc_ptr: int, child: VARIANT) -> int:
    _, oleaut32, _, _ = _dlls()
    result = VARIANT()
    oleaut32.VariantInit(ctypes.byref(result))
    try:
        fn = _method(acc_ptr, 13, HRESULT, VARIANT, ctypes.POINTER(VARIANT))
        hr = int(fn(PVOID(acc_ptr), child, ctypes.byref(result)))
        if hr >= 0 and result.vt == VT_I4:
            return int(result.value.lVal)
        return 0
    except Exception:
        return 0
    finally:
        try:
            oleaut32.VariantClear(ctypes.byref(result))
        except Exception:
            pass

def _acc_location(acc_ptr: int, child: VARIANT) -> tuple[int, int, int, int] | None:
    """Return the accessible screen rectangle as (left, top, width, height)."""
    left = LONG()
    top = LONG()
    width = LONG()
    height = LONG()
    try:
        fn = _method(
            acc_ptr,
            22,
            HRESULT,
            ctypes.POINTER(LONG),
            ctypes.POINTER(LONG),
            ctypes.POINTER(LONG),
            ctypes.POINTER(LONG),
            VARIANT,
        )
        hr = int(
            fn(
                PVOID(acc_ptr),
                ctypes.byref(left),
                ctypes.byref(top),
                ctypes.byref(width),
                ctypes.byref(height),
                child,
            )
        )
        if hr < 0 or width.value <= 0 or height.value <= 0:
            return None
        return int(left.value), int(top.value), int(width.value), int(height.value)
    except Exception:
        return None

def _acc_hit_test(acc_ptr: int, x: int, y: int) -> tuple[int, VARIANT]:
    """Hit-test one exact IAccessible object without moving the mouse.

    Firefox can leave a replaced web accessible cached until accessibility
    hit-testing reaches that part of the page. Calling accHitTest on the
    retained *document* forces the same refresh without synthesizing input and
    without allowing a different tab to satisfy the probe.
    """
    _, oleaut32, _, _ = _dlls()
    result = VARIANT()
    oleaut32.VariantInit(ctypes.byref(result))
    try:
        fn = _method(acc_ptr, 24, HRESULT, LONG, LONG, ctypes.POINTER(VARIANT))
        hr = int(fn(PVOID(acc_ptr), LONG(int(x)), LONG(int(y)), ctypes.byref(result)))
        if hr < 0:
            return 0, VARIANT()

        if result.vt == VT_DISPATCH and result.value.pdispVal:
            target = _query_iaccessible(int(result.value.pdispVal))
            if not target:
                return 0, VARIANT()
            return target, _self_variant()

        if result.vt == VT_I4:
            child_id = int(result.value.lVal)
            _add_ref(acc_ptr)
            return int(acc_ptr), _child_variant(child_id)

        return 0, VARIANT()
    except Exception:
        return 0, VARIANT()
    finally:
        _clear_variant(result)

def _hit_test_composer_in_document(
    document: int, point: tuple[int, int] | None
) -> tuple[str, int]:
    """Read the live composer at *point* inside one retained chat document.

    ``accHitTest`` is allowed to return an intermediate container rather than
    the deepest child. Descend through hit-testable children a few levels so a
    generic ChatGPT wrapper cannot hide the actual composer button.
    """
    if not document or not point:
        return "", 0
    current = 0
    try:
        _add_ref(document)
        current = int(document)
        for _ in range(12):
            target = 0
            child = VARIANT()
            try:
                target, child = _acc_hit_test(current, int(point[0]), int(point[1]))
                if not target:
                    return "", 0
                name = _acc_name(target, child).strip()
                if name.casefold() in {"stop answering", "send prompt", "start voice"}:
                    return name, _acc_role(target, child)

                concrete = _materialize_accessible_child(target, child)
                if not concrete:
                    return "", 0
                if concrete == current:
                    _release(concrete)
                    return "", 0
                _release(current)
                current = concrete
            finally:
                if target:
                    _clear_variant(child)
                    _release(target)
        return "", 0
    finally:
        if current:
            _release(current)

def _child_count(acc_ptr: int) -> int:
    count = LONG()
    fn = _method(acc_ptr, 8, HRESULT, ctypes.POINTER(LONG))
    hr = int(fn(PVOID(acc_ptr), ctypes.byref(count)))
    if hr < 0:
        return 0
    return max(0, min(int(count.value), 20000))

def _accessible_from_window(hwnd: int, object_id: int) -> int:
    oleacc, _, _, _ = _dlls()
    out = PVOID()
    hr = int(
        oleacc.AccessibleObjectFromWindow(
            PVOID(int(hwnd)),
            DWORD(object_id & 0xFFFFFFFF),
            ctypes.byref(IID_IACCESSIBLE),
            ctypes.byref(out),
        )
    )
    if hr < 0 or not out.value:
        return 0
    return int(out.value)

def _candidate_hwnds(hwnd: int, limit: int = 48) -> list[int]:
    _, _, _, user32 = _dlls()
    values = [int(hwnd)]
    seen = {int(hwnd)}
    CALLBACK = WINFUNCTYPE(ctypes.c_int, PVOID, PVOID)

    @CALLBACK
    def callback(child, _lparam):
        value = int(child or 0)
        if value and value not in seen:
            seen.add(value)
            values.append(value)
        return 0 if len(values) >= limit else 1

    try:
        user32.EnumChildWindows(PVOID(int(hwnd)), callback, None)
    except Exception:
        pass
    return values

def _walk_tree(
    root_ptr: int,
    max_nodes: int = 16000,
    *,
    capture_actions: bool = False,
    capture_all_actions: bool = False,
    include_unnamed: bool = False,
) -> tuple[list[MsaaElement], list[_ActionTarget]]:
    """Walk the accessibility tree in child/document order.

    Earlier builds used breadth-first traversal.  That made a Download button's
    numeric index unrelated to the page's DOM/accessibility order and forced the
    download picker to guess with node-gap thresholds.  A depth-first pre-order
    walk preserves Firefox's accessible child order, so the last Download after
    the last ``ChatGPT said:`` marker is genuinely the newest response link.

    ``visit`` owns one IAccessible reference and always releases it before
    returning. Action targets AddRef their backing accessible separately.
    """
    oleacc, oleaut32, _, _ = _dlls()
    elements: list[MsaaElement] = []
    actions: list[_ActionTarget] = []
    visited: set[int] = set()
    index = 0

    def append_named(acc_ptr: int, child_id: int, name: str) -> bool:
        nonlocal index
        if (not name and not include_unnamed) or index >= max_nodes:
            return index < max_nodes
        child_var = _self_variant() if child_id == CHILDID_SELF else _child_variant(child_id)
        lower_name = name.strip().casefold()
        named_action = (
            lower_name in {
                "stop answering", "send prompt", "start voice", "response actions",
                "copy response", "ask chatgpt",
            }
            or lower_name.startswith("download")
            or lower_name.startswith("worked for ")
        )
        capture_item = capture_actions and (capture_all_actions or named_action)
        role = _acc_role(acc_ptr, child_var) if capture_item else 0
        default_action = _acc_default_action(acc_ptr, child_var) if capture_item else ""
        elements.append(MsaaElement(index=index, name=name, role=role, default_action=default_action))
        if capture_item:
            _add_ref(acc_ptr)
            actions.append(
                _ActionTarget(
                    index=index,
                    name=name,
                    role=role,
                    acc_ptr=acc_ptr,
                    child_id=child_id,
                )
            )
        index += 1
        return index < max_nodes

    def visit(acc_ptr: int, depth: int) -> None:
        nonlocal index
        if not acc_ptr:
            return
        if acc_ptr in visited:
            _release(acc_ptr)
            return
        visited.add(acc_ptr)
        try:
            if index >= max_nodes:
                return
            name = _acc_name(acc_ptr, _self_variant())
            if (name or include_unnamed) and not append_named(acc_ptr, CHILDID_SELF, name):
                return
            if depth >= 96 or index >= max_nodes:
                return

            count = _child_count(acc_ptr)
            if count <= 0:
                return
            children = (VARIANT * count)()
            for i in range(count):
                oleaut32.VariantInit(ctypes.byref(children[i]))
            obtained = LONG()
            try:
                hr = int(oleacc.AccessibleChildren(PVOID(acc_ptr), 0, count, children, ctypes.byref(obtained)))
                if hr < 0:
                    return
                child_total = max(0, min(int(obtained.value), count))
                for i in range(child_total):
                    if index >= max_nodes:
                        break
                    var = children[i]
                    if var.vt == VT_DISPATCH and var.value.pdispVal:
                        disp = int(var.value.pdispVal)
                        child_acc = _query_iaccessible(disp)
                        # AccessibleChildren owns an IDispatch ref; QueryInterface
                        # creates the IAccessible ref consumed by visit().
                        _release(disp)
                        var.vt = VT_EMPTY
                        var.value.pdispVal = None
                        if child_acc:
                            visit(child_acc, depth + 1)
                    elif var.vt == VT_I4:
                        child_id = int(var.value.lVal)
                        child_var = _child_variant(child_id)
                        child_name = _acc_name(acc_ptr, child_var)
                        if child_name or include_unnamed:
                            append_named(acc_ptr, child_id, child_name)
            finally:
                for i in range(max(0, min(int(obtained.value), count))):
                    try:
                        oleaut32.VariantClear(ctypes.byref(children[i]))
                    except Exception:
                        pass
        finally:
            _release(acc_ptr)

    visit(root_ptr, 0)
    return elements, actions

def _release_actions(actions: Iterable[_ActionTarget]) -> None:
    for target in actions:
        _release(target.acc_ptr)

def _classify_elements(elements: Iterable[MsaaElement]) -> tuple[str, str, str, int]:
    composer_state = "unknown"
    composer_name = ""
    error_text = ""
    download_count = 0
    for element in elements:
        name = element.name.strip()
        lower = name.lower()
        # Exact Firefox accessible names supplied by the ChatGPT composer.
        # Start Voice is intentionally not a composer state.
        if lower == "stop answering":
            composer_state = "stop"
            composer_name = name
        elif composer_state != "stop" and lower == "send prompt":
            composer_state = "send"
            composer_name = name
        elif composer_state == "unknown" and lower == "start voice":
            composer_state = "voice"
            composer_name = name
        if not error_text and re.search(
            r"(message delivery timed out\.?\s*please try again\.?|there was an error|something went wrong|network error|error generating|failed to generate|try again later|unable to load|rate limit|you.ve reached)",
            lower,
        ):
            error_text = name
        if lower.startswith("download"):
            download_count += 1
    return composer_state, composer_name, error_text, download_count

def _tree_snapshot(
    source_hwnd: int,
    object_id: int,
    elements: list[MsaaElement],
    actions: Iterable[_ActionTarget] = (),
) -> MsaaTreeSnapshot:
    composer_state, composer_name, error_text, download_count = _classify_elements(elements)
    document_name = ""
    document_value = ""
    document_key = ""
    for target in actions:
        if target.name.strip().casefold() not in {"stop answering", "send prompt", "start voice"}:
            continue
        child = _child_variant(target.child_id)
        document_name, document_value, document_key = _document_identity(target.acc_ptr, child)
        if document_key:
            break
    structure = analyze_latest_response(elements)
    # A complete newest-response structure outranks a stale Stop-answering
    # accessible that Firefox can leave elsewhere in the window tree. The
    # structure is scoped after the newest ChatGPT-said marker, so an older
    # finished response cannot mask a genuinely running newer response.
    if structure.completed_successfully:
        composer_state = "voice"
        composer_name = "Start Voice"
    tail_names = [element.name for element in elements[-320:] if element.name]
    joined = "\n".join(tail_names)
    fingerprint = hashlib.sha256(joined.encode("utf-8", errors="replace")).hexdigest()
    return MsaaTreeSnapshot(
        source_hwnd=source_hwnd,
        object_id=object_id,
        available=True,
        composer_state=composer_state,
        composer_name=composer_name,
        error_text=error_text,
        response_fingerprint=fingerprint,
        response_text_length=len(joined),
        download_count=download_count,
        document_name=document_name,
        document_value=document_value,
        document_key=document_key,
        elements=tuple(elements),
        worked_for_text=structure.worked_for_text,
        latest_response_complete=structure.completed_successfully,
    )

def probe_event_object_msaa(
    top_hwnd: int,
    source_hwnd: int,
    object_id: int,
    child_id: int,
) -> MsaaWinEvent | None:
    """Actively re-read one exact previously reported Firefox accessible.

    Unlike WinEvent monitoring, this does not wait for Firefox to emit a new
    notification.  It is the authoritative completion probe for an in-flight
    response: the same composer object that said ``Stop answering`` can be read
    again until it says ``Send prompt`` (or, on replacement, the document probe
    below finds ``Start Voice``).
    """
    if os.name != "nt" or not source_hwnd:
        return None
    with _com_scope():
        acc_ptr, child = _accessible_from_event_target(source_hwnd, object_id, child_id)
        if not acc_ptr:
            return None
        try:
            name = _acc_name(acc_ptr, child).strip()
            if not name:
                return None
            document_name, document_value, document_key = _document_identity(acc_ptr, child)
            return MsaaWinEvent(
                top_hwnd=int(top_hwnd or 0),
                event_id=0,
                source_hwnd=int(source_hwnd),
                object_id=int(object_id),
                child_id=int(child_id),
                name=name,
                role=_acc_role(acc_ptr, child),
                default_action=_acc_default_action(acc_ptr, child),
                document_name=document_name,
                document_value=document_value,
                document_key=document_key,
            )
        finally:
            _clear_variant(child)
            _release(acc_ptr)

def invoke_event_object_msaa(
    source_hwnd: int,
    object_id: int,
    child_id: int,
    *,
    required_name_prefix: str = "",
) -> str:
    """Invoke the exact accessible object identified by a WinEvent tuple.

    The name is re-read immediately before invocation.  This is used by the
    keyboard-tab download path: tab order identifies the last live Download
    control, then this function presses that exact control rather than doing a
    second tree search which could land on an older response.
    """
    if os.name != "nt" or not source_hwnd:
        return ""
    prefix = str(required_name_prefix or "").strip().casefold()
    with _com_scope():
        acc_ptr, child = _accessible_from_event_target(source_hwnd, object_id, child_id)
        if not acc_ptr:
            return ""
        try:
            name = _acc_name(acc_ptr, child).strip()
            if not name:
                return ""
            if prefix and not name.casefold().startswith(prefix):
                return ""
            target = _ActionTarget(
                index=0,
                name=name,
                role=_acc_role(acc_ptr, child),
                acc_ptr=acc_ptr,
                child_id=int(child.value.lVal) if child.vt == VT_I4 else CHILDID_SELF,
            )
            # _invoke_target does not own/release acc_ptr.
            return name if _invoke_target(target) else ""
        finally:
            _clear_variant(child)
            _release(acc_ptr)

def probe_composer_msaa(
    hwnd: int,
    expected_document_key: str = "",
    preferred_source_hwnd: int = 0,
) -> MsaaTreeSnapshot | None:
    """Read the composer from the exact known chat document when possible.

    This differs from :func:`inspect_window_msaa`, which is allowed to find the
    active browser document for initial discovery.  A running response must not
    be completed by another tab, so this probe only returns an expected-document
    match when one is supplied.  The source HWND that produced ``Stop answering``
    is scanned first to handle Firefox content windows which are not ordinary
    Win32 children of the top-level browser frame.
    """
    if os.name != "nt" or not hwnd:
        return None
    expected_document_key = str(expected_document_key or "")
    candidates: list[int] = []
    if preferred_source_hwnd:
        candidates.append(int(preferred_source_hwnd))
    for value in _candidate_hwnds(hwnd, limit=96):
        if value not in candidates:
            candidates.append(value)

    best: MsaaTreeSnapshot | None = None
    with _com_scope():
        for candidate in candidates:
            for object_id in (OBJID_CLIENT, OBJID_WINDOW):
                root = _accessible_from_window(candidate, object_id)
                if not root:
                    continue
                actions: list[_ActionTarget] = []
                try:
                    elements, actions = _walk_tree(root, max_nodes=32000, capture_actions=True)
                    snapshot = _tree_snapshot(candidate, object_id, elements, actions)
                finally:
                    _release_actions(actions)
                if snapshot.composer_state not in {"stop", "send", "voice"}:
                    continue
                if expected_document_key:
                    if snapshot.document_key == expected_document_key:
                        return snapshot
                    continue
                if candidate == int(preferred_source_hwnd or 0):
                    return snapshot
                if best is None:
                    best = snapshot
    return best if not expected_document_key else None

def inspect_window_msaa(hwnd: int) -> MsaaTreeSnapshot | None:
    if os.name != "nt" or not hwnd:
        return None
    best: MsaaTreeSnapshot | None = None
    with _com_scope():
        for candidate in _candidate_hwnds(hwnd):
            for object_id in (OBJID_CLIENT, OBJID_WINDOW):
                root = _accessible_from_window(candidate, object_id)
                if not root:
                    continue
                actions: list[_ActionTarget] = []
                try:
                    elements, actions = _walk_tree(root, capture_actions=True)
                    snapshot = _tree_snapshot(candidate, object_id, elements, actions)
                finally:
                    _release_actions(actions)
                # Exact composer name is decisive; return immediately.
                if snapshot.composer_state in {"stop", "send", "voice"}:
                    return snapshot
                if best is None or len(snapshot.elements) > len(best.elements):
                    best = snapshot
    return best

def inspect_windows_msaa(hwnds: Iterable[int]) -> dict[int, MsaaTreeSnapshot]:
    result: dict[int, MsaaTreeSnapshot] = {}
    for hwnd in sorted({int(value) for value in hwnds if int(value) > 0}):
        try:
            snapshot = inspect_window_msaa(hwnd)
        except Exception:
            snapshot = None
        if snapshot is not None:
            result[hwnd] = snapshot
    return result


__all__=[name for name in globals() if not name.startswith("__")]
