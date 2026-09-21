from __future__ import annotations

import unittest
from unittest.mock import patch
from contextlib import nullcontext
from types import SimpleNamespace

from repo_manager.chat import ChatState, ChatWatch, ChatWatchManager
from repo_manager.domain import Project
from repo_manager.ui.app import ProjectRepoManagerApp
from repo_manager.platform.windows.uia_fallback import ChatAccessibilitySnapshot
from repo_manager.platform.windows.msaa import MsaaElement, MsaaWinEvent, invoke_chatgpt_retry_msaa, make_document_key
from repo_manager.platform.windows.msaa.models import _ActionTarget
from repo_manager.platform.windows.msaa.downloads import _latest_download_group, _window_tree_download_fallback
from repo_manager.platform.windows.msaa.response_structure import analyze_latest_response


class ChatWatcherIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = []
        self.manager = ChatWatchManager(self.events.append)
        self.doc_a = make_document_key("", "https://chatgpt.com/c/aaa")
        self.doc_b = make_document_key("", "https://chatgpt.com/c/bbb")
        self.manager._watches[100] = ChatWatch(
            project_id="p1",
            url_id="u1",
            hwnd=100,
            label="A",
            url="https://chatgpt.com/c/aaa",
            expected_document_key=self.doc_a,
            document_key=self.doc_a,
            initialized=True,
        )
        self.manager._watches[200] = ChatWatch(
            project_id="p2",
            url_id="u2",
            hwnd=200,
            label="B",
            url="https://chatgpt.com/c/bbb",
            expected_document_key=self.doc_b,
            document_key=self.doc_b,
            initialized=True,
        )

    @staticmethod
    def event(
        top: int,
        source: int,
        name: str,
        document_key: str,
        object_id: int = -4,
        child_id: int = 7,
    ) -> MsaaWinEvent:
        return MsaaWinEvent(
            top_hwnd=top,
            event_id=1,
            source_hwnd=source,
            object_id=object_id,
            child_id=child_id,
            name=name,
            role=43,
            default_action="Press",
            document_key=document_key,
        )

    def test_other_tab_cannot_finish_running_chat(self) -> None:
        self.manager._on_msaa_event(self.event(100, 111, "Stop answering", self.doc_a))
        self.assertEqual(self.manager._watches[100].state, ChatState.IN_PROGRESS)

        self.manager._on_msaa_event(self.event(100, 222, "Send prompt", self.doc_b))
        self.assertEqual(self.manager._watches[100].state, ChatState.IN_PROGRESS)

        self.manager._on_msaa_event(self.event(100, 111, "Send prompt", self.doc_a))
        self.assertEqual(self.manager._watches[100].state, ChatState.SUCCESS)

    def test_unmapped_event_routes_only_by_unique_document(self) -> None:
        self.manager._on_msaa_event(self.event(0, 222, "Stop answering", self.doc_b))
        self.assertEqual(self.manager._watches[200].state, ChatState.IN_PROGRESS)
        self.assertEqual(self.manager._watches[100].state, ChatState.IDLE)

    def test_same_chat_can_run_finish_then_run_finish_again(self) -> None:
        watch = self.manager._watches[100]

        self.manager._on_msaa_event(self.event(100, 111, "Stop answering", self.doc_a))
        self.assertEqual(watch.state, ChatState.IN_PROGRESS)
        self.manager._on_msaa_event(self.event(100, 111, "Send prompt", self.doc_a))
        self.assertEqual(watch.state, ChatState.SUCCESS)
        self.assertEqual(watch.active_response_document_key, "")

        # A completed response must not leave an active identity behind that
        # blocks the next response lifecycle in the same ChatGPT tab.
        self.manager._on_msaa_event(self.event(100, 111, "Stop answering", self.doc_a))
        self.assertEqual(watch.state, ChatState.IN_PROGRESS)
        self.manager._on_msaa_event(self.event(100, 111, "Send prompt", self.doc_a))
        self.assertEqual(watch.state, ChatState.SUCCESS)
        self.assertEqual(watch.active_response_document_key, "")


    def test_same_composer_object_finishes_even_when_final_event_has_no_document(self) -> None:
        # Firefox can omit the containing document on the final NameChange. The
        # exact composer object identity still proves that Stop -> Send belongs
        # to the same ChatGPT tab/response.
        self.manager._watches[100].document_key = ""
        self.manager._on_msaa_event(self.event(100, 111, "Stop answering", "", object_id=-4, child_id=9))
        self.assertEqual(self.manager._watches[100].state, ChatState.IN_PROGRESS)
        self.assertEqual(self.manager._watches[100].active_response_document_key, "")

        self.manager._on_msaa_event(self.event(100, 111, "Send prompt", "", object_id=-4, child_id=9))
        self.assertEqual(self.manager._watches[100].state, ChatState.SUCCESS)

    def test_different_composer_object_cannot_finish_identityless_running_chat(self) -> None:
        self.manager._watches[100].document_key = ""
        self.manager._on_msaa_event(self.event(100, 111, "Stop answering", "", object_id=-4, child_id=9))
        self.assertEqual(self.manager._watches[100].state, ChatState.IN_PROGRESS)

        # Same Firefox HWND, but another tab/composer accessible object.
        self.manager._on_msaa_event(self.event(100, 111, "Send prompt", "", object_id=-4, child_id=10))
        self.assertEqual(self.manager._watches[100].state, ChatState.IN_PROGRESS)

    def test_other_tab_snapshot_cannot_finish_running_chat(self) -> None:
        watch = self.manager._watches[100]
        watch.state = ChatState.IN_PROGRESS
        watch.saw_progress = True
        watch.accessibility_backend = "winevent"
        watch.active_response_document_key = self.doc_a
        watch.active_response_source_hwnd = 111

        wrong = ChatAccessibilitySnapshot(
            hwnd=100,
            available=True,
            in_progress=False,
            composer_state="send",
            error_text="",
            response_fingerprint="wrong",
            response_text_length=5,
            download_count=0,
            backend="msaa",
            composer_name="Send prompt",
            document_key=self.doc_b,
        )
        self.manager._apply_snapshots({100: wrong})
        self.assertEqual(watch.state, ChatState.IN_PROGRESS)

        right = ChatAccessibilitySnapshot(
            hwnd=100,
            available=True,
            in_progress=False,
            composer_state="send",
            error_text="",
            response_fingerprint="right",
            response_text_length=5,
            download_count=0,
            backend="msaa",
            composer_name="Send prompt",
            document_key=self.doc_a,
        )
        self.manager._apply_snapshots({100: right})
        self.assertEqual(watch.state, ChatState.SUCCESS)

    def test_retained_exact_document_can_finish_without_document_key(self) -> None:
        watch = self.manager._watches[100]
        watch.document_key = ""
        watch.active_response_document_key = ""
        watch.state = ChatState.IN_PROGRESS
        watch.saw_progress = True
        watch.accessibility_backend = "winevent"
        exact = ChatAccessibilitySnapshot(
            hwnd=100,
            available=True,
            in_progress=False,
            composer_state="send",
            error_text="",
            response_fingerprint="",
            response_text_length=0,
            download_count=0,
            backend="msaa-retained",
            composer_name="Send prompt",
            document_key="",
        )
        self.manager._apply_snapshots({100: exact})
        self.assertEqual(watch.state, ChatState.SUCCESS)

    def test_structural_finished_signature_completes_running_response(self) -> None:
        watch = self.manager._watches[100]
        watch.state = ChatState.IN_PROGRESS
        watch.saw_progress = True
        watch.active_response_document_key = self.doc_a
        snapshot = ChatAccessibilitySnapshot(
            hwnd=100, available=True, in_progress=False, composer_state="unknown",
            error_text="", response_fingerprint="done", response_text_length=10,
            download_count=1, backend="msaa", document_key=self.doc_a,
            worked_for_text="8m 19s", latest_response_complete=True,
        )
        self.manager._apply_snapshots({100: snapshot})
        self.assertEqual(watch.state, ChatState.SUCCESS)
        self.assertEqual(watch.worked_for_text, "8m 19s")


