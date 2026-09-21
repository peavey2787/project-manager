from __future__ import annotations

import os

from .bindings import HRESULT, PVOID, VARIANT, _child_variant, _method
from .constants import (
    ROLE_SYSTEM_COMBOBOX,
    ROLE_SYSTEM_DOCUMENT,
    ROLE_SYSTEM_PUSHBUTTON,
    ROLE_SYSTEM_STATICTEXT,
    ROLE_SYSTEM_TEXT,
)
from .models import MsaaTreeSnapshot, _ActionTarget
from .tree import (
    _accessible_from_window,
    _candidate_hwnds,
    _com_scope,
    _release_actions,
    _tree_snapshot,
    _walk_tree,
)


_CHATGPT_COMPOSER_NAMES = ("Ask ChatGPT", "Chat with ChatGPT")


def _chatgpt_composer_name_candidates(name: str) -> tuple[str, ...]:
    requested = str(name or "").strip()
    ordered = (requested, *_CHATGPT_COMPOSER_NAMES)
    seen: set[str] = set()
    result: list[str] = []
    for candidate in ordered:
        folded = candidate.casefold()
        if not candidate or folded in seen:
            continue
        seen.add(folded)
        result.append(candidate)
    return tuple(result)

_ROLE_NAMES = {
    ROLE_SYSTEM_DOCUMENT: "document",
    ROLE_SYSTEM_TEXT: "text",
    ROLE_SYSTEM_PUSHBUTTON: "push button",
    ROLE_SYSTEM_STATICTEXT: "static text",
    ROLE_SYSTEM_COMBOBOX: "combo box",
}


def msaa_role_name(role: int) -> str:
    return _ROLE_NAMES.get(int(role or 0), f"role 0x{int(role or 0):02X}")


def inspect_accessibility_items_msaa(hwnd: int, max_nodes: int = 20000) -> MsaaTreeSnapshot | None:
    """Return the richest accessibility tree reachable from one browser HWND."""
    if os.name != "nt" or not hwnd:
        return None
    best: MsaaTreeSnapshot | None = None
    with _com_scope():
        for candidate in _candidate_hwnds(int(hwnd), limit=96):
            for object_id in (-4, 0):
                root = _accessible_from_window(candidate, object_id)
                if not root:
                    continue
                actions: list[_ActionTarget] = []
                try:
                    elements, actions = _walk_tree(
                        root,
                        max_nodes=max_nodes,
                        capture_actions=True,
                        capture_all_actions=True,
                        include_unnamed=True,
                    )
                    snapshot = _tree_snapshot(candidate, object_id, elements, actions)
                finally:
                    _release_actions(actions)
                if snapshot.composer_state in {"stop", "send", "voice"}:
                    return snapshot
                if best is None or len(snapshot.elements) > len(best.elements):
                    best = snapshot
    return best


def _invoke_target(target: _ActionTarget) -> bool:
    child = _child_variant(target.child_id)
    try:
        fn = _method(target.acc_ptr, 25, HRESULT, VARIANT)
        return int(fn(PVOID(target.acc_ptr), child)) >= 0
    except Exception:
        return False


def invoke_accessibility_item_msaa(
    source_hwnd: int,
    object_id: int,
    index: int,
    expected_name: str = "",
    max_nodes: int = 20000,
) -> str:
    """Re-resolve and invoke one explorer item by stable tree index/name."""
    if os.name != "nt" or not source_hwnd:
        return ""
    with _com_scope():
        root = _accessible_from_window(int(source_hwnd), int(object_id))
        if not root:
            return ""
        actions: list[_ActionTarget] = []
        try:
            _elements, actions = _walk_tree(
                root,
                max_nodes=max_nodes,
                capture_actions=True,
                capture_all_actions=True,
                include_unnamed=True,
            )
            target = next((item for item in actions if item.index == int(index)), None)
            if target is None:
                return ""
            current_name = str(target.name or "")
            expected = str(expected_name or "")
            if expected and current_name != expected:
                return ""
            return (current_name or "(unnamed)") if _invoke_target(target) else ""
        finally:
            _release_actions(actions)


