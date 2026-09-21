from __future__ import annotations
import ctypes
import os
from contextlib import contextmanager
from .constants import *
HRESULT=ctypes.c_int32
LONG=ctypes.c_int32
ULONG=ctypes.c_uint32
DWORD=ctypes.c_uint32
WORD=ctypes.c_uint16
BYTE=ctypes.c_ubyte
PVOID=ctypes.c_void_p
WINFUNCTYPE=getattr(ctypes,"WINFUNCTYPE",ctypes.CFUNCTYPE)

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", BYTE * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "GUID":
        import uuid

        u = uuid.UUID(value)
        raw = u.bytes_le
        return cls(
            int.from_bytes(raw[0:4], "little"),
            int.from_bytes(raw[4:6], "little"),
            int.from_bytes(raw[6:8], "little"),
            (BYTE * 8)(*raw[8:16]),
        )

class _RecordValue(ctypes.Structure):
    _fields_ = [("pvRecord", PVOID), ("pRecInfo", PVOID)]

class _VariantValue(ctypes.Union):
    _fields_ = [
        ("llVal", ctypes.c_int64),
        ("lVal", LONG),
        ("ulVal", DWORD),
        ("bstrVal", PVOID),
        ("punkVal", PVOID),
        ("pdispVal", PVOID),
        ("byref", PVOID),
        ("record", _RecordValue),
        ("raw", BYTE * 16),
    ]

class VARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", WORD),
        ("wReserved1", WORD),
        ("wReserved2", WORD),
        ("wReserved3", WORD),
        ("value", _VariantValue),
    ]

IID_IACCESSIBLE = GUID.from_string("618736e0-3c3d-11cf-810c-00aa00389b71")
IID_IACCESSIBLE2 = GUID.from_string("e89f726e-c4f4-4c19-bb19-b647d7fa8478")
_OLEACC=None
_OLEAUT32=None
_OLE32=None
_USER32=None