class LatestDownloadGroupingTests(unittest.TestCase):
    def test_uses_newest_response_boundary(self) -> None:
        elements = [
            MsaaElement(10, "ChatGPT said:", 0, ""),
            MsaaElement(20, "Download old.zip", 0, ""),
            MsaaElement(40, "ChatGPT said:", 0, ""),
            MsaaElement(50, "Download new-a.zip", 0, ""),
            MsaaElement(51, "Download new-b.zip", 0, ""),
        ]
        actions = [
            _ActionTarget(20, "Download old.zip", 0, 0, 0),
            _ActionTarget(50, "Download new-a.zip", 0, 0, 0),
            _ActionTarget(51, "Download new-b.zip", 0, 0, 0),
        ]
        self.assertEqual(
            [item.name for item in _latest_download_group(elements, actions)],
            ["Download new-a.zip", "Download new-b.zip"],
        )


    def test_named_download_button_is_accepted_inside_latest_response(self) -> None:
        elements = [
            MsaaElement(10, "ChatGPT said:", 0, ""),
            MsaaElement(20, "Download KasKold v58", 43, "Press"),
            MsaaElement(30, "Response actions", 43, "Press"),
            MsaaElement(31, "Copy response", 43, "Press"),
            MsaaElement(40, "Start Voice", 43, "Press"),
        ]
        actions = [_ActionTarget(20, "Download KasKold v58", 43, 101, 0)]
        self.assertEqual(
            [item.name for item in _latest_download_group(elements, actions)],
            ["Download KasKold v58"],
        )

    def test_rich_window_tree_fallback_invokes_named_download_button(self) -> None:
        from repo_manager.platform.windows.msaa.models import MsaaTreeSnapshot

        elements = [
            MsaaElement(10, "ChatGPT said:", 0, ""),
            MsaaElement(20, "Download KasKold v58", 43, "Press"),
            MsaaElement(30, "Response actions", 43, "Press"),
            MsaaElement(31, "Copy response", 43, "Press"),
            MsaaElement(40, "Start Voice", 43, "Press"),
        ]
        actions = [_ActionTarget(20, "Download KasKold v58", 43, 101, 0)]
        snapshot = MsaaTreeSnapshot(
            source_hwnd=123, object_id=-4, available=True,
            composer_state="voice", composer_name="Start Voice", error_text="",
            response_fingerprint="x", response_text_length=1, download_count=1,
            document_name="", document_value="", document_key="",
            elements=tuple(elements), worked_for_text="", latest_response_complete=False,
        )
        with (
            patch("repo_manager.platform.windows.msaa.downloads.os.name", "nt"),
            patch("repo_manager.platform.windows.msaa.downloads._com_scope", return_value=nullcontext()),
            patch("repo_manager.platform.windows.msaa.downloads._candidate_hwnds", return_value=[123]),
            patch("repo_manager.platform.windows.msaa.downloads._accessible_from_window", side_effect=[999, 0]),
            patch("repo_manager.platform.windows.msaa.downloads._walk_tree", return_value=(elements, actions)),
            patch("repo_manager.platform.windows.msaa.downloads._tree_snapshot", return_value=snapshot),
            patch("repo_manager.platform.windows.msaa.downloads._child_variant", return_value=object()),
            patch("repo_manager.platform.windows.msaa.downloads._acc_name", return_value="Download KasKold v58"),
            patch("repo_manager.platform.windows.msaa.downloads._acc_role", return_value=43),
            patch("repo_manager.platform.windows.msaa.downloads._invoke_target", return_value=True) as invoke,
            patch("repo_manager.platform.windows.msaa.downloads._release_actions"),
        ):
            self.assertEqual(
                _window_tree_download_fallback(123),
                ["Download KasKold v58"],
            )
            invoke.assert_called_once()

    def test_download_must_be_before_latest_response_actions_boundary(self) -> None:
        elements = [
            MsaaElement(10, "ChatGPT said:", 0, ""),
            MsaaElement(20, "Download newest.zip", 43, "Press"),
            MsaaElement(30, "Response actions", 43, "Press"),
            MsaaElement(31, "Copy response", 43, "Press"),
            MsaaElement(40, "Download historical-or-unrelated.zip", 43, "Press"),
        ]
        actions = [
            _ActionTarget(20, "Download newest.zip", 43, 0, 0),
            _ActionTarget(40, "Download historical-or-unrelated.zip", 43, 0, 0),
        ]
        self.assertEqual(
            [item.name for item in _latest_download_group(elements, actions)],
            ["Download newest.zip"],
        )

    def test_finished_structure_extracts_worked_for_duration(self) -> None:
        structure = analyze_latest_response([
            MsaaElement(10, "ChatGPT said:", 0, ""),
            MsaaElement(20, "Worked for 8m 19s", 43, "Press"),
            MsaaElement(21, "Download", 43, "Press"),
            MsaaElement(30, "Response actions", 43, "Press"),
            MsaaElement(31, "Copy response", 43, "Press"),
            MsaaElement(40, "Start Voice", 43, "Press"),
        ])
        self.assertEqual(structure.worked_for_text, "8m 19s")
        self.assertTrue(structure.completed_successfully)


    def test_trailing_newer_response_without_download_never_falls_back_to_old_artifact(self) -> None:
        elements = [
            MsaaElement(10, "ChatGPT said:", 0, ""),
            MsaaElement(20, "Download r179", 0, ""),
            MsaaElement(40, "ChatGPT said:", 0, ""),
            MsaaElement(50, "Download r180", 0, ""),
            MsaaElement(70, "ChatGPT said:", 0, ""),
            MsaaElement(80, "A later response with no artifact", 0, ""),
        ]
        actions = [
            _ActionTarget(20, "Download r179", 0, 0, 0),
            _ActionTarget(50, "Download r180", 0, 0, 0),
        ]
        self.assertEqual(
            [item.name for item in _latest_download_group(elements, actions)],
            [],
        )

    def test_without_response_marker_uses_only_last_download_not_gap_guessing(self) -> None:
        elements = [
            MsaaElement(10, "Download older.zip", 0, ""),
            MsaaElement(12, "some text", 0, ""),
            MsaaElement(13, "Download newest.zip", 0, ""),
        ]
        actions = [
            _ActionTarget(10, "Download older.zip", 0, 0, 0),
            _ActionTarget(13, "Download newest.zip", 0, 0, 0),
        ]
        self.assertEqual(
            [item.name for item in _latest_download_group(elements, actions)],
            ["Download newest.zip"],
        )



