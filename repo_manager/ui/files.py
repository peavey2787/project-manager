from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..domain import CommandSpec
from .widgets import style_black_combobox_popup

from ..file_server import (
    build_folder_server_command,
    FileServerSession,
    generate_self_signed_certificate,
    generated_certificate_paths,
    local_lan_address,
)

_MAX_EDITOR_BYTES = 4 * 1024 * 1024


class FilesController:
    def _build_files(self) -> None:
        self._file_server_session = FileServerSession(self._thread_log)
        self._file_tree_paths: dict[str, Path] = {}
        self._file_tree_counter = 0
        self._files_selected_folder: Path | None = None
        self._editor_path: Path | None = None
        self._editor_encoding = "utf-8"
        self._files_loaded_project_id: str | None = None
        self._files_split_initialized = False

        parent = self.files_tab
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        server = ttk.LabelFrame(parent, text="Folder server", padding=8)
        server.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        server.columnconfigure(1, weight=1)
        server.columnconfigure(3, weight=1)

        self.file_server_folder_var = tk.StringVar(value="Select a folder in the tree to serve")
        self.file_server_protocol_var = tk.StringVar(value="HTTP")
        self.file_server_port_var = tk.StringVar(value="8000")
        self.file_server_cert_mode_var = tk.StringVar(value="Generated certificate")
        self.file_server_cert_var = tk.StringVar()
        self.file_server_key_var = tk.StringVar()
        self.file_server_status_var = tk.StringVar(value="Stopped")

        ttk.Label(server, text="Selected folder:").grid(row=0, column=0, sticky="w")
        ttk.Label(server, textvariable=self.file_server_folder_var).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=(6, 0)
        )
        ttk.Label(server, text="Protocol:").grid(row=1, column=0, sticky="w", pady=(7, 0))
        self.file_server_protocol_box = ttk.Combobox(
            server,
            textvariable=self.file_server_protocol_var,
            values=("HTTP", "HTTPS"),
            state="readonly",
            width=10,
            style="Protocol.TCombobox",
            postcommand=self._style_file_server_protocol_dropdown,
        )
        self.file_server_protocol_box.grid(row=1, column=1, sticky="w", padx=(6, 12), pady=(7, 0))
        self.file_server_protocol_box.bind("<<ComboboxSelected>>", self._on_file_server_option_changed, add=True)
        ttk.Label(server, text="Port:").grid(row=1, column=2, sticky="e", pady=(7, 0))
        self.file_server_port_entry = ttk.Entry(server, textvariable=self.file_server_port_var, width=8)
        self.file_server_port_entry.grid(row=1, column=3, sticky="w", padx=(6, 0), pady=(7, 0))
        self.file_server_port_entry.bind("<FocusOut>", self._persist_file_server_settings, add=True)

        ttk.Label(server, text="HTTPS certificate:").grid(row=2, column=0, sticky="w", pady=(7, 0))
        self.file_server_cert_mode_box = ttk.Combobox(
            server,
            textvariable=self.file_server_cert_mode_var,
            values=("Generated certificate", "Custom certificate"),
            state="readonly",
            width=22,
            style="Certificate.TCombobox",
            postcommand=lambda: style_black_combobox_popup(self.file_server_cert_mode_box),
        )
        self.file_server_cert_mode_box.grid(row=2, column=1, sticky="w", padx=(6, 12), pady=(7, 0))
        self.file_server_cert_mode_box.bind("<<ComboboxSelected>>", self._on_file_server_option_changed, add=True)
        self.generate_cert_btn = ttk.Button(server, text="Generate / Regenerate", command=self._generate_file_server_certificate)
        self.generate_cert_btn.grid(row=2, column=2, columnspan=2, sticky="w", pady=(7, 0))

        ttk.Label(server, text="Custom cert PEM:").grid(row=3, column=0, sticky="w", pady=(7, 0))
        self.file_server_cert_entry = ttk.Entry(server, textvariable=self.file_server_cert_var)
        self.file_server_cert_entry.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(6, 6), pady=(7, 0))
        self.file_server_cert_browse = ttk.Button(server, text="Browse…", command=self._browse_file_server_cert)
        self.file_server_cert_browse.grid(row=3, column=3, sticky="w", pady=(7, 0))

        ttk.Label(server, text="Private key PEM (optional if combined):").grid(row=4, column=0, sticky="w", pady=(7, 0))
        self.file_server_key_entry = ttk.Entry(server, textvariable=self.file_server_key_var)
        self.file_server_key_entry.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(6, 6), pady=(7, 0))
        self.file_server_key_browse = ttk.Button(server, text="Browse…", command=self._browse_file_server_key)
        self.file_server_key_browse.grid(row=4, column=3, sticky="w", pady=(7, 0))

        controls = ttk.Frame(server)
        controls.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(9, 0))
        self.file_server_toggle_btn = ttk.Button(controls, text="Start Server", command=self._toggle_file_server)
        self.file_server_toggle_btn.grid(row=0, column=0, sticky="w")
        self.file_server_add_command_btn = ttk.Button(
            controls,
            text="Add Server Command",
            command=self._add_file_server_command,
        )
        self.file_server_add_command_btn.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(controls, textvariable=self.file_server_status_var).grid(row=0, column=2, sticky="w", padx=(10, 0))
        controls.columnconfigure(2, weight=1)

        panes = ttk.Panedwindow(parent, orient="horizontal")
        self.files_panes = panes
        panes.grid(row=1, column=0, sticky="nsew")
        tree_frame = ttk.Frame(panes, padding=(0, 0, 6, 0))
        editor_frame = ttk.Frame(panes, padding=(6, 0, 0, 0))
        panes.add(tree_frame, weight=1)
        panes.add(editor_frame, weight=1)
        self._build_file_tree(tree_frame)
        self._build_file_editor(editor_frame)
        self.after_idle(self._set_files_default_split)
        self._update_file_server_controls()


    def _set_files_default_split(self) -> None:
        if self._files_split_initialized or not hasattr(self, "files_panes"):
            return
        width = self.files_panes.winfo_width()
        if width < 120:
            self.after(50, self._set_files_default_split)
            return
        try:
            self.files_panes.sashpos(0, width // 2)
            self._files_split_initialized = True
        except tk.TclError:
            pass

    def _build_file_tree(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(0, 6))
        ttk.Label(header, text="Project files", style="Section.TLabel").pack(side="left")
        ttk.Button(header, text="Refresh", command=self._refresh_file_tree).pack(side="right")
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        self.files_tree = ttk.Treeview(frame, show="tree", selectmode="browse")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.files_tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.files_tree.xview)
        self.files_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.files_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.files_tree.bind("<<TreeviewOpen>>", self._on_file_tree_open, add=True)
        self.files_tree.bind("<<TreeviewSelect>>", self._on_file_tree_select, add=True)
        self.files_tree.bind("<Button-3>", self._show_files_context, add=True)

    def _build_file_editor(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(0, 6))
        self.file_editor_path_var = tk.StringVar(value="Select a text file to edit")
        ttk.Label(header, textvariable=self.file_editor_path_var, style="Section.TLabel").pack(side="left", fill="x", expand=True)
        self.file_editor_reload_btn = ttk.Button(header, text="Reload", command=self._reload_editor_file, state="disabled")
        self.file_editor_reload_btn.pack(side="right")
        self.file_editor_save_btn = ttk.Button(header, text="Save", command=self._save_editor_file, state="disabled")
        self.file_editor_save_btn.pack(side="right", padx=(0, 6))
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        self.file_editor_text = tk.Text(frame, wrap="none", undo=True)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.file_editor_text.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.file_editor_text.xview)
        self.file_editor_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set, state="disabled")
        self.file_editor_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    def _load_files_project(self, project) -> None:
        if not hasattr(self, "files_tree"):
            return
        next_project_id = project.project_id if project is not None else None
        if (
            self._files_loaded_project_id is not None
            and next_project_id != self._files_loaded_project_id
            and self._file_server_session.running
        ):
            self._file_server_session.stop()
            self.file_server_status_var.set("Stopped (project changed)")
        self._files_loaded_project_id = next_project_id
        if project is None:
            self.file_server_protocol_var.set("HTTP")
            self.file_server_port_var.set("8000")
            self.file_server_cert_mode_var.set("Generated certificate")
            self.file_server_cert_var.set("")
            self.file_server_key_var.set("")
        else:
            self.file_server_protocol_var.set(str(project.file_server_protocol or "http").upper())
            self.file_server_port_var.set(str(project.file_server_port or 8000))
            mode = "Custom certificate" if project.file_server_cert_mode == "custom" else "Generated certificate"
            self.file_server_cert_mode_var.set(mode)
            self.file_server_cert_var.set(project.file_server_cert_path)
            self.file_server_key_var.set(project.file_server_key_path)
        self._update_file_server_controls()
        self._refresh_file_tree()

    def _refresh_file_tree(self) -> None:
        if not hasattr(self, "files_tree"):
            return
        self._clear_file_editor()
        self.files_tree.delete(*self.files_tree.get_children(""))
        self._file_tree_paths.clear()
        self._files_selected_folder = None
        self.file_server_folder_var.set("Select a folder in the tree to serve")
        project = self._current_project()
        if project is None:
            return
        root = project.working_dir.resolve()
        if not root.is_dir():
            self.file_editor_path_var.set(f"Project folder does not exist: {root}")
            return
        item = self._insert_file_node("", root, root.name or str(root), is_dir=True)
        self.files_tree.item(item, open=True)
        self._populate_file_node(item)
        self.files_tree.selection_set(item)
        self.files_tree.focus(item)
        self._files_selected_folder = root
        self.file_server_folder_var.set(str(root))

    def _insert_file_node(self, parent: str, path: Path, text: str, *, is_dir: bool) -> str:
        self._file_tree_counter += 1
        item = f"file-{self._file_tree_counter}"
        self._file_tree_paths[item] = path
        self.files_tree.insert(parent, "end", iid=item, text=text, values=("dir" if is_dir else "file",))
        if is_dir:
            self.files_tree.insert(item, "end", iid=f"{item}-loading", text="…")
        return item

    def _populate_file_node(self, item: str) -> None:
        path = self._file_tree_paths.get(item)
        if path is None or not path.is_dir():
            return
        for child in self.files_tree.get_children(item):
            if child not in self._file_tree_paths:
                self.files_tree.delete(child)
        if any(child in self._file_tree_paths for child in self.files_tree.get_children(item)):
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
        except OSError as exc:
            self._set_status(f"Could not read folder: {exc}")
            return
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            self._insert_file_node(item, entry, entry.name, is_dir=is_dir)

    def _on_file_tree_open(self, _event=None) -> None:
        item = self.files_tree.focus()
        if item:
            self._populate_file_node(item)

    def _on_file_tree_select(self, _event=None) -> None:
        selected = self.files_tree.selection()
        if not selected:
            return
        path = self._file_tree_paths.get(selected[0])
        if path is None:
            return
        if path.is_dir():
            self._files_selected_folder = path
            self.file_server_folder_var.set(str(path))
            return
        self._load_editor_file(path)

    def _clear_file_editor(self) -> None:
        self._editor_path = None
        self.file_editor_path_var.set("Select a text file to edit")
        self.file_editor_text.configure(state="normal")
        self.file_editor_text.delete("1.0", "end")
        self.file_editor_text.configure(state="disabled")
        self.file_editor_save_btn.configure(state="disabled")
        self.file_editor_reload_btn.configure(state="disabled")

    def _load_editor_file(self, path: Path) -> None:
        try:
            size = path.stat().st_size
            if size > _MAX_EDITOR_BYTES:
                raise ValueError(f"File is {size:,} bytes; the simple editor limit is {_MAX_EDITOR_BYTES:,} bytes.")
            raw = path.read_bytes()
            if b"\0" in raw:
                raise ValueError("This appears to be a binary file and cannot be opened in the text editor.")
            if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                encoding = "utf-16"
            else:
                encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
            text = raw.decode(encoding)
        except (OSError, UnicodeError, ValueError) as exc:
            self._editor_path = None
            self.file_editor_path_var.set(f"Cannot edit {path.name}: {exc}")
            self.file_editor_text.configure(state="normal")
            self.file_editor_text.delete("1.0", "end")
            self.file_editor_text.configure(state="disabled")
            self.file_editor_save_btn.configure(state="disabled")
            self.file_editor_reload_btn.configure(state="disabled")
            return
        self._editor_path = path
        self._editor_encoding = encoding
        self.file_editor_path_var.set(str(path))
        self.file_editor_text.configure(state="normal")
        self.file_editor_text.delete("1.0", "end")
        self.file_editor_text.insert("1.0", text)
        self.file_editor_text.edit_modified(False)
        self.file_editor_save_btn.configure(state="normal")
        self.file_editor_reload_btn.configure(state="normal")

    def _reload_editor_file(self) -> None:
        if self._editor_path is not None:
            self._load_editor_file(self._editor_path)

    def _save_editor_file(self) -> None:
        path = self._editor_path
        if path is None:
            return
        try:
            text = self.file_editor_text.get("1.0", "end-1c")
            path.write_text(text, encoding=self._editor_encoding, newline="")
            self.file_editor_text.edit_modified(False)
            self._set_status(f"Saved {path.name}")
        except Exception as exc:
            self._show_error("Save file failed", exc)

    def _persist_file_server_settings(self, _event=None) -> None:
        project = self._current_project()
        if project is None or self._loading_form:
            return
        project.file_server_protocol = self.file_server_protocol_var.get().strip().casefold() or "http"
        try:
            project.file_server_port = max(1, min(65535, int(self.file_server_port_var.get().strip())))
        except ValueError:
            project.file_server_port = 8000
        project.file_server_cert_mode = "custom" if self.file_server_cert_mode_var.get().startswith("Custom") else "generated"
        project.file_server_cert_path = self.file_server_cert_var.get().strip()
        project.file_server_key_path = self.file_server_key_var.get().strip()
        self._save_config()

    def _on_file_server_option_changed(self, _event=None) -> None:
        self._persist_file_server_settings()
        self._update_file_server_controls()

    def _update_file_server_controls(self) -> None:
        if not hasattr(self, "file_server_toggle_btn"):
            return
        running = self._file_server_session.running
        https = self.file_server_protocol_var.get().strip().upper() == "HTTPS"
        custom = self.file_server_cert_mode_var.get().startswith("Custom")
        normal = "normal" if https and not running else "disabled"
        custom_state = "normal" if https and custom and not running else "disabled"
        self.file_server_cert_mode_box.configure(state="readonly" if https and not running else "disabled")
        self.generate_cert_btn.configure(state=normal if not custom else "disabled")
        self.file_server_cert_entry.configure(state=custom_state)
        self.file_server_key_entry.configure(state=custom_state)
        self.file_server_cert_browse.configure(state=custom_state)
        self.file_server_key_browse.configure(state=custom_state)
        self.file_server_protocol_box.configure(state="disabled" if running else "readonly")
        self.file_server_port_entry.configure(state="disabled" if running else "normal")
        self.file_server_toggle_btn.configure(text="Stop Server" if running else "Start Server")


    def _style_file_server_protocol_dropdown(self) -> None:
        style_black_combobox_popup(self.file_server_protocol_box)

    def _resolved_file_server_tls_paths(self, *, create_generated: bool) -> tuple[Path | None, Path | None]:
        https = self.file_server_protocol_var.get().strip().upper() == "HTTPS"
        if not https:
            return None, None
        custom = self.file_server_cert_mode_var.get().startswith("Custom")
        if custom:
            cert_value = self.file_server_cert_var.get().strip()
            key_value = self.file_server_key_var.get().strip()
            if not cert_value:
                raise ValueError("Select a custom certificate PEM file.")
            return Path(cert_value), Path(key_value) if key_value else None
        paths = generated_certificate_paths()
        if create_generated and (not paths.cert_path.is_file() or not paths.key_path.is_file()):
            paths = generate_self_signed_certificate(local_lan_address())
        if not paths.cert_path.is_file() or not paths.key_path.is_file():
            raise FileNotFoundError("Generate the HTTPS certificate before adding this server command.")
        return paths.cert_path, paths.key_path

    def _add_file_server_command(self) -> None:
        project = self._current_project()
        folder = self._files_selected_folder
        if project is None or folder is None or not folder.is_dir():
            messagebox.showinfo(
                "Select a folder",
                "Select the folder you want to serve in the Files tree first.",
                parent=self,
            )
            return
        try:
            port = int(self.file_server_port_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError("Server port must be from 1 to 65535.")
            https = self.file_server_protocol_var.get().strip().upper() == "HTTPS"
            cert_path, key_path = self._resolved_file_server_tls_paths(create_generated=True)
            command = build_folder_server_command(
                folder,
                port=port,
                https=https,
                cert_path=cert_path,
                key_path=key_path,
            )
        except Exception as exc:
            self._show_error("Cannot add server command", exc)
            return
        spec = CommandSpec(
            label=f"Serve {folder.name or 'folder'} ({'HTTPS' if https else 'HTTP'})",
            command=command,
            auto_run=False,
        )
        project.commands.append(spec)
        self._persist_file_server_settings()
        self._save_config()
        self._refresh_commands(preferred_id=spec.command_id)
        if hasattr(self, "notebook") and hasattr(self, "commands_tab"):
            self.notebook.select(self.commands_tab)
        self._set_status(f"Added folder-server command: {spec.label}")

    def _browse_file_server_cert(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select TLS certificate",
            filetypes=[("PEM/certificate", "*.pem *.crt *.cer"), ("All files", "*.*")],
        )
        if path:
            self.file_server_cert_var.set(path)
            self._persist_file_server_settings()

    def _browse_file_server_key(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select TLS private key",
            filetypes=[("PEM/private key", "*.pem *.key"), ("All files", "*.*")],
        )
        if path:
            self.file_server_key_var.set(path)
            self._persist_file_server_settings()

    def _generate_file_server_certificate(self) -> None:
        lan_ip = local_lan_address()

        def work():
            return generate_self_signed_certificate(lan_ip)

        def done(paths):
            self.file_server_status_var.set(f"Generated certificate: {paths.cert_path}")
            self._set_status("Generated self-signed HTTPS certificate")

        self._run_worker("Generating HTTPS certificate…", work, done)

    def _toggle_file_server(self) -> None:
        if self._file_server_session.running:
            self._file_server_session.stop()
            self.file_server_status_var.set("Stopped")
            self._update_file_server_controls()
            self._set_status("File server stopped")
            return
        folder = self._files_selected_folder
        if folder is None or not folder.is_dir():
            messagebox.showinfo("Select a folder", "Select the folder you want to serve in the Files tree first.", parent=self)
            return
        try:
            port = int(self.file_server_port_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid port", "Server port must be a number from 1 to 65535.", parent=self)
            return
        if not 1 <= port <= 65535:
            messagebox.showerror("Invalid port", "Server port must be from 1 to 65535.", parent=self)
            return
        https = self.file_server_protocol_var.get().strip().upper() == "HTTPS"
        self._persist_file_server_settings()

        def work():
            cert_path, key_path = self._resolved_file_server_tls_paths(create_generated=True)
            return self._file_server_session.start(
                folder,
                port=port,
                https=https,
                cert_path=cert_path,
                key_path=key_path,
            )

        def done(endpoint):
            self.file_server_status_var.set(f"Serving {endpoint.root} at {endpoint.lan_url}")
            self._update_file_server_controls()
            self._set_status(f"File server started: {endpoint.lan_url}")

        self._run_worker("Starting file server…", work, done)
