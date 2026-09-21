from pathlib import Path
from types import SimpleNamespace

from repo_manager.chat.models import ChatState
from repo_manager.chat.state_machine import classify_detected_state


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_auto_extract_setting_is_under_project_settings_not_main_actions() -> None:
    layout = _source("repo_manager/ui/controllers/layout.py")
    actions = _source("repo_manager/ui/main_actions.py")
    assert 'text="Auto extract when ready"' in layout
    assert 'text="Auto extract when ready"' not in actions

def test_urls_include_stacked_chatgpt_diagnostics_and_debug_log() -> None:
    urls = _source("repo_manager/ui/controllers/urls.py")
    layout = _source("repo_manager/ui/controllers/layout.py")
    actions = _source("repo_manager/ui/main_actions.py")
    assert '"Discover URL"' in urls
    assert '"Focus"' in urls
    assert '"Close"' in urls
    assert '"Detect Current ChatGPT State"' not in urls
    assert '"Download ChatGPT Files"' in urls
    assert '"Accessibility Items…"' in urls
    assert 'button.pack(fill="x"' in urls
    assert 'text="Show ChatGPT monitoring debug log"' in layout
    assert 'self.chat_debug_visible_var = tk.BooleanVar(value=False)' in layout
    assert 'self.chat_debug_frame = debug' in layout
    assert "self.chat_debug_text" in layout
    assert 'Paste Error Output into ChatGPT' in actions
    assert 'value="ChatGPT worked for: —"' in layout


def test_url_download_button_reuses_main_download_method() -> None:
    urls = _source("repo_manager/ui/controllers/urls.py")
    chat_status = _source("repo_manager/ui/controllers/chat_status.py")
    assert "self._download_chatgpt_response_files(uid)" in urls
    assert chat_status.count("def _download_chatgpt_response_files") == 1
    assert "url_id: str | None = None" in chat_status


def test_accessibility_explorer_supports_list_inspect_and_click() -> None:
    dialog = _source("repo_manager/ui/dialogs/accessibility.py")
    adapter = _source("repo_manager/platform/windows/msaa/accessibility_items.py")
    assert "Inspect Accessibility Item" in dialog
    assert "Click Accessibility Item" in dialog
    assert "Document key:" in dialog
    assert 'text="Search:"' in dialog
    assert "self.search_var.trace_add" in dialog
    assert "_apply_search_filter" in dialog
    assert "0x{numeric_role:02x}" in dialog
    assert "inspect_accessibility_items_msaa" in adapter
    assert "invoke_accessibility_item_msaa" in adapter
    assert "capture_all_actions=True" in adapter
    assert "include_unnamed=True" in adapter


def test_manual_state_classifier_returns_only_requested_four_states() -> None:
    idle = SimpleNamespace(available=True, error_text="", in_progress=False, composer_state="send", has_assistant_response=False, latest_response_complete=False)
    running = SimpleNamespace(available=True, error_text="", in_progress=True, composer_state="stop", has_assistant_response=True, latest_response_complete=False)
    finished = SimpleNamespace(available=True, error_text="", in_progress=False, composer_state="send", has_assistant_response=True, latest_response_complete=True)
    error = SimpleNamespace(available=True, error_text="Something went wrong", in_progress=False, composer_state="send", has_assistant_response=True, latest_response_complete=False)
    unavailable = SimpleNamespace(available=False, error_text="", in_progress=False, composer_state="unknown", has_assistant_response=False, latest_response_complete=False)

    assert classify_detected_state(idle) is ChatState.IDLE
    assert classify_detected_state(running) is ChatState.IN_PROGRESS
    assert classify_detected_state(finished) is ChatState.SUCCESS
    assert classify_detected_state(error) is ChatState.ERROR
    assert classify_detected_state(unavailable) is ChatState.ERROR
    assert {classify_detected_state(x) for x in [idle, running, finished, error, unavailable]} <= {
        ChatState.IDLE, ChatState.IN_PROGRESS, ChatState.SUCCESS, ChatState.ERROR
    }


def test_msaa_runtime_dependencies_are_explicitly_available() -> None:
    # These names are used only when the user clicks the Windows diagnostics /
    # download fallbacks, so compile-only tests are not enough to catch them.
    from repo_manager.platform.windows.msaa import events, tree
    from repo_manager.platform.windows.msaa.models import MsaaElement

    assert events._InvokeDownloadsCommand is not None
    assert events._ProbeRetainedCommand is not None
    assert events._ActionTarget is not None

    # Exercise the exact tree classifier path that previously crashed with
    # ``name 're' is not defined`` when Accessibility Items was opened.
    composer, composer_name, error_text, downloads = tree._classify_elements(
        [
            MsaaElement(0, "Start Voice", 0, ""),
            MsaaElement(1, "There was an error", 0, ""),
            MsaaElement(2, "Download artifact", 0, "Press"),
        ]
    )
    assert (composer, composer_name) == ("voice", "Start Voice")
    assert error_text == "There was an error"
    assert downloads == 1


def test_event_monitor_can_construct_internal_commands() -> None:
    from repo_manager.platform.windows.msaa.events import MsaaWinEventMonitor
    from repo_manager.platform.windows.msaa.models import _InvokeDownloadsCommand, _ProbeRetainedCommand

    monitor = MsaaWinEventMonitor(lambda _event: None)
    assert monitor._commands is not None
    assert _InvokeDownloadsCommand(hwnd=1, match_terms=(), expected_document_key="", done=__import__("threading").Event(), clicked=[]).hwnd == 1
    assert _ProbeRetainedCommand(hwnd=1, done=__import__("threading").Event()).hwnd == 1


def test_paste_error_output_uses_exact_ask_chatgpt_accessible_and_does_not_send() -> None:
    chat_status = _source("repo_manager/ui/controllers/chat_status.py")
    keyboard = _source("repo_manager/platform/windows/keyboard.py")
    constants = _source("repo_manager/platform/windows/msaa/constants.py")
    assert '"Ask ChatGPT"' in chat_status
    assert "ROLE_SYSTEM_STATICTEXT" in chat_status
    assert "ROLE_SYSTEM_STATICTEXT=0x29" in constants
    assert "paste_clipboard_with_ctrl_v" in chat_status
    assert "VK_V" in keyboard
    assert "Send prompt" not in chat_status[chat_status.index("def _paste_error_output_into_chatgpt"):chat_status.index("def _chat_state_label")]


def test_download_registry_has_no_cached_historical_fallback() -> None:
    registry = _source("repo_manager/chat/registry.py")
    start = registry.index("def download_latest")
    end = registry.index("def detect_current", start)
    body = registry[start:end]
    assert "download_latest_chatgpt_response_links" in body
    assert "invoke_downloads" not in body
