from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ...platform.windows.msaa import MsaaTreeSnapshot, msaa_role_name


class AccessibilityItemsDialog(tk.Toplevel):
    """Browse, search, inspect, and explicitly invoke one MSAA accessibility item."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        label: str,
        top_hwnd: int,
        snapshot: MsaaTreeSnapshot,
        on_click: Callable[[int, str], None],
    ):
        super().__init__(parent)
        self.title(f"Accessibility Items - {label}")
        self.geometry("1180x720")
        self.minsize(760, 480)
        self.transient(parent)
        self._snapshot = snapshot
        self._top_hwnd = int(top_hwnd)
        self._on_click = on_click
        self._all_elements = tuple(snapshot.elements)

        ttk.Label(
            self,
            text=(
                f"Browser HWND {self._top_hwnd} • source HWND {snapshot.source_hwnd} • "
                f"object {snapshot.object_id} • {len(snapshot.elements):,} accessibility items"
            ),
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(
            self,
            text="Search the accessibility tree, then inspect an item or invoke its default action.",
            wraplength=1120,
        ).pack(anchor="w", padx=12, pady=(0, 6))

        search_row = ttk.Frame(self)
        search_row.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Label(search_row, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(search_row, text="Clear", command=lambda: self.search_var.set("")).pack(side="left")
        self.search_count_var = tk.StringVar(value=f"{len(self._all_elements):,} shown")
        ttk.Label(search_row, textvariable=self.search_count_var).pack(side="left", padx=(10, 0))

        body = ttk.Panedwindow(self, orient="vertical")
        body.pack(fill="both", expand=True, padx=12)

        list_frame = ttk.Frame(body)
        detail_frame = ttk.Frame(body)
        body.add(list_frame, weight=3)
        body.add(detail_frame, weight=2)

        self.tree = ttk.Treeview(
            list_frame,
            columns=("index", "name", "role", "action"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("index", text="#")
        self.tree.heading("name", text="Accessible name")
        self.tree.heading("role", text="Role")
        self.tree.heading("action", text="Default action")
        self.tree.column("index", width=70, minwidth=55, stretch=False, anchor="e")
        self.tree.column("name", width=650, minwidth=220, stretch=True)
        self.tree.column("role", width=150, minwidth=100, stretch=False)
        self.tree.column("action", width=190, minwidth=100, stretch=True)
        yscroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.metadata = tk.Text(detail_frame, wrap="word", height=10, state="disabled")
        meta_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.metadata.yview)
        self.metadata.configure(yscrollcommand=meta_scroll.set)
        self.metadata.grid(row=0, column=0, sticky="nsew")
        meta_scroll.grid(row=0, column=1, sticky="ns")
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=12, pady=12)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Click Accessibility Item", command=self._click_selected).pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="Inspect Accessibility Item", command=self._inspect_selected).pack(side="right", padx=(0, 8))

        self.search_var.trace_add("write", self._apply_search_filter)
        self.tree.bind("<Double-1>", lambda _event: self._inspect_selected())
        self.bind("<Control-f>", self._focus_search)
        self.bind("<Control-F>", self._focus_search)
        self._populate_tree(self._all_elements)

    @staticmethod
    def _search_text(element) -> str:
        role = msaa_role_name(element.role)
        numeric_role = int(element.role or 0)
        return " ".join(
            (
                str(element.index),
                element.name or "",
                role,
                str(numeric_role),
                f"0x{numeric_role:02x}",
                element.default_action or "",
            )
        ).casefold()

    def _populate_tree(self, elements) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        rows = tuple(elements)
        for element in rows:
            self.tree.insert(
                "",
                "end",
                iid=str(element.index),
                values=(
                    element.index,
                    element.name or "(unnamed)",
                    msaa_role_name(element.role),
                    element.default_action or "",
                ),
            )
        self.search_count_var.set(f"{len(rows):,} of {len(self._all_elements):,} shown")

    def _apply_search_filter(self, *_args) -> None:
        terms = [term for term in self.search_var.get().casefold().split() if term]
        if not terms:
            matches = self._all_elements
        else:
            matches = tuple(
                element for element in self._all_elements
                if all(term in self._search_text(element) for term in terms)
            )
        self._populate_tree(matches)
        children = self.tree.get_children()
        if terms and children:
            first = children[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.tree.see(first)

    def _focus_search(self, _event=None):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        return "break"

    def _selected_element(self):
        selection = self.tree.selection()
        if not selection:
            return None
        index = int(selection[0])
        return next((item for item in self._snapshot.elements if item.index == index), None)

    def _inspect_selected(self) -> None:
        element = self._selected_element()
        if element is None:
            return
        snapshot = self._snapshot
        lines = [
            f"Index: {element.index}",
            f"Name: {element.name or '(unnamed)'}",
            f"Role: {msaa_role_name(element.role)} ({element.role})",
            f"Default action: {element.default_action or '(none exposed)'}",
            f"Browser HWND: {self._top_hwnd}",
            f"Source HWND: {snapshot.source_hwnd}",
            f"MSAA object ID: {snapshot.object_id}",
            f"Composer state: {snapshot.composer_state}",
            f"Composer name: {snapshot.composer_name or '(none)'}",
            f"Document name: {snapshot.document_name or '(none)'}",
            f"Document value: {snapshot.document_value or '(none)'}",
            f"Document key: {snapshot.document_key or '(none)'}",
        ]
        self.metadata.configure(state="normal")
        self.metadata.delete("1.0", "end")
        self.metadata.insert("1.0", "\n".join(lines))
        self.metadata.configure(state="disabled")

    def _click_selected(self) -> None:
        element = self._selected_element()
        if element is None:
            return
        self._on_click(element.index, element.name)