class LiveDownloadPathTests(unittest.TestCase):
    def test_user_download_invokes_newest_accessibility_group_without_keyboard(self) -> None:
        manager = ChatWatchManager(lambda _event: None)
        manager._watches[123] = ChatWatch(
            project_id="p",
            url_id="u",
            hwnd=123,
            label="Chat",
        )
        with (
            patch("repo_manager.chat.registry.os.name", "nt"),
            patch("repo_manager.chat.registry.windows_hwnd_exists", return_value=True),
            patch(
                "repo_manager.chat.registry.download_latest_chatgpt_response_links",
                return_value=["Download r180"],
            ) as scan,
        ):
            self.assertEqual(manager.download_latest(123, ("Ghost Talk",)), ["Download r180"])
            scan.assert_called_once_with(123, ("Ghost Talk",), expected_document_key="", anchor_source_hwnd=0, anchor_object_id=0, anchor_child_id=0)


class ExtractingProjectStatusTests(unittest.TestCase):
    def test_extracting_overrides_idle_command_status(self) -> None:
        project = Project(name="P", project_id="p")
        dummy = SimpleNamespace(_extracting_project_ids={"p"}, _command_runs={})
        self.assertEqual(ProjectRepoManagerApp._project_command_state(dummy, project), "extracting")