def invoke_named_accessibility_item_msaa(
    hwnd: int,
    name: str,
    *,
    role: int = 0,
    max_nodes: int = 30000,
) -> str:
    """Invoke an exact named item from the active ChatGPT accessibility tree.

    The tree must also expose a recognized ChatGPT composer so a same-named
    browser-chrome accessible cannot be selected accidentally.
    """
    if os.name != "nt" or not hwnd or not str(name or "").strip():
        return ""
    expected_name = str(name).strip().casefold()
    expected_role = int(role or 0)
    with _com_scope():
        for candidate in _candidate_hwnds(int(hwnd), limit=96):
            for object_id in (-4, 0):
                root = _accessible_from_window(candidate, object_id)
                if not root:
                    continue
                actions: list[_ActionTarget] = []
                try:
                    elements, actions = _walk_tree(
                        root,
                        max_nodes=max_nodes,
                        capture_actions=True,
                        capture_all_actions=True,
                        include_unnamed=False,
                    )
                    snapshot = _tree_snapshot(candidate, object_id, elements, actions)
                    if snapshot.composer_state not in {"stop", "send", "voice"}:
                        continue
                    matches = [
                        item for item in actions
                        if item.name.strip().casefold() == expected_name
                        and (not expected_role or int(item.role) == expected_role)
                    ]
                    for target in reversed(matches):
                        if _invoke_target(target):
                            return target.name
                finally:
                    _release_actions(actions)
    return ""



def invoke_chatgpt_retry_msaa(hwnd: int, max_nodes: int = 30000) -> str:
    """Invoke ChatGPT's Retry control for a delivery-timeout error.

    This is intentionally stricter than the generic named-item helper: the
    exact same accessibility tree must contain both the delivery-timeout text
    and an exact ``Retry`` action.  That prevents a same-named browser-chrome
    or unrelated page control from being clicked.
    """
    if os.name != "nt" or not hwnd:
        return ""

    timeout_phrase = "message delivery timed out"
    please_try_again = "please try again"
    with _com_scope():
        for candidate in _candidate_hwnds(int(hwnd), limit=96):
            for object_id in (-4, 0):
                root = _accessible_from_window(candidate, object_id)
                if not root:
                    continue
                actions: list[_ActionTarget] = []
                try:
                    elements, actions = _walk_tree(
                        root,
                        max_nodes=max_nodes,
                        capture_actions=True,
                        capture_all_actions=True,
                        include_unnamed=False,
                    )
                    names = [str(item.name or "").strip().casefold() for item in elements]
                    if not any(timeout_phrase in name and please_try_again in name for name in names):
                        continue

                    elements_by_index = {item.index: item for item in elements}
                    matches = [
                        target for target in actions
                        if str(target.name or "").strip().casefold() == "retry"
                    ]
                    for target in reversed(matches):
                        element = elements_by_index.get(target.index)
                        if element is None:
                            continue
                        action = "".join(ch for ch in str(element.default_action or "").casefold() if ch.isalnum())
                        # Firefox currently exposes this text leaf as
                        # Default action: clickAncestor.  Permit an ordinary
                        # press/click too so a harmless accessibility-role
                        # change does not break recovery.
                        if action and action not in {"clickancestor", "click", "press"}:
                            continue
                        if _invoke_target(target):
                            return target.name
                finally:
                    _release_actions(actions)
    return ""

def invoke_chatgpt_composer_msaa(
    hwnd: int,
    name: str = "Ask ChatGPT",
    *,
    role: int = ROLE_SYSTEM_STATICTEXT,
    max_nodes: int = 30000,
) -> str:
    """Invoke the ChatGPT composer using the explorer's rich accessibility tree.

    ChatGPT/Firefox currently exposes the composer text leaf as either
    ``Ask ChatGPT`` or ``Chat with ChatGPT`` depending on page state/version.
    Preserve the caller's requested name as the first choice, then try both
    known aliases.  The rich-tree fallback also tolerates a harmless role
    change while still requiring an exact composer name in the managed window.
    """
    names = _chatgpt_composer_name_candidates(name)
    for candidate_name in names:
        clicked = invoke_named_accessibility_item_msaa(
            hwnd, candidate_name, role=role, max_nodes=max_nodes
        )
        if clicked:
            return clicked

    snapshot = inspect_accessibility_items_msaa(hwnd, max_nodes=max_nodes)
    if snapshot is None:
        return ""
    expected_role = int(role or 0)
    for candidate_name in names:
        expected_name = candidate_name.casefold()
        name_matches = [
            element
            for element in snapshot.elements
            if element.name.strip().casefold() == expected_name
        ]
        role_matches = [
            element for element in name_matches
            if not expected_role or int(element.role) == expected_role
        ]
        for element in reversed(role_matches or name_matches):
            clicked = invoke_accessibility_item_msaa(
                snapshot.source_hwnd,
                snapshot.object_id,
                element.index,
                expected_name=element.name,
                max_nodes=max_nodes,
            )
            if clicked:
                return clicked
    return ""


__all__ = [
    "inspect_accessibility_items_msaa",
    "invoke_chatgpt_retry_msaa",
    "invoke_chatgpt_composer_msaa",
    "invoke_accessibility_item_msaa",
    "invoke_named_accessibility_item_msaa",
    "msaa_role_name",
]
