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
from ...platform.windows.msaa import inspect_accessibility_items_msaa, invoke_accessibility_item_msaa, invoke_chatgpt_composer_msaa
from ...platform.windows.msaa.constants import ROLE_SYSTEM_STATICTEXT
from ...platform.windows.keyboard import paste_clipboard_with_ctrl_v
from ...platform.windows.clipboard import copy_file_to_windows_clipboard
from ..constants import *
from ..dialogs.command import CommandDialog
from ..dialogs.accessibility import AccessibilityItemsDialog
from ..dialogs.url import UrlDialog
from ..dialogs.misc import MultiSelectDialog, SingleSelectDialog, SnapshotDiffDialog
from ..widgets import ScrollableFrame

class ChatStatusController:
    def _poll_chat_foreground(self):
        if self._closing or not self.winfo_exists():
            return
        if os.name == "nt":
            hwnd = get_windows_foreground_hwnd()
            watches = {watch.hwnd: watch for watch in self._chat_watch_manager.watches()}
            watch = watches.get(hwnd)
            if watch and watch.state in {ChatState.SUCCESS, ChatState.ERROR}:
                now = time.monotonic()
                if self._foreground_watch_hwnd != hwnd:
                    self._foreground_watch_hwnd = hwnd
                    self._foreground_watch_since = now
                elif not watch.acknowledged and now - self._foreground_watch_since >= 1.0:
                    self._chat_watch_manager.acknowledge(hwnd)
                    self._update_project_status_cells()
            else:
                self._foreground_watch_hwnd = 0
                self._foreground_watch_since = 0.0
        try:
            self.after(400, self._poll_chat_foreground)
        except tk.TclError:
            pass

    def _on_chat_event(self, event: ChatEvent):
        self._chat_debug_event(event)
        if event.kind == "event_monitor_error":
            detail = f" — {event.watch.error_text}" if event.watch.error_text else ""
            self._log(f"ERROR: ChatGPT WinEvent accessibility hook is not active{detail}")
            return
        if event.kind == "event_monitor_ready":
            self._log("ChatGPT WinEvent accessibility hook is active.")
            return
        if event.kind == "event_callback_error":
            detail = event.watch.error_text or "unknown accessibility callback error"
            self._log(f"ERROR: ChatGPT accessibility event callback failed — {detail}")
            return
        if event.kind == "monitor_error":
            self._log("ChatGPT fallback accessibility scan could not read a browser window; WinEvent monitoring remains active.")
            return
        if event.kind == "accessibility_event":
            signal = event.watch.composer_name
            if signal in {"Stop answering", "Send prompt", "Start Voice"} or signal.lower().startswith("download"):
                self._log(f"ChatGPT accessibility event: {event.watch.label} — {signal} via WinEvent")
            self._update_project_status_cells()
            self._update_download_chatgpt_button()
            return
        if event.kind == "initialized":
            signal = event.watch.composer_name or event.watch.composer_state
            backend = event.watch.accessibility_backend.upper() if event.watch.accessibility_backend else "accessibility"
            self._log(f"ChatGPT accessibility ready: {event.watch.label} — {signal} via {backend}")
        elif event.kind == "closed":
            self._log(f"ChatGPT watcher closed: {event.watch.label}")
        elif event.kind == "state":
            if event.watch.state == ChatState.IN_PROGRESS:
                self._log(f"ChatGPT working: {event.watch.label} — {event.watch.composer_name or 'Stop answering'} via {event.watch.accessibility_backend.upper() or 'accessibility'}")
            elif event.watch.state == ChatState.SUCCESS:
                self._log(f"ChatGPT response complete: {event.watch.label} — {event.watch.composer_name or 'idle composer'} via {event.watch.accessibility_backend.upper() or 'accessibility'}")
                if os.name == "nt":
                    try:
                        import winsound
                        winsound.MessageBeep(winsound.MB_OK)
                    except Exception:
                        pass
            elif event.watch.state == ChatState.ERROR:
                detail = f" — {event.watch.error_text}" if event.watch.error_text else ""
                self._log(f"ChatGPT response error: {event.watch.label}{detail}")
                if os.name == "nt":
                    try:
                        import winsound
                        winsound.MessageBeep(winsound.MB_ICONHAND)
                    except Exception:
                        pass
        self._update_project_status_cells()
        self._update_download_chatgpt_button()

    def _update_download_chatgpt_button(self):
        if not hasattr(self, "download_chatgpt_btn"):
            return
        project = self._current_project()
        eligible = False
        if project and os.name == "nt" and self._worker_count == 0:
            # The user may open an existing ChatGPT conversation that already
            # contains downloadable artifacts. No new generation needs to have
            # occurred in this app session; the action performs a fresh
            # accessibility scan when clicked.
            eligible = any(
                windows_hwnd_exists(watch.hwnd)
                for watch in self._chat_watch_manager.watches_for_project(project.project_id)
            )
        self.download_chatgpt_btn.configure(state="normal" if eligible else "disabled")

    def _download_chatgpt_response_files(self, url_id: str | None = None):
        project = self._current_project()
        if not project or os.name != "nt":
            return
        watches = [
            watch
            for watch in self._chat_watch_manager.watches_for_project(project.project_id)
            if windows_hwnd_exists(watch.hwnd) and (url_id is None or watch.url_id == url_id)
        ]
        if not watches:
            messagebox.showinfo(
                "No open ChatGPT window",
                "No monitored ChatGPT window is currently open for this project/URL.",
                parent=self,
            )
            self._update_download_chatgpt_button()
            return

        def work():
            try:
                results: list[tuple[str, list[str]]] = []
                match_terms = (project.name, project.match_string)
                for watch in watches:
                    clicked = self._chat_watch_manager.download_latest(
                        watch.hwnd,
                        (*match_terms, watch.label),
                    )
                    results.append((watch.label, clicked))
                return results
            finally:
                self._ui_queue.put(("chat_download_finished", project.project_id, None))

        def done(results):
            total = sum(len(clicked) for _label, clicked in results)
            for label, clicked in results:
                if clicked:
                    self._log(f"ChatGPT downloads invoked for {label}: " + ", ".join(clicked))
                else:
                    self._log(f"No downloadable artifact links found in the most recent available ChatGPT response: {label}")
            if total:
                self._set_status(f"Started {total} ChatGPT response download(s) for {project.name}")
            else:
                self._set_status(f"No download links found in the most recent available ChatGPT response(s) for {project.name}")
                messagebox.showinfo(
                    "No download links found",
                    "No Download control was found in the newest available response of the managed ChatGPT accessibility document.",
                    parent=self,
                )

        self._downloading_project_ids.add(project.project_id)
        self._update_project_status_cells()
        self._run_worker("Downloading ChatGPT response files…", work, done)

    def _select_chatgpt_paste_target(self, action_title: str, item_kind: str):
        project = self._current_project()
        if project is None or os.name != "nt":
            return None
        candidates = []
        for watch in self._chat_watch_manager.watches_for_project(project.project_id):
            if not windows_hwnd_exists(watch.hwnd):
                continue
            run = next(
                (item for item in self._browser_runs.get(watch.url_id, []) if item.hwnd == watch.hwnd),
                None,
            )
            if run is not None:
                candidates.append((watch, run))
        if not candidates:
            messagebox.showinfo(
                "No open ChatGPT window",
                "No monitored ChatGPT window is currently open for this project.",
                parent=self,
            )
            return None
        foreground = get_windows_foreground_hwnd()
        selected = next((item for item in candidates if item[0].hwnd == foreground), None)
        if selected is None and len(candidates) == 1:
            selected = candidates[0]
        if selected is None:
            dialog = SingleSelectDialog(
                self,
                action_title,
                f"Choose the managed ChatGPT window that should receive the {item_kind}. Nothing will be sent automatically.",
                [f"{watch.label} — {run.browser_name} — {watch.url}" for watch, run in candidates],
            )
            if dialog.result is None:
                return None
            selected = candidates[dialog.result]
        return selected

    def _focus_chatgpt_composer_for_paste(self, run) -> str:
        """Focus a managed browser and tolerate Firefox accessibility refresh lag."""
        focus_windows_browser(run)
        for delay in (0.08, 0.18, 0.30):
            time.sleep(delay)
            focused = invoke_chatgpt_composer_msaa(
                run.hwnd, "Ask ChatGPT", role=ROLE_SYSTEM_STATICTEXT
            )
            if focused:
                return focused
        return ""

    def _paste_text_into_chatgpt(
        self,
        text: str,
        *,
        action_title: str,
        status_subject: str,
        debug_kind: str,
    ) -> None:
        if not text:
            return
        selected = self._select_chatgpt_paste_target(action_title, "text")
        if selected is None:
            return
        watch, run = selected
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

        def work():
            focused = self._focus_chatgpt_composer_for_paste(run)
            if not focused:
                return ""
            paste_clipboard_with_ctrl_v()
            return focused

        def done(focused_name: str):
            if focused_name:
                self._chat_debug(
                    f"pasted {debug_kind} label={watch.label!r} hwnd={watch.hwnd} "
                    f"subject={status_subject!r} chars={len(text)} composer={focused_name!r}"
                )
                self._set_status(
                    f"Pasted {status_subject} into {watch.label} ({len(text):,} characters); not sent"
                )
            else:
                self._chat_debug(
                    f"paste {debug_kind} label={watch.label!r} hwnd={watch.hwnd}: "
                    "Ask ChatGPT / Chat with ChatGPT composer not found/invokable"
                )
                messagebox.showinfo(
                    "ChatGPT composer not found",
                    'Could not invoke the ChatGPT composer accessibility item ("Ask ChatGPT" or "Chat with ChatGPT") in the selected managed ChatGPT window.',
                    parent=self,
                )

        self._run_worker(f"{action_title}…", work, done)

    def _paste_file_into_chatgpt(self, path: Path, *, action_title: str, item_kind: str) -> None:
        if os.name != "nt" or not path.is_file():
            if not path.is_file():
                messagebox.showinfo("File not found", f"The file no longer exists:\n{path}", parent=self)
            return
        selected = self._select_chatgpt_paste_target(action_title, item_kind)
        if selected is None:
            return
        watch, run = selected

        def work():
            copy_file_to_windows_clipboard(path)
            focused = self._focus_chatgpt_composer_for_paste(run)
            if not focused:
                return ""
            paste_clipboard_with_ctrl_v()
            return focused

        def done(focused_name: str):
            if focused_name:
                self._chat_debug(
                    f"pasted file attachment label={watch.label!r} hwnd={watch.hwnd} "
                    f"file={str(path)!r} composer={focused_name!r}"
                )
                self._set_status(f"Pasted {path.name} into {watch.label}; attachment not sent")
            else:
                self._set_status(f"Could not paste {path.name}; file remains at {path}")
                messagebox.showinfo(
                    "ChatGPT composer not found",
                    'Could not invoke the ChatGPT composer accessibility item ("Ask ChatGPT" or "Chat with ChatGPT") '
                    f'in the selected managed ChatGPT window.\n\nThe file was kept at:\n{path}',
                    parent=self,
                )

        self._run_worker(f"{action_title}…", work, done)

    def _paste_zip_project_into_chatgpt(self) -> None:
        project = self._current_project()
        if project is None:
            return
        zip_path = Path(project.last_created_zip).expanduser() if project.last_created_zip else None
        if zip_path is None or not zip_path.is_file():
            messagebox.showinfo(
                "No project ZIP ready",
                "Click ZIP Project first. The newest ZIP created by Project Repo Manager will then be available for this action.",
                parent=self,
            )
            return
        self._paste_file_into_chatgpt(
            zip_path,
            action_title="Paste ZIP Project into ChatGPT",
            item_kind="ZIP attachment",
        )

    def _paste_error_output_into_chatgpt(self) -> None:
        result = self._active_project_command_error_output()
        if result is None:
            return
        command, error_output = result
        self._paste_text_into_chatgpt(
            error_output,
            action_title="Paste Error Output into ChatGPT",
            status_subject=f"error output from {command.label}",
            debug_kind="error output",
        )

    def _paste_output_into_chatgpt(self) -> None:
        result = self._active_project_command_output()
        if result is None:
            return
        command, output = result
        self._paste_text_into_chatgpt(
            output,
            action_title="Paste Output into ChatGPT",
            status_subject=f"output from {command.label}",
            debug_kind="command output",
        )

    @staticmethod
    def _chat_state_label(state: ChatState) -> str:
        return {
            ChatState.IDLE: "Idle",
            ChatState.IN_PROGRESS: "Running",
            ChatState.SUCCESS: "Finished",
            ChatState.ERROR: "Error",
            ChatState.UNAVAILABLE: "Error",
        }.get(state, "Error")

    def _chat_debug(self, message: str) -> None:
        if not hasattr(self, "chat_debug_text"):
            return
        stamp = time.strftime("%H:%M:%S")
        try:
            self.chat_debug_text.configure(state="normal")
            self.chat_debug_text.insert("end", f"[{stamp}] {message}\n")
            self._chat_debug_lines += 1
            if self._chat_debug_lines > 2500:
                self.chat_debug_text.delete("1.0", "501.0")
                self._chat_debug_lines = max(0, self._chat_debug_lines - 500)
            self.chat_debug_text.see("end")
            self.chat_debug_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _chat_debug_event(self, event: ChatEvent) -> None:
        watch = event.watch
        self._chat_debug(
            f"event={event.kind} label={watch.label!r} hwnd={watch.hwnd} "
            f"state={self._chat_state_label(watch.state)} previous={self._chat_state_label(event.previous_state)} "
            f"backend={watch.accessibility_backend or '-'} composer={watch.composer_state}/{watch.composer_name or '-'} "
            f"doc={watch.document_key or '-'} active_doc={watch.active_response_document_key or '-'} "
            f"last_doc={watch.last_response_document_key or '-'} response_len={watch.response_text_length} "
            f"downloads={watch.download_count} worked_for={watch.worked_for_text or '-'} error={watch.error_text or '-'}"
        )

    def _clear_chat_debug_log(self) -> None:
        if not hasattr(self, "chat_debug_text"):
            return
        self.chat_debug_text.configure(state="normal")
        self.chat_debug_text.delete("1.0", "end")
        self.chat_debug_text.configure(state="disabled")
        self._chat_debug_lines = 0

    def _copy_chat_debug_log(self) -> None:
        if not hasattr(self, "chat_debug_text"):
            return
        text = self.chat_debug_text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Copied ChatGPT monitoring debug log")

    def _detect_chatgpt_state(self, url_id: str) -> None:
        run = self._latest_browser_run(url_id)
        if os.name != "nt" or run is None or not windows_hwnd_exists(run.hwnd):
            self._chat_detected_state_by_url[url_id] = "Error"
            self._refresh_urls()
            self._chat_debug(f"manual detect url_id={url_id}: Error — no attached browser window")
            return

        def work():
            return self._chat_watch_manager.detect_current(run.hwnd)

        def done(result):
            state, snapshot, watch = result
            label = self._chat_state_label(state)
            self._chat_detected_state_by_url[url_id] = label
            self._refresh_urls()
            if snapshot is None:
                detail = "snapshot unavailable"
            else:
                detail = (
                    f"available={snapshot.available} backend={snapshot.backend or '-'} "
                    f"composer={snapshot.composer_state}/{snapshot.composer_name or '-'} "
                    f"assistant_response={snapshot.has_assistant_response} response_len={snapshot.response_text_length} "
                    f"downloads={snapshot.download_count} worked_for={snapshot.worked_for_text or '-'} "
                    f"structural_complete={snapshot.latest_response_complete} doc={snapshot.document_key or '-'} "
                    f"error={snapshot.error_text or '-'}"
                )
            watcher_state = self._chat_state_label(watch.state) if watch is not None else "not registered"
            self._chat_debug(
                f"manual detect label={run.label!r} hwnd={run.hwnd}: fresh={label} watcher={watcher_state}; {detail}"
            )
            self._set_status(f"ChatGPT state for {run.label}: {label}")

        self._run_worker(f"Detecting ChatGPT state for {run.label}…", work, done)

    def _open_chatgpt_accessibility_items(self, url_id: str) -> None:
        run = self._latest_browser_run(url_id)
        if os.name != "nt" or run is None or not windows_hwnd_exists(run.hwnd):
            messagebox.showinfo("Accessibility Items", "No attached browser window is open for this URL.", parent=self)
            return

        def work():
            return inspect_accessibility_items_msaa(run.hwnd)

        def done(snapshot):
            if snapshot is None:
                self._chat_debug(f"accessibility explorer label={run.label!r} hwnd={run.hwnd}: no MSAA tree")
                messagebox.showinfo("Accessibility Items", "No accessibility tree could be read from this browser window.", parent=self)
                return
            self._chat_debug(
                f"accessibility explorer label={run.label!r} hwnd={run.hwnd}: "
                f"source={snapshot.source_hwnd} object={snapshot.object_id} items={len(snapshot.elements)} "
                f"composer={snapshot.composer_state}/{snapshot.composer_name or '-'} doc={snapshot.document_key or '-'}"
            )
            AccessibilityItemsDialog(
                self,
                label=run.label,
                top_hwnd=run.hwnd,
                snapshot=snapshot,
                on_click=lambda index, name: self._click_accessibility_item(snapshot, index, name),
            )

        self._run_worker(f"Reading accessibility items for {run.label}…", work, done)

    def _click_accessibility_item(self, snapshot, index: int, name: str) -> None:
        def work():
            return invoke_accessibility_item_msaa(
                snapshot.source_hwnd, snapshot.object_id, index, expected_name=name
            )

        def done(clicked_name: str):
            display = clicked_name or name or "(unnamed)"
            if clicked_name or (not name and display == "(unnamed)"):
                self._chat_debug(f"accessibility click index={index} name={display!r}: invoked")
                self._set_status(f"Clicked accessibility item #{index}: {display}")
            else:
                self._chat_debug(f"accessibility click index={index} name={display!r}: no action invoked")
                self._set_status(f"Accessibility item #{index} could not be invoked")

        self._run_worker(f"Clicking accessibility item #{index}…", work, done)
