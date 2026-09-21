from __future__ import annotations
import tkinter as tk
from tkinter import messagebox,simpledialog,ttk
from ...domain import UrlSpec

class UrlDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, value: UrlSpec | None = None):
        self.value = value or UrlSpec()
        self.result: UrlSpec | None = None
        super().__init__(parent, title="URL")

    def body(self, master: tk.Misc):
        self.label_var = tk.StringVar(value=self.value.label)
        self.url_var = tk.StringVar(value=self.value.url)
        self.watch_chat_var = tk.BooleanVar(value=self.value.watch_chat)
        self.ensure_open_var = tk.BooleanVar(value=self.value.ensure_open)
        self._watch_chat_touched = False
        ttk.Label(master, text="Label").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(master, textvariable=self.label_var, width=58).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Label(master, text="URL").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        url_entry = ttk.Entry(master, textvariable=self.url_var, width=58)
        url_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=6)
        watch = ttk.Checkbutton(
            master,
            text="Watch ChatGPT response status with Windows accessibility",
            variable=self.watch_chat_var,
            command=self._mark_watch_chat_touched,
        )
        watch.grid(row=2, column=1, sticky="w", padx=6, pady=6)
        ttk.Checkbutton(
            master,
            text="Ensure this URL stays open (restore on app startup if missing)",
            variable=self.ensure_open_var,
        ).grid(row=3, column=1, sticky="w", padx=6, pady=6)
        self.url_var.trace_add("write", self._auto_watch_chatgpt)
        master.columnconfigure(1, weight=1)
        return master

    def _mark_watch_chat_touched(self):
        self._watch_chat_touched = True

    def _auto_watch_chatgpt(self, *_args):
        if self._watch_chat_touched:
            return
        value = self.url_var.get().strip().casefold()
        self.watch_chat_var.set("chatgpt.com" in value)

    def validate(self):
        if not self.label_var.get().strip() or not self.url_var.get().strip():
            messagebox.showerror("Missing value", "Label and URL are required.", parent=self)
            return False
        return True

    def apply(self):
        self.result = UrlSpec(
            label=self.label_var.get().strip(),
            url=self.url_var.get().strip(),
            watch_chat=self.watch_chat_var.get(),
            ensure_open=self.ensure_open_var.get(),
            last_browser_id=self.value.last_browser_id,
            window_x=self.value.window_x,
            window_y=self.value.window_y,
            window_width=self.value.window_width,
            window_height=self.value.window_height,
            window_maximized=self.value.window_maximized,
            last_hwnd=self.value.last_hwnd,
            last_owner_pid=self.value.last_owner_pid,
            url_id=self.value.url_id,
        )