if __name__ == "__main__":
    unittest.main()


class AccessibilityOnlyDownloadTests(unittest.TestCase):
    def test_download_uses_accessibility_scan_without_keyboard_or_remote_agent(self) -> None:
        manager = ChatWatchManager(lambda _event: None)
        manager._watches[901] = ChatWatch(
            project_id="p",
            url_id="u",
            hwnd=901,
            label="Chat",
            url="https://chatgpt.com/c/example",
            document_key="url:https://chatgpt.com/c/example",
        )
        with (
            patch("repo_manager.chat.registry.os.name", "nt"),
            patch("repo_manager.chat.registry.windows_hwnd_exists", return_value=True),
            patch(
                "repo_manager.chat.registry.download_latest_chatgpt_response_links",
                return_value=["Download newest.zip"],
            ) as scan,
        ):
            self.assertEqual(manager.download_latest(901), ["Download newest.zip"])
            scan.assert_called_once()

    def test_chat_watcher_has_no_remote_agent_or_bidi_dependency(self) -> None:
        import inspect
        import repo_manager.chat.registry as module

        source = inspect.getsource(module)
        self.assertNotIn("firefox_bidi", source)
        self.assertNotIn("RemoteAgent", source)
        self.assertNotIn("WebDriver", source)
        self.assertNotIn("send_windows_virtual_key", source)


