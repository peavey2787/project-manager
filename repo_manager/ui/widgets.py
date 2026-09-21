from __future__ import annotations
import tkinter as tk
from tkinter import ttk

class ScrollableFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))



class ToolTip:
    """Small delayed hover tooltip for compact icon actions."""

    def __init__(self, widget: tk.Misc, text: str, *, delay_ms: int = 450):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add=True)
        widget.bind("<Leave>", self._hide, add=True)
        widget.bind("<ButtonPress>", self._hide, add=True)

    def _schedule(self, _event=None) -> None:
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay_ms, self._show)
        except tk.TclError:
            self._after_id = None

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        if self._window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            window = tk.Toplevel(self.widget)
            window.wm_overrideredirect(True)
            window.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                window,
                text=self.text,
                justify="left",
                background="#111111",
                foreground="#ffffff",
                relief="solid",
                borderwidth=1,
                padx=7,
                pady=5,
                wraplength=300,
            )
            label.pack()
            self._window = window
        except tk.TclError:
            self._window = None

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


def bind_tooltip(widget: tk.Misc, text: str) -> ToolTip:
    tooltip = ToolTip(widget, text)
    setattr(widget, "_prm_tooltip", tooltip)
    return tooltip


def style_black_combobox_popup(combo: ttk.Combobox) -> None:
    """Keep a readonly combobox popup readable and black in either app theme."""
    try:
        popdown = combo.tk.call("ttk::combobox::PopdownWindow", str(combo))
        listbox = f"{popdown}.f.l"
        combo.tk.call(
            listbox,
            "configure",
            "-background", "#000000",
            "-foreground", "#ffffff",
            "-selectbackground", "#3f6ea8",
            "-selectforeground", "#ffffff",
        )
    except tk.TclError:
        pass
