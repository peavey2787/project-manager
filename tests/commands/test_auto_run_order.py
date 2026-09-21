from __future__ import annotations

from repo_manager.commands.scheduling import build_auto_run_groups
from repo_manager.config.repository import _command_from
from repo_manager.domain import CommandSpec


def test_auto_run_groups_are_sorted_and_same_order_is_concurrent_batch() -> None:
    commands = [
        CommandSpec(label="Later", command="later", auto_run=True, auto_run_order=7),
        CommandSpec(label="First A", command="a", auto_run=True, auto_run_order=1),
        CommandSpec(label="Disabled", command="skip", auto_run=False, auto_run_order=0),
        CommandSpec(label="First B", command="b", auto_run=True, auto_run_order=1),
        CommandSpec(label="Middle", command="middle", auto_run=True, auto_run_order=4),
    ]
    groups = build_auto_run_groups(commands)
    assert [group.order for group in groups] == [1, 4, 7]
    assert [command.label for command in groups[0].commands] == ["First A", "First B"]
    assert [command.label for command in groups[1].commands] == ["Middle"]
    assert [command.label for command in groups[2].commands] == ["Later"]


def test_auto_run_order_round_trips_and_is_bounded() -> None:
    parsed = _command_from({"label": "QA", "command": "make qa", "auto_run": True, "auto_run_order": "9"})
    assert parsed.auto_run_order == 9
    assert _command_from({"auto_run_order": 99}).auto_run_order == 9
    assert _command_from({"auto_run_order": -3}).auto_run_order == 0
    assert _command_from({"auto_run_order": "invalid"}).auto_run_order == 0


def test_command_dialog_exposes_zero_through_nine_order_picker() -> None:
    source = open("repo_manager/ui/dialogs/command.py", encoding="utf-8").read()
    assert 'values=tuple(str(value) for value in range(10))' in source
    assert 'text="Order:"' in source
    assert 'auto_run_order=int(self.order_var.get() or 0)' in source


def test_windows_auto_run_waits_for_exit_codes_before_advancing_group() -> None:
    source = open("repo_manager/ui/auto_run.py", encoding="utf-8").read()
    assert "code = run.exit_code" in source
    assert "job.group_index += 1" in source
    assert "self._start_auto_run_group(job)" in source
    assert "for command in group.commands:" in source


def test_command_completion_mode_round_trips_with_default_five_second_delay() -> None:
    delayed = _command_from({
        "label": "Serve",
        "command": "python -m http.server",
        "auto_run": True,
        "completion_mode": "delay",
    })
    assert delayed.completion_mode == "delay"
    assert delayed.completion_delay_seconds == 5.0
    assert _command_from({"completion_mode": "unknown"}).completion_mode == "exit_code"
    assert _command_from({"completion_delay_seconds": "2.5"}).completion_delay_seconds == 2.5


def test_command_dialog_exposes_exit_code_or_delay_radio_options() -> None:
    source = open("repo_manager/ui/dialogs/command.py", encoding="utf-8").read()
    assert 'text="Wait for exit code"' in source
    assert 'text="Wait for delay"' in source
    assert 'value="exit_code"' in source
    assert 'value="delay"' in source
    assert 'completion_delay_seconds=float(self.delay_var.get().strip() or "5")' in source


def test_delay_completion_advances_without_requiring_exit_code() -> None:
    source = open("repo_manager/ui/auto_run.py", encoding="utf-8").read()
    assert 'if command.completion_mode == "delay"' in source
    assert "if not run.delay_elapsed" in source
    assert "code is not None and code != 0" in source
    assert "reached its" in source and "completion delay" in source


def test_command_auto_close_on_success_round_trips_and_is_exposed_in_dialog() -> None:
    parsed = _command_from({"label": "QA", "command": "make qa", "auto_close_on_success": True})
    assert parsed.auto_close_on_success is True
    assert _command_from({}).auto_close_on_success is False

    dialog = open("repo_manager/ui/dialogs/command.py", encoding="utf-8").read()
    actions = open("repo_manager/ui/controllers/command_actions.py", encoding="utf-8").read()
    auto_run = open("repo_manager/ui/auto_run.py", encoding="utf-8").read()
    assert 'text="Auto close command window on success"' in dialog
    assert "auto_close_on_success=self.auto_close_var.get()" in dialog
    assert "spec.auto_close_on_success" in actions
    assert "command.auto_close_on_success" in auto_run
    assert "close_windows_terminal(run)" in auto_run
