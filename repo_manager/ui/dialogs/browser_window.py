from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence

from ...browser.models import WindowsBrowserRun
from ...platform.windows.window_highlight import flash_windows_hwnd_border

ReloadCallback = Callable[[Callable[[Sequence[WindowsBrowserRun], int | None], None]], None]


class BrowserWindowDiscoveryDialog(tk.Toplevel):
    """Let the user bind a saved URL to one discovered browser window."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        label: str,
        saved_url: str,
        windows: Sequence[WindowsBrowserRun],
        recommended_index: int | None,
        reload_callback: ReloadCallback | None = None,
    ):
        super().__init__(parent)
        self.title(f"Discover URL - {label}")
        self.geometry("1100x500")
        self.minsize(760, 360)
        self.transient(parent)
        self.grab_set()
        self.result: WindowsBrowserRun | None = None
        self._windows = list(windows)
        self._reload_callback = reload_callback
        self._suppress_selection_flash = False

        ttk.Label(
            self,
            text=f"Saved URL: {saved_url}",
            font=("TkDefaultFont", 10, "bold"),
            wraplength=1060,
        ).pack(anchor="w", padx=14, pady=(14, 4))
        ttk.Label(
            self,
            text=(
                "All supported browser windows are shown. Active URL detection uses passive "
                "Windows accessibility and may be unavailable for a dormant window; those "
                "windows remain selectable. The best URL/previous-window/title match is highlighted as a recommendation."
            ),
            wraplength=1060,
        ).pack(anchor="w", padx=14, pady=(0, 10))

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=14)
        self.tree = ttk.Treeview(
            frame,
            columns=("browser", "title", "url", "hwnd"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("browser", text="Browser")
        self.tree.heading("title", text="Window title")
        self.tree.heading("url", text="Active URL")
        self.tree.heading("hwnd", text="HWND")
        self.tree.column("browser", width=105, minwidth=80, stretch=False)
        self.tree.column("title", width=280, minwidth=120, stretch=True)
        self.tree.column("url", width=540, minwidth=220, stretch=True)
        self.tree.column("hwnd", width=100, minwidth=90, stretch=False, anchor="e")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.recommendation_var = tk.StringVar()
        ttk.Label(self, textvariable=self.recommendation_var).pack(anchor="w", padx=14, pady=(8, 0))

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=14, pady=14)
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(buttons, text="Use Selected Window", command=self._ok).pack(side="right", padx=(0, 8))
        self.reload_button = ttk.Button(buttons, text="Reload", command=self._reload)
        self.reload_button.pack(side="left")
        if reload_callback is None:
            self.reload_button.configure(state="disabled")

        self.tree.bind("<Double-1>", lambda _event: self._ok())
        self.tree.bind("<<TreeviewSelect>>", self._on_selection_changed)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._replace_windows(self._windows, recommended_index)
        self.wait_window(self)

    def _replace_windows(
        self,
        windows: Sequence[WindowsBrowserRun],
        recommended_index: int | None,
    ) -> None:
        if not self.winfo_exists():
            return
        self._windows = list(windows)
        self._suppress_selection_flash = True
        try:
            for iid in self.tree.get_children(""):
                self.tree.delete(iid)
            for index, run in enumerate(self._windows):
                self.tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        run.browser_name,
                        run.window_title or "(untitled)",
                        run.url or "(unreadable - select manually if this is the right window)",
                        str(run.hwnd),
                    ),
                )
            if recommended_index is not None and 0 <= recommended_index < len(self._windows):
                iid = str(recommended_index)
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self.recommendation_var.set(
                    "Best-match window is highlighted; choose another one if needed."
                )
            elif self._windows:
                self.recommendation_var.set(
                    "No strong URL match was readable; choose the correct window manually."
                )
            else:
                self.recommendation_var.set("No supported browser windows are currently open.")
        finally:
            self._suppress_selection_flash = False
        self.reload_button.configure(state="normal" if self._reload_callback else "disabled")

    def _reload(self) -> None:
        if self._reload_callback is None:
            return
        self.reload_button.configure(state="disabled")
        self.recommendation_var.set("Reloading browser windows…")
        self._reload_callback(self._replace_windows)

    def _on_selection_changed(self, _event=None) -> None:
        if self._suppress_selection_flash or os.name != "nt":
            return
        selection = self.tree.selection()
        if not selection:
            return
        try:
            run = self._windows[int(selection[0])]
        except (IndexError, TypeError, ValueError):
            return
        flash_windows_hwnd_border(run.hwnd)

    def _ok(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            selected = self._windows[int(selection[0])]
        except (IndexError, TypeError, ValueError):
            return
        # Flash once more on confirmation so the final binding is visually
        # unambiguous even if the user never changed the recommended row.
        flash_windows_hwnd_border(selected.hwnd)
        self.result = selected
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()
