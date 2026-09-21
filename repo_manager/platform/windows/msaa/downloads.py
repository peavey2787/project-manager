from __future__ import annotations
import re
from typing import Iterable
from .bindings import *
from .models import _ActionTarget
from .response_structure import analyze_latest_response
from .tree import *

def _normalized_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())

def _matches_terms(name: str, terms: Iterable[str]) -> bool:
    normalized = _normalized_token(name)
    tokens = [_normalized_token(term) for term in terms]
    tokens = [token for token in tokens if len(token) >= 3]
    return any(token in normalized for token in tokens)

def _invoke_target(target: _ActionTarget) -> bool:
    child = _child_variant(target.child_id)
    try:
        fn = _method(target.acc_ptr, 25, HRESULT, VARIANT)
        return int(fn(PVOID(target.acc_ptr), child)) >= 0
    except Exception:
        return False

def _latest_download_group(
    elements: list[MsaaElement],
    downloads: list[_ActionTarget],
) -> list[_ActionTarget]:
    """Return Download controls belonging to the newest assistant response only.

    ChatGPT's Firefox accessibility tree exposes a stable response shape:
    ``ChatGPT said:`` -> response content / Download -> ``Response actions``.
    When that newest response marker exists, a Download outside that exact
    interval is historical and must never be invoked as a fallback.

    Older Firefox builds which expose no assistant marker still get the safest
    deterministic behavior available: only the final Download control.
    """
    if not downloads:
        return []
    ordered = sorted(downloads, key=lambda item: item.index)
    structure = analyze_latest_response(elements)
    if structure.marker_index >= 0:
        end = structure.response_actions_index
        return [
            target
            for target in ordered
            if target.index > structure.marker_index
            and (end < 0 or target.index < end)
        ]
    return [ordered[-1]]


def _window_tree_download_fallback(
    hwnd: int,
    expected_document_key: str = "",
) -> list[str]:
    """Invoke the newest Download control from the managed window tree.

    Firefox does not always place response controls beneath the same containing
    document accessible returned by the composer relation. The diagnostic
    Accessibility Items window walks the richer managed-window tree and can see
    those controls. This fallback mirrors that view while still enforcing the
    newest ``ChatGPT said:`` -> ``Response actions`` boundary.
    """
    if os.name != "nt" or not hwnd:
        return []

    expected_document_key = str(expected_document_key or "")
    best_actions: list[_ActionTarget] = []
    best_score: tuple[int, int, int, int] = (-1, -1, -1, -1)

    with _com_scope():
        for candidate in _candidate_hwnds(int(hwnd), limit=96):
            for object_id in (OBJID_CLIENT, OBJID_WINDOW):
                root = _accessible_from_window(candidate, object_id)
                if not root:
                    continue
                actions: list[_ActionTarget] = []
                try:
                    elements, actions = _walk_tree(
                        root,
                        max_nodes=100000,
                        capture_actions=True,
                        capture_all_actions=True,
                        include_unnamed=True,
                    )
                    snapshot = _tree_snapshot(candidate, object_id, elements, actions)
                    if snapshot.composer_state not in {"stop", "send", "voice"}:
                        continue
                    if (
                        expected_document_key
                        and snapshot.document_key
                        and snapshot.document_key != expected_document_key
                    ):
                        continue

                    downloads = [
                        target
                        for target in actions
                        if target.name.strip().casefold().startswith("download")
                        and (not target.role or int(target.role) == ROLE_SYSTEM_PUSHBUTTON)
                    ]
                    selected = _latest_download_group(elements, downloads)
                    if not selected:
                        continue

                    verified: list[_ActionTarget] = []
                    for target in selected:
                        child = _child_variant(target.child_id)
                        current_name = _acc_name(target.acc_ptr, child).strip()
                        current_role = _acc_role(target.acc_ptr, child)
                        if not current_name.casefold().startswith("download"):
                            continue
                        if current_role and int(current_role) != ROLE_SYSTEM_PUSHBUTTON:
                            continue
                        target.name = current_name
                        target.role = current_role
                        verified.append(target)
                    if not verified:
                        continue

                    structure = analyze_latest_response(elements)
                    score = (
                        1 if snapshot.document_key and snapshot.document_key == expected_document_key else 0,
                        structure.marker_index,
                        max(target.index for target in verified),
                        len(elements),
                    )
                    if score > best_score:
                        keep_ids = {id(target) for target in verified}
                        _release_actions(target for target in actions if id(target) not in keep_ids)
                        actions = []
                        _release_actions(best_actions)
                        best_actions = verified
                        best_score = score
                finally:
                    if actions:
                        _release_actions(actions)

        clicked: list[str] = []
        try:
            for target in sorted(best_actions, key=lambda item: item.index):
                child = _child_variant(target.child_id)
                current_name = _acc_name(target.acc_ptr, child).strip()
                if not current_name.casefold().startswith("download"):
                    continue
                current_role = _acc_role(target.acc_ptr, child)
                if current_role and int(current_role) != ROLE_SYSTEM_PUSHBUTTON:
                    continue
                target.name = current_name
                target.role = current_role
                if _invoke_target(target):
                    clicked.append(current_name)
        finally:
            _release_actions(best_actions)
        return clicked

