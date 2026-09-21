from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ...domain import CommandSpec


class CommandDialog(simpledialog.Dialog):
    def __init__(
        self,
        parent: tk.Misc,
        command: CommandSpec | None = None,
        *,
        initial_dir: Path | str | None = None,
    ):
        self.command = command or CommandSpec()
        self.result: CommandSpec | None = None
        self.initial_dir = Path(initial_dir).expanduser() if initial_dir else None
        super().__init__(parent, title="Command")

    def body(self, master: tk.Misc):
        ttk.Label(master, text="Label").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Label(master, text="Command or script path").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.label_var = tk.StringVar(value=self.command.label)
        self.command_var = tk.StringVar(value=self.command.command)
        self.auto_var = tk.BooleanVar(value=self.command.auto_run)
        self.order_var = tk.StringVar(value=str(max(0, min(9, int(self.command.auto_run_order)))))
        self.completion_mode_var = tk.StringVar(
            value="delay" if self.command.completion_mode == "delay" else "exit_code"
        )
        self.delay_var = tk.StringVar(value=self._format_delay(self.command.completion_delay_seconds))
        self.auto_close_var = tk.BooleanVar(value=self.command.auto_close_on_success)

        ttk.Entry(master, textvariable=self.label_var, width=56).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=6, pady=6
        )
        ttk.Entry(master, textvariable=self.command_var, width=56).grid(
            row=1, column=1, sticky="ew", padx=6, pady=6
        )
        ttk.Button(master, text="Browse…", command=self._browse).grid(row=1, column=2, padx=6, pady=6)

        auto_row = ttk.Frame(master)
        auto_row.grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=6)
        ttk.Checkbutton(
            auto_row,
            text="Auto-run after successful extraction",
            variable=self.auto_var,
            command=self._sync_auto_state,
        ).pack(side="left")
        ttk.Label(auto_row, text="Order:").pack(side="left", padx=(12, 4))
        self.order_combo = ttk.Combobox(
            auto_row,
            textvariable=self.order_var,
            values=tuple(str(value) for value in range(10)),
            width=3,
            state="readonly",
        )
        self.order_combo.pack(side="left")

        ttk.Checkbutton(
            master,
            text="Auto close command window on success",
            variable=self.auto_close_var,
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=6, pady=(2, 6))

        completion = ttk.LabelFrame(master, text="Auto-run completion", padding=8)
        completion.grid(row=4, column=0, columnspan=3, sticky="ew", padx=6, pady=(4, 6))
        completion.columnconfigure(1, weight=1)
        self.exit_radio = ttk.Radiobutton(
            completion,
            text="Wait for exit code",
            variable=self.completion_mode_var,
            value="exit_code",
            command=self._sync_auto_state,
        )
        self.exit_radio.grid(row=0, column=0, columnspan=3, sticky="w", pady=2)
        self.delay_radio = ttk.Radiobutton(
            completion,
            text="Wait for delay",
            variable=self.completion_mode_var,
            value="delay",
            command=self._sync_auto_state,
        )
        self.delay_radio.grid(row=1, column=0, sticky="w", pady=2)
        ttk.Label(completion, text="Delay:").grid(row=1, column=1, sticky="e", padx=(16, 4))
        self.delay_entry = ttk.Entry(completion, textvariable=self.delay_var, width=8)
        self.delay_entry.grid(row=1, column=2, sticky="w")
        ttk.Label(completion, text="seconds (default 5)").grid(row=1, column=3, sticky="w", padx=(4, 0))

        self._sync_auto_state()
        master.columnconfigure(1, weight=1)
        return master

    @staticmethod
    def _format_delay(value: float) -> str:
        value = float(value or 5.0)
        return str(int(value)) if value.is_integer() else f"{value:g}"

    def _sync_auto_state(self) -> None:
        enabled = bool(self.auto_var.get())
        if hasattr(self, "order_combo"):
            self.order_combo.configure(state="readonly" if enabled else "disabled")
        radio_state = "normal" if enabled else "disabled"
        for radio in (getattr(self, "exit_radio", None), getattr(self, "delay_radio", None)):
            if radio is not None:
                radio.configure(state=radio_state)
        if hasattr(self, "delay_entry"):
            delay_enabled = enabled and self.completion_mode_var.get() == "delay"
            self.delay_entry.configure(state="normal" if delay_enabled else "disabled")

    def _browse(self):
        initialdir = None
        if self.initial_dir:
            candidate = self.initial_dir
            if candidate.is_file():
                candidate = candidate.parent
            while not candidate.exists() and candidate != candidate.parent:
                candidate = candidate.parent
            if candidate.is_dir():
                initialdir = str(candidate)
        path = filedialog.askopenfilename(
            parent=self,
            title="Select script",
            initialdir=initialdir,
            filetypes=[
                ("Scripts", "*.cmd *.bat *.ps1 *.sh *.py"),
                ("Windows command", "*.cmd *.bat"),
                ("PowerShell", "*.ps1"),
                ("Shell", "*.sh"),
                ("Python", "*.py"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.command_var.set(path)
            current_label = self.label_var.get().strip()
            if not current_label or current_label == "Command":
                self.label_var.set(Path(path).stem)

    def validate(self):
        if not self.label_var.get().strip() or not self.command_var.get().strip():
            messagebox.showerror(
                "Missing value", "Both label and command/script path are required.", parent=self
            )
            return False
        try:
            delay = float(self.delay_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid delay", "Delay must be a number of seconds.", parent=self)
            return False
        if delay < 0.1 or delay > 86400:
            messagebox.showerror(
                "Invalid delay", "Delay must be between 0.1 and 86400 seconds.", parent=self
            )
            return False
        return True

    def apply(self):
        self.result = CommandSpec(
            label=self.label_var.get().strip(),
            command=self.command_var.get().strip(),
            auto_run=self.auto_var.get(),
            auto_run_order=int(self.order_var.get() or 0),
            completion_mode="delay" if self.completion_mode_var.get() == "delay" else "exit_code",
            completion_delay_seconds=float(self.delay_var.get().strip() or "5"),
            auto_close_on_success=self.auto_close_var.get(),
            command_id=self.command.command_id,
        )
