from __future__ import annotations

import unittest
from types import SimpleNamespace

from repo_manager.ui.app import ProjectRepoManagerApp
from repo_manager.domain import Project


class CommandErrorCopyTests(unittest.TestCase):
    def test_cargo_fmt_copy_includes_all_diffs_before_error(self) -> None:
        output = (
            "some earlier output\n"
            "==> cargo fmt --manifest-path crates/ghost-wasm/Cargo.toml -- --check\n"
            "Diff in C:\\\\repo\\src\\native.rs:11:\n"
            "-    send_call_contact_request,\n"
            "+    send_call_contact_request, send_call_control_message,\n"
            "Diff in C:\\\\repo\\src\\other.rs:25:\n"
            "-old\n"
            "+new\n"
            "ERROR: gate failed with exit code 1: cargo fmt --manifest-path crates/ghost-wasm/Cargo.toml -- --check\n"
            "QUALITY GATES FAILED with exit code 1.\n"
        )
        copied = ProjectRepoManagerApp._rust_error_tail(output)
        self.assertIsNotNone(copied)
        self.assertTrue(copied.startswith("==> cargo fmt"))
        self.assertIn("Diff in C:\\\\repo\\src\\native.rs:11:", copied)
        self.assertIn("Diff in C:\\\\repo\\src\\other.rs:25:", copied)
        self.assertIn("ERROR: gate failed with exit code 1", copied)
        self.assertNotIn("some earlier output", copied)

    def test_cargo_fmt_diff_without_error_summary_is_copyable(self) -> None:
        output = (
            "==> cargo fmt --all -- --check\n"
            "Diff in C:\\\\repo\\src\\lib.rs:2:\n"
            "-a\n+b\n"
        )
        copied = ProjectRepoManagerApp._rust_error_tail(output)
        self.assertIsNotNone(copied)
        self.assertTrue(copied.startswith("==> cargo fmt"))
        self.assertIn("Diff in ", copied)

    def test_rustfmt_diff_is_immediate_failure_signal(self) -> None:
        self.assertTrue(
            ProjectRepoManagerApp._output_has_decisive_command_error(
                "Diff in C:\\\\repo\\src\\lib.rs:2:\n-old\n+new\n"
            )
        )

    def test_python_traceback_copy_includes_exception_and_gate_summary(self) -> None:
        output = (
            "  Finished `dev` profile [unoptimized + debuginfo] target(s) in 54.27s\n"
            "Traceback (most recent call last):\n"
            "  File \"C:\\\\repo\\\\run-all-tests-windows.py\", line 155, in <module>\n"
            "    raise SystemExit(main())\n"
            "  File \"C:\\\\repo\\\\quality_gate_support.py\", line 127, in ensure_cargo_llvm_cov\n"
            "    if _runnable(str(executable), \"--version\"):\n"
            "TypeError: _runnable() takes 1 positional argument but 2 were given\n"
            "QUALITY GATES FAILED with exit code 1.\n"
        )
        copied = ProjectRepoManagerApp._rust_error_tail(output)
        self.assertIsNotNone(copied)
        self.assertTrue(copied.startswith("Traceback (most recent call last):"))
        self.assertIn("TypeError: _runnable() takes 1 positional argument but 2 were given", copied)
        self.assertIn("QUALITY GATES FAILED with exit code 1.", copied)
        self.assertNotIn("Finished `dev` profile", copied)

    def test_python_traceback_is_immediate_failure_signal(self) -> None:
        self.assertTrue(
            ProjectRepoManagerApp._output_has_decisive_command_error(
                "Traceback (most recent call last):\n  File x.py, line 1\nTypeError: broken\n"
            )
        )

    def test_unittest_error_copy_starts_at_first_error_and_keeps_everything_to_eof(self) -> None:
        output = (
            "test_web_dom_contract_is_utf8_explicit_under_ascii_locale (...) ... ok\n"
            "======================================================================\n"
            "ERROR: test_admin_explanation_precedes_password_validation (...)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            "  File \"C:\\\\repo\\qa\\test_qemu_scripts.py\", line 330, in test_admin\n"
            "ValueError: substring not found\n"
            "======================================================================\n"
            "FAIL: test_secure_release_preparation_rejects_stale_opposite_policy_artifacts (...)\n"
            "AssertionError: stale dual-authority artifact not found\n"
            "FAILED (failures=1, errors=1, skipped=38)\n"
            "make: *** [qa] Error 1\n"
            "===== COMMAND RETURNED TO TERMINAL =====\n"
            "Exit code: 2\n"
        )
        copied = ProjectRepoManagerApp._rust_error_tail(output)
        self.assertIsNotNone(copied)
        self.assertTrue(copied.startswith("ERROR: test_admin_explanation_precedes_password_validation"))
        self.assertIn("FAIL: test_secure_release_preparation_rejects_stale_opposite_policy_artifacts", copied)
        self.assertIn("make: *** [qa] Error 1", copied)
        self.assertTrue(copied.rstrip().endswith("Exit code: 2"))
        self.assertNotIn("test_web_dom_contract", copied)

    def test_flattened_terminal_error_is_found_even_when_not_at_line_start(self) -> None:
        output = (
            "earlier successful output                              "
            "ERROR: test_admin_explanation_precedes_password_validation "
            "Traceback (most recent call last): ValueError: substring not found "
            "FAILED (failures=1, errors=1) make: *** [qa] Error 1"
        )
        copied = ProjectRepoManagerApp._rust_error_tail(output)
        self.assertIsNotNone(copied)
        self.assertTrue(copied.startswith("ERROR: test_admin_explanation"))
        self.assertIn("make: *** [qa] Error 1", copied)

        self.assertTrue(ProjectRepoManagerApp._output_has_decisive_command_error(output))