def download_latest_msaa(
    hwnd: int,
    match_terms: Iterable[str] = (),
    expected_document_key: str = "",
    *,
    anchor_source_hwnd: int = 0,
    anchor_object_id: int = 0,
    anchor_child_id: int = 0,
) -> list[str]:
    """Invoke Download buttons from the newest response in one exact chat document.

    The strongest anchor is the composer object which actually produced the
    watched response.  We actively resolve that exact ``(HWND, object, child)``
    tuple and follow its containing-document relation/parent chain.  This keeps
    downloads tied to the same ChatGPT tab even if another Firefox tab is now
    selected.

    If no response anchor exists (for example immediately after opening an
    existing saved chat), the current composer is discovered under the managed
    Firefox HWND and its containing document is used.  Selection inside the
    document is deterministic accessibility order: the final Download control,
    plus any sibling Download controls in that same assistant response when a
    ``ChatGPT said:`` boundary is exposed.
    """
    if os.name != "nt" or not hwnd:
        return []
    terms = [str(value).strip() for value in match_terms if str(value).strip()]
    expected_document_key = str(expected_document_key or "")

    def scan_document(doc_acc: int, *, anchored: bool) -> tuple[list[_ActionTarget], tuple[int, int, int]]:
        """Consume *doc_acc* and return retained newest-response targets + score."""
        self_child = _self_variant()
        doc_name = _acc_name(doc_acc, self_child).strip()
        doc_value = _acc_value(doc_acc, self_child).strip()
        doc_key = make_document_key(doc_name, doc_value)

        # An exact response object is stronger evidence than title-vs-URL
        # normalization differences.  Discovery paths, however, must honor the
        # expected document when one is known.
        if expected_document_key and not anchored and doc_key != expected_document_key:
            _release(doc_acc)
            return [], (-1, -1, -1)

        actions: list[_ActionTarget] = []
        try:
            # A user-triggered download is infrequent. Walk a much larger slice
            # than the background monitor so long chats cannot silently stop at
            # the previous response.
            elements, actions = _walk_tree(doc_acc, max_nodes=100000, capture_actions=True)
            downloads = [
                target
                for target in actions
                if target.name.strip().casefold().startswith("download")
            ]
            if not downloads:
                return [], (-1, -1, -1)
            for target in downloads:
                target.document_key = doc_key

            selected_group = _latest_download_group(elements, downloads)
            if not selected_group:
                return [], (-1, -1, -1)

            # Verify the retained objects still expose the same Download name at
            # selection time.  Accessibility trees can update while we walk; a
            # stale child id must never be allowed to invoke an older artifact.
            verified: list[_ActionTarget] = []
            for target in selected_group:
                child = _child_variant(target.child_id)
                current_name = _acc_name(target.acc_ptr, child).strip()
                if current_name.casefold().startswith("download"):
                    target.name = current_name
                    verified.append(target)
            if not verified:
                return [], (-1, -1, -1)

            # Keep only the selected targets; release every other retained
            # action from the full document walk.
            keep_ids = {id(target) for target in verified}
            _release_actions(target for target in actions if id(target) not in keep_ids)
            actions = []
            # Anchored document always outranks discovered documents. Within the
            # same document, later accessibility index outranks group size.
            score = (1 if anchored else 0, max(target.index for target in verified), len(verified))
            return verified, score
        finally:
            if actions:
                _release_actions(actions)

    best_actions: list[_ActionTarget] = []
    best_score: tuple[int, int, int] = (-1, -1, -1)
    seen_documents: set[str] = set()

    with _com_scope():
        # 1) Exact response/composer object from the watcher.  This is the
        # preferred path after the app has seen this chat run at least once.
        if anchor_source_hwnd:
            acc_ptr, child = _accessible_from_event_target(
                int(anchor_source_hwnd), int(anchor_object_id), int(anchor_child_id)
            )
            if acc_ptr:
                try:
                    doc_acc = _containing_document_accessible(acc_ptr, child)
                finally:
                    _clear_variant(child)
                    _release(acc_ptr)
                if doc_acc:
                    # Record key only to avoid rescanning the same document via
                    # discovery below; pointer identity is not stable enough.
                    key = make_document_key(
                        _acc_name(doc_acc, _self_variant()).strip(),
                        _acc_value(doc_acc, _self_variant()).strip(),
                    )
                    if key:
                        seen_documents.add(key)
                    actions, score = scan_document(doc_acc, anchored=True)
                    if score > best_score:
                        _release_actions(best_actions)
                        best_actions, best_score = actions, score
                    else:
                        _release_actions(actions)

        # 2) Existing chat opened before this process saw a response, or anchor
        # object was replaced. Discover composer controls under the exact managed
        # Firefox HWND, resolve each control's actual containing document, and
        # evaluate only matching/current chat documents.
        candidates = _candidate_hwnds(hwnd, limit=96)
        if anchor_source_hwnd and int(anchor_source_hwnd) not in candidates:
            candidates.insert(0, int(anchor_source_hwnd))
        for candidate in candidates:
            for object_id in (OBJID_CLIENT, OBJID_WINDOW):
                root = _accessible_from_window(candidate, object_id)
                if not root:
                    continue
                root_actions: list[_ActionTarget] = []
                try:
                    _root_elements, root_actions = _walk_tree(root, max_nodes=32000, capture_actions=True)
                    composer_targets = [
                        target
                        for target in root_actions
                        if target.name.strip().casefold() in {"stop answering", "send prompt", "start voice"}
                    ]
                    for composer in composer_targets:
                        child = _child_variant(composer.child_id)
                        doc_acc = _containing_document_accessible(composer.acc_ptr, child)
                        if not doc_acc:
                            continue
                        key = make_document_key(
                            _acc_name(doc_acc, _self_variant()).strip(),
                            _acc_value(doc_acc, _self_variant()).strip(),
                        )
                        dedupe = key or f"ptr:{doc_acc}"
                        if dedupe in seen_documents:
                            _release(doc_acc)
                            continue
                        seen_documents.add(dedupe)
                        actions, score = scan_document(doc_acc, anchored=False)
                        if score > best_score:
                            _release_actions(best_actions)
                            best_actions, best_score = actions, score
                        else:
                            _release_actions(actions)
                finally:
                    _release_actions(root_actions)

        if not best_actions:
            return _window_tree_download_fallback(
                int(hwnd),
                expected_document_key=expected_document_key,
            )

        # Match terms are only a preference inside the already selected newest
        # response. They are never allowed to pull an older revision forward.
        preferred = [target for target in best_actions if _matches_terms(target.name, terms)]
        selected = preferred or best_actions

        clicked: list[str] = []
        seen: set[tuple[int, str]] = set()
        try:
            for target in sorted(selected, key=lambda item: item.index):
                child = _child_variant(target.child_id)
                # Re-read immediately before invocation. If Firefox recycled the
                # accessible during the scan, refuse instead of downloading the
                # previous response.
                current_name = _acc_name(target.acc_ptr, child).strip()
                if not current_name.casefold().startswith("download"):
                    continue
                key = (target.index, current_name)
                if key in seen:
                    continue
                seen.add(key)
                target.name = current_name
                if _invoke_target(target):
                    clicked.append(current_name)
        finally:
            _release_actions(best_actions)
        if clicked:
            return clicked

    # Firefox can expose the visible response controls in the richer managed
    # window tree while omitting them from the composer's containing-document
    # relation. Mirror the Accessibility Items view as a deterministic fallback.
    return _window_tree_download_fallback(
        int(hwnd),
        expected_document_key=expected_document_key,
    )


__all__=[name for name in globals() if not name.startswith("__")]