class RetainedComposerHitTestSourceTests(unittest.TestCase):
    def test_retained_probe_forces_document_hit_test_without_mouse_input(self) -> None:
        import inspect
        from repo_manager.platform.windows import msaa as windows_msaa

        source = inspect.getsource(windows_msaa.MsaaWinEventMonitor._process_commands)
        self.assertIn("_hit_test_composer_in_document", source)
        self.assertIn("composer_point", source)
        self.assertNotIn("SetCursorPos", source)
        self.assertNotIn("SendInput", source)


class StructuralCompletionAuthorityTests(unittest.TestCase):
    def test_full_latest_response_signature_finishes_running_watch_without_document_identity(self) -> None:
        manager = ChatWatchManager(lambda _event: None)
        watch = ChatWatch(
            project_id="p",
            url_id="u",
            hwnd=321,
            label="Chat",
            initialized=True,
            state=ChatState.IN_PROGRESS,
            saw_progress=True,
            active_response_document_key="url:https://chatgpt.com/c/example",
        )
        manager._watches[321] = watch
        snapshot = ChatAccessibilitySnapshot(
            hwnd=321,
            available=True,
            in_progress=False,
            composer_state="voice",
            error_text="",
            response_fingerprint="done",
            response_text_length=100,
            download_count=0,
            backend="msaa",
            composer_name="Start Voice",
            document_key="",
            worked_for_text="8m 19s",
            latest_response_complete=True,
        )
        manager._apply_snapshots({321: snapshot})
        self.assertEqual(watch.state, ChatState.SUCCESS)
        self.assertEqual(watch.worked_for_text, "8m 19s")

    def test_complete_latest_structure_overrides_stale_stop_accessible(self) -> None:
        from repo_manager.platform.windows.msaa.tree import _tree_snapshot

        elements = [
            MsaaElement(0, "Stop answering", 0, ""),
            MsaaElement(1, "ChatGPT said:", 0, ""),
            MsaaElement(2, "Worked for 8m 19s", 0, "Press"),
            MsaaElement(3, "Response actions", 0, ""),
            MsaaElement(4, "Copy response", 0, "Press"),
            MsaaElement(5, "Start Voice", 0, "Press"),
        ]
        snapshot = _tree_snapshot(1, -4, elements)
        self.assertTrue(snapshot.latest_response_complete)
        self.assertEqual(snapshot.worked_for_text, "8m 19s")
        self.assertEqual(snapshot.composer_state, "voice")