def _dlls():
    global _OLEACC, _OLEAUT32, _OLE32, _USER32
    if os.name != "nt":
        raise RuntimeError("MSAA accessibility is only available on Windows.")
    if _OLEACC is None:
        _OLEACC = ctypes.WinDLL("oleacc.dll")
        _OLEAUT32 = ctypes.WinDLL("oleaut32.dll")
        _OLE32 = ctypes.WinDLL("ole32.dll")
        _USER32 = ctypes.WinDLL("user32.dll")

        _OLEACC.AccessibleObjectFromWindow.argtypes = [PVOID, DWORD, ctypes.POINTER(GUID), ctypes.POINTER(PVOID)]
        _OLEACC.AccessibleObjectFromWindow.restype = HRESULT
        _OLEACC.AccessibleChildren.argtypes = [PVOID, LONG, LONG, ctypes.POINTER(VARIANT), ctypes.POINTER(LONG)]
        _OLEACC.AccessibleChildren.restype = HRESULT
        _OLEACC.AccessibleObjectFromEvent.argtypes = [PVOID, DWORD, DWORD, ctypes.POINTER(PVOID), ctypes.POINTER(VARIANT)]
        _OLEACC.AccessibleObjectFromEvent.restype = HRESULT
        _OLEACC.WindowFromAccessibleObject.argtypes = [PVOID, ctypes.POINTER(PVOID)]
        _OLEACC.WindowFromAccessibleObject.restype = HRESULT

        _OLEAUT32.SysFreeString.argtypes = [PVOID]
        _OLEAUT32.SysFreeString.restype = None
        _OLEAUT32.VariantInit.argtypes = [ctypes.POINTER(VARIANT)]
        _OLEAUT32.VariantInit.restype = None
        _OLEAUT32.VariantClear.argtypes = [ctypes.POINTER(VARIANT)]
        _OLEAUT32.VariantClear.restype = HRESULT

        _OLE32.CoInitializeEx.argtypes = [PVOID, DWORD]
        _OLE32.CoInitializeEx.restype = HRESULT
        _OLE32.CoUninitialize.argtypes = []
        _OLE32.CoUninitialize.restype = None

        _USER32.EnumChildWindows.argtypes = [PVOID, PVOID, PVOID]
        _USER32.EnumChildWindows.restype = ctypes.c_int
        _USER32.GetAncestor.argtypes = [PVOID, ctypes.c_uint]
        _USER32.GetAncestor.restype = PVOID
        _USER32.IsChild.argtypes = [PVOID, PVOID]
        _USER32.IsChild.restype = ctypes.c_int
        _USER32.SetWinEventHook.argtypes = [DWORD, DWORD, PVOID, PVOID, DWORD, DWORD, DWORD]
        _USER32.SetWinEventHook.restype = PVOID
        _USER32.UnhookWinEvent.argtypes = [PVOID]
        _USER32.UnhookWinEvent.restype = ctypes.c_int
        _USER32.GetMessageW.argtypes = [PVOID, PVOID, ctypes.c_uint, ctypes.c_uint]
        _USER32.GetMessageW.restype = ctypes.c_int
        _USER32.PeekMessageW.argtypes = [PVOID, PVOID, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        _USER32.PeekMessageW.restype = ctypes.c_int
        _USER32.TranslateMessage.argtypes = [PVOID]
        _USER32.TranslateMessage.restype = ctypes.c_int
        _USER32.DispatchMessageW.argtypes = [PVOID]
        _USER32.DispatchMessageW.restype = ctypes.c_ssize_t
        _USER32.PostThreadMessageW.argtypes = [DWORD, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        _USER32.PostThreadMessageW.restype = ctypes.c_int
    return _OLEACC, _OLEAUT32, _OLE32, _USER32

@contextmanager
def _com_scope():
    _, _, ole32, _ = _dlls()
    hr = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
    should_uninit = hr in (S_OK, S_FALSE)
    # RPC_E_CHANGED_MODE simply means somebody initialized this thread in a
    # different COM apartment already; COM calls are still legal on the thread.
    if hr < 0 and hr != RPC_E_CHANGED_MODE:
        raise OSError(f"CoInitializeEx failed: 0x{hr & 0xFFFFFFFF:08X}")
    try:
        yield
    finally:
        if should_uninit:
            ole32.CoUninitialize()

def _self_variant() -> VARIANT:
    value = VARIANT()
    value.vt = VT_I4
    value.value.lVal = CHILDID_SELF
    return value

def _child_variant(child_id: int) -> VARIANT:
    value = VARIANT()
    value.vt = VT_I4
    value.value.lVal = int(child_id)
    return value

def _method(acc_ptr: int | PVOID, index: int, restype, *argtypes):
    raw = int(acc_ptr.value if isinstance(acc_ptr, PVOID) else acc_ptr or 0)
    if not raw:
        raise ValueError("Null IAccessible pointer")
    vtable_ptr = ctypes.cast(PVOID(raw), ctypes.POINTER(ctypes.POINTER(PVOID)))
    address = int(vtable_ptr.contents[index] or 0)
    if not address:
        raise ValueError(f"Missing IAccessible vtable method {index}")
    return WINFUNCTYPE(restype, PVOID, *argtypes)(address)

def _release(acc_ptr: int | PVOID) -> None:
    try:
        _method(acc_ptr, 2, ULONG)(PVOID(int(acc_ptr.value if isinstance(acc_ptr, PVOID) else acc_ptr or 0)))
    except Exception:
        pass

def _add_ref(acc_ptr: int) -> None:
    _method(acc_ptr, 1, ULONG)(PVOID(acc_ptr))

def _query_iaccessible(dispatch_ptr: int) -> int:
    if not dispatch_ptr:
        return 0
    out = PVOID()
    fn = _method(dispatch_ptr, 0, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(PVOID))
    hr = int(fn(PVOID(dispatch_ptr), ctypes.byref(IID_IACCESSIBLE), ctypes.byref(out)))
    if hr < 0 or not out.value:
        return 0
    return int(out.value)

def _query_interface(raw_ptr: int, iid: GUID) -> int:
    if not raw_ptr:
        return 0
    out = PVOID()
    try:
        fn = _method(raw_ptr, 0, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(PVOID))
        hr = int(fn(PVOID(raw_ptr), ctypes.byref(iid), ctypes.byref(out)))
        if hr < 0 or not out.value:
            return 0
        return int(out.value)
    except Exception:
        return 0

def _accessible_from_event_target(hwnd: int, object_id: int, child_id: int) -> tuple[int, VARIANT]:
    """Resolve one exact MSAA event tuple to its current accessible object.

    This is useful even when no new WinEvent has fired: after Firefox reports
    ``Stop answering`` we can actively ask Windows for that same object again
    and observe its accessible Name changing back to ``Send prompt``.  That
    avoids relying on mouse hover/focus to make Firefox emit another event.
    The returned IAccessible pointer and VARIANT are owned by the caller.
    """
    oleacc, oleaut32, _, _ = _dlls()
    child = VARIANT()
    oleaut32.VariantInit(ctypes.byref(child))
    acc = PVOID()
    try:
        hr = int(
            oleacc.AccessibleObjectFromEvent(
                PVOID(int(hwnd)),
                DWORD(int(object_id) & 0xFFFFFFFF),
                DWORD(int(child_id) & 0xFFFFFFFF),
                ctypes.byref(acc),
                ctypes.byref(child),
            )
        )
    except Exception:
        hr = -1
    if hr < 0 or not acc.value:
        try:
            oleaut32.VariantClear(ctypes.byref(child))
        except Exception:
            pass
        empty = VARIANT()
        oleaut32.VariantInit(ctypes.byref(empty))
        return 0, empty
    return int(acc.value), child

def _clear_variant(child: VARIANT) -> None:
    try:
        _, oleaut32, _, _ = _dlls()
        oleaut32.VariantClear(ctypes.byref(child))
    except Exception:
        pass

def _containing_document_accessible(acc_ptr: int, child: VARIANT, max_depth: int = 64) -> int:
    """Return an AddRef'd IAccessible for the element's containing document.

    Firefox normally exposes the IA2 ``containingDocument`` relation.  Some
    builds/materialization states omit that relation until an element is
    focused, though, which is exactly the hover-dependent behavior we want to
    avoid.  Fall back to the real accessible parent chain and stop at
    ROLE_SYSTEM_DOCUMENT, so callers can still reach the exact chat document
    without forcing a hover.
    """
    related = _ia2_containing_document_accessible(acc_ptr, child)
    if related:
        return related

    current = _materialize_accessible_child(acc_ptr, child)
    if not current:
        return 0
    try:
        for _ in range(max_depth):
            if _acc_role(current, _self_variant()) == ROLE_SYSTEM_DOCUMENT:
                # Transfer our owned reference to the caller.
                result = current
                current = 0
                return result
            parent = _acc_parent(current)
            if not parent:
                break
            _release(current)
            current = parent
    finally:
        if current:
            _release(current)
    return 0

def _materialize_accessible_child(acc_ptr: int, child: VARIANT) -> int:
    """Return an AddRef'd IAccessible for a VARIANT child when possible."""
    if not acc_ptr:
        return 0
    if child.vt != VT_I4 or int(child.value.lVal) == CHILDID_SELF:
        try:
            _add_ref(acc_ptr)
            return int(acc_ptr)
        except Exception:
            return 0

    dispatch = PVOID()
    try:
        fn = _method(acc_ptr, 9, HRESULT, VARIANT, ctypes.POINTER(PVOID))  # get_accChild
        hr = int(fn(PVOID(acc_ptr), child, ctypes.byref(dispatch)))
        if hr < 0 or not dispatch.value:
            return 0
        return _query_iaccessible(int(dispatch.value))
    except Exception:
        return 0
    finally:
        if dispatch.value:
            _release(int(dispatch.value))

def _ia2_containing_document_accessible(acc_ptr: int, child: VARIANT) -> int:
    """Return an AddRef'd IAccessible for Firefox's containing document.

    Firefox exposes the exact relation shown by its Accessibility Inspector as
    ``containing document``.  Returning the actual document accessible lets
    callers walk *that one chat document* directly instead of scanning browser
    chrome/background roots and trying to infer which Download button is newest.
    """
    concrete = _materialize_accessible_child(acc_ptr, child)
    if not concrete:
        return 0
    ia2 = 0
    try:
        ia2 = _query_interface(concrete, IID_IACCESSIBLE2)
        if not ia2:
            return 0

        relation_count = LONG()
        try:
            get_count = _method(ia2, 28, HRESULT, ctypes.POINTER(LONG))
            hr = int(get_count(PVOID(ia2), ctypes.byref(relation_count)))
        except Exception:
            return 0
        if hr < 0:
            return 0

        count = max(0, min(int(relation_count.value), 128))
        for index in range(count):
            relation = PVOID()
            try:
                get_relation = _method(ia2, 29, HRESULT, LONG, ctypes.POINTER(PVOID))
                hr = int(get_relation(PVOID(ia2), LONG(index), ctypes.byref(relation)))
                if hr < 0 or not relation.value:
                    continue

                relation_type = PVOID()
                try:
                    get_type = _method(int(relation.value), 3, HRESULT, ctypes.POINTER(PVOID))
                    hr_type = int(get_type(relation, ctypes.byref(relation_type)))
                    if hr_type < 0 or not relation_type.value:
                        continue
                    text = ctypes.wstring_at(relation_type.value).strip().replace(" ", "").casefold()
                finally:
                    if relation_type.value:
                        try:
                            _, oleaut32, _, _ = _dlls()
                            oleaut32.SysFreeString(relation_type)
                        except Exception:
                            pass

                if text != IA2_RELATION_CONTAINING_DOCUMENT:
                    continue

                target_count = LONG()
                get_targets_count = _method(int(relation.value), 5, HRESULT, ctypes.POINTER(LONG))
                if int(get_targets_count(relation, ctypes.byref(target_count))) < 0 or target_count.value <= 0:
                    continue
                target = PVOID()
                get_target = _method(int(relation.value), 6, HRESULT, LONG, ctypes.POINTER(PVOID))
                if int(get_target(relation, LONG(0), ctypes.byref(target))) < 0 or not target.value:
                    continue
                try:
                    # QueryInterface gives the returned pointer its own ref; the
                    # relation target itself is released before returning.
                    return _query_iaccessible(int(target.value))
                finally:
                    _release(int(target.value))
            except Exception:
                continue
            finally:
                if relation.value:
                    _release(int(relation.value))
        return 0
    finally:
        if ia2:
            _release(ia2)
        _release(concrete)

def _ia2_containing_document(acc_ptr: int, child: VARIANT) -> tuple[str, str, str]:
    """Resolve Firefox's explicit IAccessible2 containingDocument relation."""
    doc_acc = _ia2_containing_document_accessible(acc_ptr, child)
    if not doc_acc:
        return "", "", ""
    try:
        self_child = _self_variant()
        name = _acc_name(doc_acc, self_child).strip()
        value = _acc_value(doc_acc, self_child).strip()
        return name, value, make_document_key(name, value)
    finally:
        _release(doc_acc)

def _bstr_property(acc_ptr: int, child: VARIANT, method_index: int) -> str:
    _, oleaut32, _, _ = _dlls()
    result = PVOID()
    fn = _method(acc_ptr, method_index, HRESULT, VARIANT, ctypes.POINTER(PVOID))
    hr = int(fn(PVOID(acc_ptr), child, ctypes.byref(result)))
    if hr < 0 or not result.value:
        return ""
    try:
        return ctypes.wstring_at(result.value).strip()
    finally:
        oleaut32.SysFreeString(result)

def _acc_name(acc_ptr: int, child: VARIANT) -> str:
    try:
        return _bstr_property(acc_ptr, child, 10)
    except Exception:
        return ""

def _acc_value(acc_ptr: int, child: VARIANT) -> str:
    try:
        return _bstr_property(acc_ptr, child, 11)
    except Exception:
        return ""


__all__=[name for name in globals() if not name.startswith("__")]
