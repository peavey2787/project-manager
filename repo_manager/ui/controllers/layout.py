from __future__ import annotations

import faulthandler
import os
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from tkinter import filedialog, messagebox, simpledialog, ttk

from ...archive.facade import *
from ...browser.facade import *
from ...chat import ChatEvent, ChatState, ChatWatchManager
from ...commands.facade import *
from ...config import config_path, save_config
from ...domain import AppConfig, CommandSpec, Project, UrlGroup, UrlSpec
from ...platform.windows.desktop import open_folder, open_in_vscode
from ..constants import *
from ..dialogs.command import CommandDialog
from ..dialogs.url import UrlDialog
from ..dialogs.misc import MultiSelectDialog, SnapshotDiffDialog
from ..widgets import ScrollableFrame

class LayoutController:
    def _build_style(self):
        self.style = ttk.Style(self)
        try:
            if "clam" in self.style.theme_names():
                self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.style.configure("Action.TButton", font=("TkDefaultFont", 11, "bold"), padding=(12, 10))
        self.style.configure("IconAction.TButton", font=("Segoe UI Symbol", 15, "bold"), padding=(7, 6))
        self.style.configure("ImageAction.TButton", padding=(0, 0))
        self.style.configure("CommandImageAction.TButton", padding=(3, 3), borderwidth=2, relief="flat")
        self.style.configure("ActionGroup.TLabel", font=("TkDefaultFont", 9, "bold"))
        self.style.configure("Section.TLabel", font=("TkDefaultFont", 11, "bold"))
        self.bind_class("TButton", "<Map>", self._ensure_text_button_min_width, add="+")

    @staticmethod
    def _ensure_text_button_min_width(event) -> None:
        """Keep mapped text buttons wide enough to show their full label."""
        button = event.widget
        try:
            text = str(button.cget("text") or "")
            if not text:
                return
            desired = max(3, len(text) + 1)
            current = int(button.cget("width") or 0)
            if current == 0 or (current < 0 and abs(current) < desired):
                # ttk uses a negative width as a minimum character width.
                button.configure(width=-desired)
        except (tk.TclError, TypeError, ValueError):
            pass

    def _apply_theme(self, theme: str, *, persist: bool = True):
        theme = "light" if str(theme).casefold() == "light" else "dark"
        self.config_data.theme = theme

        if theme == "dark":
            bg = "#1e1f22"
            panel = "#25262a"
            field = "#2b2d31"
            fg = "#f2f3f5"
            muted = "#b5bac1"
            select = "#3f6ea8"
            button = "#303238"
            border = "#45474e"
        else:
            bg = "#f3f3f3"
            panel = "#ffffff"
            field = "#ffffff"
            fg = "#202020"
            muted = "#5d5d5d"
            select = "#3478c6"
            button = "#ececec"
            border = "#c9c9c9"

        self.configure(bg=bg)
        style = self.style
        style.configure(".", background=bg, foreground=fg)
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, foreground=fg, bordercolor=border)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Section.TLabel", background=bg, foreground=fg, font=("TkDefaultFont", 11, "bold"))
        style.configure("TButton", background=button, foreground=fg, bordercolor=border, focusthickness=1)
        style.map("TButton", background=[("active", select)], foreground=[("active", "#ffffff")])
        style.configure("Action.TButton", background=button, foreground=fg, bordercolor=border, font=("TkDefaultFont", 11, "bold"), padding=(12, 10))
        style.map("Action.TButton", background=[("active", select)], foreground=[("active", "#ffffff")])
        style.configure("IconAction.TButton", background=button, foreground=fg, bordercolor=border, font=("Segoe UI Symbol", 15, "bold"), padding=(7, 6))
        style.map("IconAction.TButton", background=[("active", select)], foreground=[("active", "#ffffff")])
        style.configure("ImageAction.TButton", background=button, foreground=fg, bordercolor=border, padding=(0, 0))
        style.map("ImageAction.TButton", background=[("active", select), ("pressed", select)])
        style.configure(
            "CommandImageAction.TButton",
            background=button,
            foreground=fg,
            bordercolor=border,
            padding=(3, 3),
            borderwidth=2,
            relief="flat",
        )
        style.map(
            "CommandImageAction.TButton",
            background=[("active", select), ("pressed", select)],
            bordercolor=[("active", select), ("pressed", select)],
            relief=[("pressed", "sunken"), ("active", "raised")],
        )
        style.configure("ActionGroup.TLabel", background=bg, foreground=muted, font=("TkDefaultFont", 9, "bold"))
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.map("TCheckbutton", background=[("active", bg)], foreground=[("active", fg)])
        style.configure("TNotebook", background=bg, bordercolor=border)
        style.configure("TNotebook.Tab", background=button, foreground=fg, padding=(10, 6))
        style.map("TNotebook.Tab", background=[("selected", panel), ("active", select)], foreground=[("selected", fg), ("active", "#ffffff")])
        style.configure("Treeview", background=field, fieldbackground=field, foreground=fg, bordercolor=border)
        style.map("Treeview", background=[("selected", select)], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background=button, foreground=fg)
        style.map("Treeview.Heading", background=[("active", select)], foreground=[("active", "#ffffff")])
        style.configure("TEntry", fieldbackground=field, foreground=fg, insertcolor=fg)
        style.configure("TCombobox", fieldbackground=field, foreground=fg)
        style.configure(
            "Prompt.TCombobox",
            fieldbackground="#000000",
            background="#000000",
            foreground="#ffffff",
            arrowcolor="#ffffff",
        )
        style.map(
            "Prompt.TCombobox",
            fieldbackground=[("readonly", "#000000")],
            foreground=[("readonly", "#ffffff")],
            selectbackground=[("readonly", "#000000")],
            selectforeground=[("readonly", "#ffffff")],
        )
        style.configure("Protocol.TCombobox", fieldbackground="#000000", background="#000000", foreground="#ffffff", arrowcolor="#ffffff")
        style.map("Protocol.TCombobox", fieldbackground=[("readonly", "#000000")], foreground=[("readonly", "#ffffff")], selectbackground=[("readonly", "#000000")], selectforeground=[("readonly", "#ffffff")])
        style.configure("Certificate.TCombobox", fieldbackground="#000000", background="#000000", foreground="#ffffff", arrowcolor="#ffffff")
        style.map("Certificate.TCombobox", fieldbackground=[("disabled", "#000000"), ("readonly", "#000000")], foreground=[("disabled", "#ffffff"), ("readonly", "#ffffff")], selectbackground=[("readonly", "#000000")], selectforeground=[("readonly", "#ffffff")])
        style.configure("TScrollbar", background=button, troughcolor=bg, bordercolor=border, arrowcolor=fg)

        # Tk widgets are not controlled by ttk.Style. option_add also applies
        # to future widgets/dialogs created after a theme switch.
        for pattern, value in {
            "*background": bg,
            "*foreground": fg,
            "*insertBackground": fg,
            "*selectBackground": select,
            "*selectForeground": "#ffffff",
            "*Text.background": field,
            "*Text.foreground": fg,
            "*Listbox.background": field,
            "*Listbox.foreground": fg,
            "*Canvas.background": bg,
            "*Menu.background": panel,
            "*Menu.foreground": fg,
            "*Menu.activeBackground": select,
            "*Menu.activeForeground": "#ffffff",
        }.items():
            self.option_add(pattern, value)

        # Existing classic Tk widgets must be recolored directly.
        for widget in self.winfo_children():
            self._theme_tk_descendants(widget, bg, panel, field, fg, select)

        if hasattr(self, "theme_button"):
            self.theme_button.configure(text="Light Mode" if theme == "dark" else "Dark Mode")
        if persist:
            self._save_config()

    def _theme_tk_descendants(self, widget: tk.Misc, bg: str, panel: str, field: str, fg: str, select: str):
        try:
            if isinstance(widget, tk.Text):
                widget.configure(background=field, foreground=fg, insertbackground=fg, selectbackground=select, selectforeground="#ffffff")
            elif isinstance(widget, tk.Listbox):
                widget.configure(background=field, foreground=fg, selectbackground=select, selectforeground="#ffffff")
            elif isinstance(widget, tk.Canvas):
                widget.configure(background=bg)
            elif isinstance(widget, tk.Menu):
                widget.configure(background=panel, foreground=fg, activebackground=select, activeforeground="#ffffff")
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._theme_tk_descendants(child, bg, panel, field, fg, select)

    def _toggle_theme(self):
        next_theme = "light" if self.config_data.theme == "dark" else "dark"
        self._apply_theme(next_theme)
        self._set_status(f"Theme: {next_theme.title()}")

    def _build_ui(self):
        main = ttk.Panedwindow(self, orient="horizontal")
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main, padding=10)
        right = ttk.Frame(main, padding=(0, 10, 10, 10))
        main.add(left, weight=0)
        main.add(right, weight=1)

        left_header = ttk.Frame(left)
        left_header.pack(fill="x")
        ttk.Label(left_header, text="Projects", font=("TkDefaultFont", 12, "bold")).pack(side="left", anchor="w")
        self.theme_button = ttk.Button(left_header, text="Light Mode", command=self._toggle_theme)
        self.theme_button.pack(side="right")
        self.project_list = ttk.Treeview(
            left,
            columns=("project", "chat", "commands"),
            show="headings",
            selectmode="browse",
            height=9,
        )
        self.project_list.heading("project", text="Project")
        self.project_list.heading("chat", text="ChatGPT")
        self.project_list.heading("commands", text="Commands")
        self.project_list.column("project", width=180, minwidth=120, anchor="w", stretch=True)
        self.project_list.column("chat", width=132, minwidth=118, anchor="center", stretch=False)
        self.project_list.column("commands", width=132, minwidth=118, anchor="center", stretch=False)
        self.project_list.pack(fill="x", expand=False, pady=(8, 8))
        self.project_list.bind("<<TreeviewSelect>>", self._on_project_select)
        self.project_list.bind("<Button-3>", self._show_project_context)
        self.project_list.bind("<ButtonPress-1>", self._project_drag_start, add=True)
        self.project_list.bind("<Button-1>", self._acknowledge_clicked_project, add=True)
        self.project_list.bind("<B1-Motion>", self._project_drag_motion)
        self.project_list.bind("<ButtonRelease-1>", self._project_drag_end)
        self.project_list.bind("<Configure>", lambda _e: self.after_idle(self._layout_project_status_labels))
        self.project_list.bind("<MouseWheel>", lambda _e: self.after_idle(self._layout_project_status_labels), add=True)

        status = ttk.LabelFrame(left, text="Project status", padding=8)
        status.pack(fill="x", pady=(0, 6))
        self.snapshot_status_var = tk.StringVar(value="No snapshot")
        ttk.Label(status, textvariable=self.snapshot_status_var, anchor="w").pack(fill="x")
        self.last_extracted_var = tk.StringVar(value="Last extracted: none")
        ttk.Label(status, textvariable=self.last_extracted_var, anchor="w").pack(fill="x", pady=(2, 0))
        self.download_status_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.download_status_var, anchor="w").pack(fill="x", pady=(2, 0))
        self.chat_worked_for_var = tk.StringVar(value="ChatGPT worked for: —")
        ttk.Label(status, textvariable=self.chat_worked_for_var, anchor="w").pack(fill="x", pady=(2, 0))

        self.main_actions_scroll = ScrollableFrame(left)
        self.main_actions_scroll.pack(fill="both", expand=True, pady=(2, 0))
        self._build_main_actions(self.main_actions_scroll.inner)
        self.project_empty_menu = tk.Menu(self, tearoff=False)
        self.project_empty_menu.add_command(label="Add Project", command=self._add_project)
        self.project_menu = tk.Menu(self, tearoff=False)
        self.project_menu.add_command(label="Add Project", command=self._add_project)
        self.project_menu.add_separator()
        self.project_menu.add_command(label="Show in Explorer / File Manager", command=self._open_selected_folder)
        self.project_menu.add_command(label="Open with Visual Studio Code", command=self._open_selected_vscode)
        self.project_menu.add_command(label="Open Command Prompt Here", command=self._open_selected_cmd_terminal)
        self.project_menu.add_command(label="Open PowerShell Here", command=self._open_selected_powershell_terminal)
        self.project_menu.add_command(label="Extract Newest ZIP", command=lambda: self._run_main_action(self._extract_newest))
        self.project_menu.add_separator()
        self.project_menu.add_command(label="Copy Active Command Output", command=self._copy_active_project_command_output)
        self.project_menu.add_command(label="Copy Active Command Error Output", command=self._copy_active_project_command_error_output)
        self.project_menu.add_command(label="Focus All Commands", command=self._focus_project_commands)
        self.project_menu.add_command(label="Focus All ChatGPT URL Windows", command=self._focus_project_chatgpt_windows)
        self.project_menu.add_command(label="Close All URL Windows", command=self._close_project_url_windows)
        self.project_menu.add_command(label="Close All Commands", command=self._stop_project_commands)
        self.project_menu.add_separator()
        self.project_menu.add_command(label="Move Up", command=lambda: self._move_project(-1))
        self.project_menu.add_command(label="Move Down", command=lambda: self._move_project(1))
        self.project_menu.add_separator()
        self.project_menu.add_command(label="Remove Project", command=self._remove_project)
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)
        self.overview_tab = ScrollableFrame(self.notebook)
        self.commands_tab = ttk.Frame(self.notebook, padding=10)
        self.urls_tab = ScrollableFrame(self.notebook)
        self.prompts_tab = ttk.Frame(self.notebook, padding=10)
        self.notes_tab = ttk.Frame(self.notebook, padding=10)
        self.files_tab = ttk.Frame(self.notebook, padding=10)
        self.activity_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.overview_tab, text="Project")
        self.notebook.add(self.commands_tab, text="Commands")
        self.notebook.add(self.urls_tab, text="URLs")
        self.notebook.add(self.prompts_tab, text="Prompts")
        self.notebook.add(self.notes_tab, text="Notes")
        self.notebook.add(self.files_tab, text="Files")
        self.notebook.add(self.activity_tab, text="Activity")
        self.notebook.bind("<Button-1>", self._on_notebook_click_acknowledge, add=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed, add=True)

        self._build_overview()
        self._build_commands()
        self._build_urls()
        self._build_prompts()
        self._build_notes()
        self._build_files()
        self._build_activity()

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken", padding=(8, 4)).pack(fill="x")

    def _build_overview(self):
        parent = self.overview_tab.inner

        self._project_settings_visible = True
        self.project_settings_toggle = ttk.Button(parent, text="▼ Project Settings", command=self._toggle_project_settings)
        self.project_settings_toggle.pack(fill="x", padx=4, pady=(0, 4))

        settings = ttk.Frame(parent, padding=10)
        self.project_settings_frame = settings
        settings.columnconfigure(1, weight=1)

        self.name_var = tk.StringVar()
        self.match_var = tk.StringVar()
        self.downloads_var = tk.StringVar()
        self.extract_parent_var = tk.StringVar()
        self.repo_root_var = tk.StringVar()
        self.delete_old_var = tk.BooleanVar()
        self.auto_extract_var = tk.BooleanVar(value=False)
        self.auto_stop_commands_before_extract_var = tk.BooleanVar(value=False)
        self.preserve_extract_git_var = tk.BooleanVar()
        self.confirm_extract_var = tk.BooleanVar(value=True)
        self.preserve_replace_git_var = tk.BooleanVar()
        self.zip_exclude_git_var = tk.BooleanVar()

        self._path_row(settings, 0, "Project name", self.name_var, browse=False)
        self._path_row(settings, 1, "ZIP match string", self.match_var, browse=False)
        self._path_row(settings, 2, "ZIP source folder", self.downloads_var, browse=True)
        self._path_row(settings, 3, "Extract parent folder", self.extract_parent_var, browse=True)
        self._path_row(settings, 4, "GitHub / repo search root", self.repo_root_var, browse=True)

        ttk.Checkbutton(
            settings,
            text="Auto extract when ready",
            variable=self.auto_extract_var,
            command=self._on_auto_extract_setting_changed,
        ).grid(row=5, column=1, sticky="w", pady=3)
        ttk.Checkbutton(
            settings,
            text="Auto close all commands before extracting",
            variable=self.auto_stop_commands_before_extract_var,
        ).grid(row=6, column=1, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="Delete older matching ZIPs after successful extraction", variable=self.delete_old_var).grid(row=7, column=1, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="Show confirmation before extracting newest matching ZIP", variable=self.confirm_extract_var).grid(row=8, column=1, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="Preserve existing .git when re-extracting", variable=self.preserve_extract_git_var).grid(row=9, column=1, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="Preserve existing .git when replacing repo", variable=self.preserve_replace_git_var).grid(row=10, column=1, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="Exclude .git when creating ZIP", variable=self.zip_exclude_git_var).grid(row=11, column=1, sticky="w", pady=3)

        ttk.Label(settings, text="Build/output folders to exclude from snapshots, repo replacement, and ZIP creation").grid(row=12, column=0, columnspan=3, sticky="w", pady=(10, 4))
        self.exclusions_text = tk.Text(settings, height=8, wrap="none")
        self.exclusions_text.grid(row=13, column=0, columnspan=3, sticky="ew")
        ttk.Label(settings, text="One folder name per line. Matching is case-insensitive and applies at any depth.").grid(row=14, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(settings, text=f"Config: {config_path()}").grid(row=15, column=0, columnspan=3, sticky="e", pady=(8, 0))

        self._bind_project_settings_autosave()
        settings.pack(fill="x", padx=4, pady=(0, 10), after=self.project_settings_toggle)

    # def _path_row is supplied by ProjectSettingsController to keep layout.py within SRP/size limits.
    def _build_commands(self):
        top = ttk.Frame(self.commands_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(
            top,
            text="Commands can be shell commands or script paths. Auto-run commands use order 0–9; commands sharing an order run together. Each command can finish by exit code or by a configurable delay for long-running servers.",
            wraplength=760,
        ).pack(side="left", anchor="w", fill="x", expand=True)
        self.close_all_commands_btn = ttk.Button(
            top,
            text="Close All Running Commands",
            command=self._close_all_command_windows,
        )
        self.close_all_commands_btn.pack(side="right", padx=(10, 0))

        self._build_command_manage_toolbar(self.commands_tab)

        frame = ttk.Frame(self.commands_tab)
        frame.pack(fill="both", expand=True)
        self.commands_tree = ttk.Treeview(
            frame,
            columns=("label", "auto", "status", "command"),
            show="headings",
            selectmode="browse",
        )
        self.commands_tree.heading("label", text="Label")
        self.commands_tree.heading("auto", text="Auto order")
        self.commands_tree.heading("status", text="Window Status")
        self.commands_tree.heading("command", text="Command / Script")
        self.commands_tree.column("label", width=170, anchor="w")
        self.commands_tree.column("auto", width=120, anchor="center")
        self.commands_tree.column("status", width=170, anchor="center")
        self.commands_tree.column("command", width=500, anchor="w")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.commands_tree.yview)
        self.commands_tree.configure(yscrollcommand=yscroll.set)
        self.commands_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.commands_tree.bind("<Double-1>", lambda _e: self._edit_command())
        self.commands_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_command_action_buttons())

        self._build_command_runtime_toolbar(self.commands_tab)

    def _build_urls(self):
        parent = self.urls_tab.inner
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=4, pady=(4, 8))
        ttk.Label(top, text="Saved URL groups", style="Section.TLabel").pack(side="left")
        ttk.Button(top, text="Add Group", command=self._add_url_group).pack(side="right")
        ttk.Label(
            parent,
            text="Each URL shows one launch button for every supported browser detected on this PC. Each launch creates a managed new window; Focus and Close control the most recently opened window for that URL.",
            wraplength=1000,
        ).pack(anchor="w", padx=4, pady=(0, 8))
        self.url_groups_host = ttk.Frame(parent)
        self.url_groups_host.pack(fill="both", expand=True, padx=4)

        self.chat_debug_visible_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            parent,
            text="Show ChatGPT monitoring debug log",
            variable=self.chat_debug_visible_var,
            command=self._toggle_chat_debug_log_visibility,
        ).pack(anchor="w", padx=4, pady=(8, 2))
        debug = ttk.LabelFrame(parent, text="ChatGPT monitoring debug log", padding=8)
        self.chat_debug_frame = debug
        debug_toolbar = ttk.Frame(debug)
        debug_toolbar.pack(fill="x", pady=(0, 5))
        ttk.Button(debug_toolbar, text="Copy", command=self._copy_chat_debug_log).pack(side="right")
        ttk.Button(debug_toolbar, text="Clear", command=self._clear_chat_debug_log).pack(side="right", padx=(0, 6))
        self.chat_debug_text = tk.Text(debug, height=9, wrap="none", state="disabled")
        debug_scroll = ttk.Scrollbar(debug, orient="vertical", command=self.chat_debug_text.yview)
        self.chat_debug_text.configure(yscrollcommand=debug_scroll.set)
        self.chat_debug_text.pack(side="left", fill="both", expand=True)
        debug_scroll.pack(side="right", fill="y")

    def _toggle_chat_debug_log_visibility(self) -> None:
        if not hasattr(self, "chat_debug_frame"):
            return
        if self.chat_debug_visible_var.get():
            self.chat_debug_frame.pack(fill="x", padx=4, pady=(2, 4))
        else:
            self.chat_debug_frame.pack_forget()

    def _build_activity(self):
        ttk.Label(self.activity_tab, text="Activity log", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        frame = ttk.Frame(self.activity_tab)
        frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(frame, wrap="none", state="disabled")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Button(self.activity_tab, text="Clear Log", command=self._clear_log).pack(anchor="e", pady=(8, 0))