class MessageDeliveryTimeoutTests(unittest.TestCase):
    def test_exact_timeout_accessibility_item_marks_idle_watch_error(self) -> None:
        manager = ChatWatchManager(lambda _event: None)
        doc = make_document_key("", "https://chatgpt.com/c/timeout")
        watch = ChatWatch(
            project_id="p",
            url_id="u",
            hwnd=777,
            label="Chat",
            url="https://chatgpt.com/c/timeout",
            expected_document_key=doc,
            document_key=doc,
            initialized=True,
            state=ChatState.IDLE,
            acknowledged=True,
        )
        manager._watches[777] = watch
        snapshot = ChatAccessibilitySnapshot(
            hwnd=777,
            available=True,
            in_progress=False,
            composer_state="send",
            error_text="Message delivery timed out. Please try again.",
            response_fingerprint="timeout",
            response_text_length=42,
            download_count=0,
            backend="msaa",
            composer_name="Send prompt",
            document_key=doc,
        )
        manager._apply_snapshots({777: snapshot})
        self.assertEqual(watch.state, ChatState.ERROR)
        self.assertFalse(watch.acknowledged)
        self.assertEqual(watch.error_text, "Message delivery timed out. Please try again.")
        dummy = SimpleNamespace(
            _chat_watch_manager=SimpleNamespace(watches_for_project=lambda _project_id: [watch])
        )
        self.assertEqual(
            ProjectRepoManagerApp._project_chat_state(dummy, Project(project_id="p")),
            (ChatState.ERROR, True),
        )

    def test_msaa_tree_recognizes_exact_timeout_text_role_42_click_ancestor(self) -> None:
        from repo_manager.platform.windows.msaa.tree import _tree_snapshot

        snapshot = _tree_snapshot(
            1,
            -4,
            [MsaaElement(0, "Message delivery timed out. Please try again.", 42, "clickAncestor")],
        )
        self.assertEqual(snapshot.error_text, "Message delivery timed out. Please try again.")

    def test_timeout_automatically_invokes_retry_once_until_next_run(self) -> None:
        manager = ChatWatchManager(lambda _event: None)
        doc = make_document_key("", "https://chatgpt.com/c/retry-timeout")
        watch = ChatWatch(
            project_id="p",
            url_id="u",
            hwnd=778,
            label="Chat",
            url="https://chatgpt.com/c/retry-timeout",
            expected_document_key=doc,
            document_key=doc,
            initialized=True,
            state=ChatState.IDLE,
        )
        manager._watches[778] = watch

        timeout = ChatAccessibilitySnapshot(
            hwnd=778, available=True, in_progress=False, composer_state="send",
            error_text="Message delivery timed out. Please try again.",
            response_fingerprint="timeout-1", response_text_length=42, download_count=0,
            backend="msaa", composer_name="Send prompt", document_key=doc,
        )
        running = ChatAccessibilitySnapshot(
            hwnd=778, available=True, in_progress=True, composer_state="stop",
            error_text="", response_fingerprint="running", response_text_length=1,
            download_count=0, backend="msaa", composer_name="Stop answering",
            document_key=doc,
        )

        with patch("repo_manager.chat.monitor.invoke_chatgpt_retry_msaa", return_value="Retry") as retry:
            manager._apply_snapshots({778: timeout})
            self.assertEqual(watch.state, ChatState.ERROR)
            self.assertTrue(watch.timeout_retry_clicked)
            retry.assert_called_once_with(778)

            # The same still-visible timeout must not click Retry repeatedly.
            manager._apply_snapshots({778: timeout})
            retry.assert_called_once_with(778)

            # A new response lifecycle rearms automatic Retry for a later timeout.
            manager._apply_snapshots({778: running})
            self.assertEqual(watch.state, ChatState.IN_PROGRESS)
            self.assertFalse(watch.timeout_retry_clicked)
            manager._apply_snapshots({778: timeout})
            self.assertEqual(retry.call_count, 2)

    def test_retry_invocation_requires_timeout_and_exact_retry_in_same_tree(self) -> None:
        from repo_manager.platform.windows.msaa import accessibility_items as items

        elements = [
            MsaaElement(0, "Message delivery timed out. Please try again.", 42, "clickAncestor"),
            MsaaElement(1, "Retry", 42, "clickAncestor"),
        ]
        actions = [_ActionTarget(index=1, name="Retry", role=42, acc_ptr=123, child_id=0)]
        with (
            patch.object(items.os, "name", "nt"),
            patch.object(items, "_com_scope", return_value=nullcontext()),
            patch.object(items, "_candidate_hwnds", return_value=[99]),
            patch.object(items, "_accessible_from_window", return_value=321),
            patch.object(items, "_walk_tree", return_value=(elements, actions)),
            patch.object(items, "_invoke_target", return_value=True) as invoke,
            patch.object(items, "_release_actions"),
        ):
            self.assertEqual(invoke_chatgpt_retry_msaa(99), "Retry")
            invoke.assert_called_once()

        no_timeout = [MsaaElement(1, "Retry", 42, "clickAncestor")]
        with (
            patch.object(items.os, "name", "nt"),
            patch.object(items, "_com_scope", return_value=nullcontext()),
            patch.object(items, "_candidate_hwnds", return_value=[99]),
            patch.object(items, "_accessible_from_window", return_value=321),
            patch.object(items, "_walk_tree", return_value=(no_timeout, actions)),
            patch.object(items, "_invoke_target", return_value=True) as invoke,
            patch.object(items, "_release_actions"),
        ):
            self.assertEqual(invoke_chatgpt_retry_msaa(99), "")
            invoke.assert_not_called()
