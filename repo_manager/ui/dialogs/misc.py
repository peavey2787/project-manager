from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Sequence

from ...archive.snapshots import SnapshotDiff


class SnapshotDiffDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, diff: SnapshotDiff):
        super().__init__(parent)
        self.title("Project structure changed")
        screen_w = max(800, int(self.winfo_screenwidth()))
        screen_h = max(600, int(self.winfo_screenheight()))
        width = min(980, screen_w - 80)
        height = min(700, screen_h - 100)
        self.geometry(f"{width}x{height}")
        self.minsize(min(760, width), min(520, height))
        self.transient(parent)
        self.grab_set()
        self.result = "ignore"
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        ttk.Label(
            self,
            text="The extracted project structure differs from the saved snapshot.",
            font=("TkDefaultFont", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 6))
        ttk.Label(
            self,
            text="Review the added and missing files/folders below. Build/output folders configured in Project Settings are ignored. 'Accept current' updates the snapshot to exactly match what is present now.",
            wraplength=max(680, width - 60),
        ).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))

        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew", padx=14, pady=4)
        self._add_list(notebook, f"Added ({len(diff.added)})", diff.added)
        self._add_list(notebook, f"Missing ({len(diff.missing)})", diff.missing)

        buttons = ttk.Frame(self)
        buttons.grid(row=3, column=0, sticky="ew", padx=14, pady=14)
        ttk.Button(buttons, text="Ignore", command=self._ignore).pack(side="right")
        ttk.Button(buttons, text="Accept current as new snapshot", command=self._accept).pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self._ignore)
        self.update_idletasks()
        try:
            px = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2)
            py = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_height()) // 2)
            self.geometry(f"+{max(0, px)}+{max(0, py)}")
        except tk.TclError:
            pass
        self.wait_window(self)

    @staticmethod
    def _add_list(notebook: ttk.Notebook, title: str, values: Sequence[str]) -> None:
        frame = ttk.Frame(notebook)
        text = tk.Text(frame, wrap="none", undo=False)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text.insert("1.0", "\n".join(values) if values else "(none)")
        text.configure(state="disabled")
        notebook.add(frame, text=title)

    def _accept(self) -> None:
        self.result = "accept"
        self.destroy()

    def _ignore(self) -> None:
        self.result = "ignore"
        self.destroy()


class MultiSelectDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, prompt: str, values: Sequence[str]):
        super().__init__(parent)
        self.title(title)
        self.geometry("780x460")
        self.minsize(520, 300)
        self.transient(parent)
        self.grab_set()
        self.result: list[int] | None = None

        ttk.Label(self, text=prompt, wraplength=740).pack(anchor="w", padx=14, pady=(14, 8))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=14)
        self.listbox = tk.Listbox(frame, selectmode="extended")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=yscroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        for value in values:
            self.listbox.insert("end", value)
        if len(values) == 1:
            self.listbox.selection_set(0)

        row = ttk.Frame(self)
        row.pack(fill="x", padx=14, pady=14)
        ttk.Button(row, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(row, text="Use selected", command=self._ok).pack(side="right", padx=(0, 8))
        ttk.Button(row, text="Select all", command=lambda: self.listbox.selection_set(0, "end")).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window(self)

    def _ok(self) -> None:
        selected = list(self.listbox.curselection())
        if selected:
            self.result = selected
            self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class SingleSelectDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, prompt: str, values: Sequence[str]):
        super().__init__(parent)
        self.title(title)
        self.geometry("780x420")
        self.minsize(520, 280)
        self.transient(parent)
        self.grab_set()
        self.result: int | None = None

        ttk.Label(self, text=prompt, wraplength=740).pack(anchor="w", padx=14, pady=(14, 8))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=14)
        self.listbox = tk.Listbox(frame, selectmode="browse")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=yscroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        for value in values:
            self.listbox.insert("end", value)
        if values:
            self.listbox.selection_set(0)

        row = ttk.Frame(self)
        row.pack(fill="x", padx=14, pady=14)
        ttk.Button(row, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(row, text="Use selected", command=self._ok).pack(side="right", padx=(0, 8))
        self.listbox.bind("<Double-1>", lambda _event: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window(self)

    def _ok(self) -> None:
        selected = self.listbox.curselection()
        if selected:
            self.result = int(selected[0])
            self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()