class CommandProjectStateTests(unittest.TestCase):
    def _app(self):
        app = ProjectRepoManagerApp.__new__(ProjectRepoManagerApp)
        app._extracting_project_ids = set()
        app._command_error_pids = set()
        app._acknowledged_command_pids = set()
        return app

    def test_failed_run_beats_still_running_state(self) -> None:
        app = self._app()
        failed = SimpleNamespace(pid=10, exit_code=1, command_finished=False, window_open=True)
        running = SimpleNamespace(pid=20, exit_code=None, command_finished=False, window_open=True)
        app._project_command_runs = lambda _project, running_only=False: [failed, running]
        self.assertEqual(app._project_command_state(Project(project_id="p")), "error")
        self.assertIn(10, app._command_error_pids)

    def test_new_error_rearms_attention_for_previously_acknowledged_pid(self) -> None:
        app = self._app()
        app._acknowledged_command_pids.add(10)
        failed = SimpleNamespace(pid=10, exit_code=1, command_finished=False, window_open=True)
        self.assertTrue(app._command_run_failed(failed))
        self.assertNotIn(10, app._acknowledged_command_pids)


class LastExtractedMetadataTests(unittest.TestCase):
    def test_version_and_revision(self) -> None:
        self.assertEqual(
            ProjectRepoManagerApp._last_extracted_summary("ghost-talk-v1.0.0-r-321.zip"),
            "v1.0.0 r321",
        )

    def test_version_only(self) -> None:
        self.assertEqual(
            ProjectRepoManagerApp._last_extracted_summary("ghost-talk-v2.3.4.zip"),
            "v2.3.4",
        )

    def test_revision_only(self) -> None:
        self.assertEqual(
            ProjectRepoManagerApp._last_extracted_summary("ghost-talk-r-180.zip"),
            "r180",
        )

    def test_no_metadata_uses_full_zip_name(self) -> None:
        self.assertEqual(
            ProjectRepoManagerApp._last_extracted_summary("ghost-talk-latest.zip"),
            "ghost-talk-latest.zip",
        )


if __name__ == "__main__":
    unittest.main()
