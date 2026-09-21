from __future__ import annotations
import re
from urllib.parse import urlsplit,urlunsplit
from .bindings import *
from .constants import *

def _coerce_browser_url(value: str) -> str:
    """Return an HTTP(S) URL from an address-bar value, or an empty string.

    Firefox and Chromium-family browsers may expose the address bar without the
    scheme while the field is not being edited.  Keep the path/query intact so
    the caller can match it against saved project URLs.
    """
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        return ""
    lowered = text.casefold()
    if lowered.startswith(("about:", "chrome:", "edge:", "brave:", "file:")):
        return ""
    candidate = text
    if not re.match(r"^[a-z][a-z0-9+.-]*://", candidate, flags=re.I):
        # Address bars commonly expose ``example.com/path``.  Require a host-
        # looking first segment so arbitrary page text is not treated as a URL.
        first = candidate.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if "." not in first and first.casefold() != "localhost":
            return ""
        candidate = "https://" + candidate
    try:
        parsed = urlsplit(candidate)
    except Exception:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return ""
    return candidate

def read_browser_active_url_msaa(hwnd: int) -> str:
    """Read the active tab URL from a browser's address bar using MSAA.

    This intentionally scans only browser chrome.  ROLE_SYSTEM_DOCUMENT
    descendants are not traversed, so a URL typed into a web page cannot be
    confused with the browser's own address bar.  The function is lightweight
    enough to poll the foreground browser and works for Firefox plus the common
    Chromium-family browsers used by Project Repo Manager.
    """
    if os.name != "nt" or not hwnd:
        return ""

    address_hints = (
        "address and search bar",
        "address bar",
        "search with",
        "enter address",
        "location",
        "omnibox",
    )

    def score_candidate(name: str, value: str, role: int, depth: int) -> tuple[int, str]:
        url = _coerce_browser_url(value)
        if not url:
            return (0, "")
        lower_name = str(name or "").strip().casefold()
        score = 40
        if any(hint in lower_name for hint in address_hints):
            score += 100
        if role in {ROLE_SYSTEM_TEXT, ROLE_SYSTEM_COMBOBOX}:
            score += 30
        if depth <= 8:
            score += 20
        if str(value).strip().casefold().startswith(("http://", "https://")):
            score += 10
        return (score, url)

    with _com_scope():
        best_score = 0
        best_url = ""
        # Browser chrome is owned by the top-level window or one of a small
        # number of child HWNDs.  There is no reason to enumerate deep content
        # process windows for the address bar.
        for candidate_hwnd in _candidate_hwnds(int(hwnd), limit=24):
            for object_id in (OBJID_CLIENT, OBJID_WINDOW):
                root = _accessible_from_window(candidate_hwnd, object_id)
                if not root:
                    continue
                visited: set[int] = set()
                node_budget = 1800
                nodes = 0

                def visit(acc_ptr: int, depth: int) -> None:
                    nonlocal best_score, best_url, nodes
                    if not acc_ptr:
                        return
                    if acc_ptr in visited:
                        _release(acc_ptr)
                        return
                    visited.add(acc_ptr)
                    try:
                        if nodes >= node_budget or depth > 18:
                            return
                        nodes += 1
                        self_var = _self_variant()
                        role = _acc_role(acc_ptr, self_var)
                        # Never use or traverse a web document.  The browser's
                        # own address bar is the authority for the active tab;
                        # renderer/document accessibles can include hidden tabs.
                        if role == ROLE_SYSTEM_DOCUMENT:
                            return
                        name = _acc_name(acc_ptr, self_var)
                        value = _acc_value(acc_ptr, self_var)
                        score, url = score_candidate(name, value, role, depth)
                        if score > best_score:
                            best_score, best_url = score, url

                        count = _child_count(acc_ptr)
                        if count <= 0:
                            return
                        oleacc, oleaut32, _, _ = _dlls()
                        children = (VARIANT * count)()
                        for i in range(count):
                            oleaut32.VariantInit(ctypes.byref(children[i]))
                        obtained = LONG()
                        try:
                            hr = int(oleacc.AccessibleChildren(PVOID(acc_ptr), 0, count, children, ctypes.byref(obtained)))
                            if hr < 0:
                                return
                            total = max(0, min(int(obtained.value), count))
                            for i in range(total):
                                if nodes >= node_budget:
                                    break
                                var = children[i]
                                if var.vt == VT_DISPATCH and var.value.pdispVal:
                                    disp = int(var.value.pdispVal)
                                    child_acc = _query_iaccessible(disp)
                                    _release(disp)
                                    var.vt = VT_EMPTY
                                    var.value.pdispVal = None
                                    if child_acc:
                                        visit(child_acc, depth + 1)
                                elif var.vt == VT_I4:
                                    child_id = int(var.value.lVal)
                                    child = _child_variant(child_id)
                                    role = _acc_role(acc_ptr, child)
                                    if role == ROLE_SYSTEM_DOCUMENT:
                                        continue
                                    name = _acc_name(acc_ptr, child)
                                    value = _acc_value(acc_ptr, child)
                                    score, url = score_candidate(name, value, role, depth + 1)
                                    if score > best_score:
                                        best_score, best_url = score, url
                        finally:
                            for i in range(max(0, min(int(obtained.value), count))):
                                try:
                                    oleaut32.VariantClear(ctypes.byref(children[i]))
                                except Exception:
                                    pass
                    finally:
                        _release(acc_ptr)

                visit(root, 0)
                if best_score >= 150:
                    return best_url
        return best_url if best_score >= 70 else ""

def normalize_document_url(value: str) -> str:
    """Return a stable key for an HTTP(S) document URL.

    Query strings and fragments are intentionally ignored so ChatGPT's transient
    navigation parameters do not create a new document identity. The conversation
    path itself remains part of the key.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except Exception:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return ""
    host = parsed.netloc.casefold()
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, "", ""))

def make_document_key(name: str = "", value: str = "") -> str:
    normalized_url = normalize_document_url(value)
    if normalized_url:
        return "url:" + normalized_url
    title = re.sub(r"\s+", " ", str(name or "").strip()).casefold()
    return ("title:" + title) if title else ""


__all__=[name for name in globals() if not name.startswith("__")]
