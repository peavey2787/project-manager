from pathlib import Path
from types import SimpleNamespace

from repo_manager.ui.command_toolbar import CommandToolbarController

ROOT = Path(__file__).resolve().parents[2]


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_command_toolbar_uses_caption_free_52px_assets_and_top_manage_row() -> None:
    toolbar = (ROOT / "repo_manager/ui/command_toolbar.py").read_text(encoding="utf-8")
    layout = (ROOT / "repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8")
    assets = ROOT / "repo_manager/ui/assets/commands"
    expected = {
        "add-command.png",
        "edit-command.png",
        "remove-command.png",
        "run-command.png",
        "focus-command.png",
        "copy-command.png",
        "copy-output.png",
        "copy-error-output.png",
        "close-command.png",
    }
    assert {p.name for p in assets.glob("*.png")} == expected
    assert all(_png_size(assets / name) == (52, 52) for name in expected)

    common = ROOT / "repo_manager/ui/assets/common"
    for name in ("move-up.png", "move-down.png"):
        assert _png_size(common / name) == (52, 52)

    assert 'style="CommandImageAction.TButton"' in toolbar
    assert 'image=self._command_action_images[asset]' in toolbar
    assert '"copy-output"' in toolbar
    assert '"copy-error-output"' in toolbar
    assert '"close-command"' in toolbar
    assert '"move-up"' in toolbar and '"move-down"' in toolbar

    manage_call = 'self._build_command_manage_toolbar(self.commands_tab)'
    tree_build = 'self.commands_tree = ttk.Treeview('
    runtime_call = 'self._build_command_runtime_toolbar(self.commands_tab)'
    assert manage_call in layout
    assert runtime_call in layout
    assert layout.index(manage_call) < layout.index(tree_build) < layout.index(runtime_call)


def test_copy_command_copies_saved_command_text_and_reports_status() -> None:
    class Fake:
        def __init__(self):
            self.spec = SimpleNamespace(label="QA", command="make qa --resume")
            self.clipboard = None
            self.status = None

        def _selected_command(self):
            return self.spec

        def clipboard_clear(self):
            self.clipboard = ""

        def clipboard_append(self, value):
            self.clipboard = value

        def update_idletasks(self):
            pass

        def _set_status(self, value):
            self.status = value

        def _update_command_action_buttons(self):
            raise AssertionError("selection exists")

    fake = Fake()
    CommandToolbarController._copy_selected_command_text(fake)
    assert fake.clipboard == "make qa --resume"
    assert fake.status == "Copied command: QA"


def test_copy_command_reactivity_is_selection_scoped_not_window_scoped() -> None:
    state = (ROOT / "repo_manager/ui/controllers/command_state.py").read_text(encoding="utf-8")
    selection_group = '("Edit", "Remove", "Run Now", "Copy Command", "Move Up", "Move Down")'
    assert selection_group in state
    live_window_group = state.split('for name in ("Focus", "Copy Output", "Copy Error Output", "Close"):', 1)
    assert len(live_window_group) == 2
    between = live_window_group[0].split(f"for name in {selection_group}:", 1)[1]
    assert '"Copy Command"' not in between


def test_prompt_reorder_uses_shared_up_down_image_assets() -> None:
    prompts = (ROOT / "repo_manager/ui/prompts.py").read_text(encoding="utf-8")
    assert 'move-up.png' in prompts
    assert 'move-down.png' in prompts
    assert 'image=self._prompt_order_images["up"]' in prompts
    assert 'image=self._prompt_order_images["down"]' in prompts
    assert '("▲", lambda: self._move_prompt(-1))' not in prompts
    assert '("▼", lambda: self._move_prompt(1))' not in prompts


def test_command_buttons_have_visible_hover_pressed_feedback() -> None:
    layout = (ROOT / "repo_manager/ui/controllers/layout.py").read_text(encoding="utf-8")
    assert '"CommandImageAction.TButton"' in layout
    assert 'padding=(3, 3)' in layout
    assert 'borderwidth=2' in layout
    assert 'bordercolor=[("active", select), ("pressed", select)]' in layout
    assert 'relief=[("pressed", "sunken"), ("active", "raised")]' in layout


def test_commands_tab_falls_back_to_first_saved_command_when_nothing_is_running() -> None:
    actions = (ROOT / "repo_manager/ui/controllers/command_actions.py").read_text(encoding="utf-8")
    assert 'if project.commands:' in actions
    assert 'preferred_id=project.commands[0].command_id' in actions
    active_branch = actions.index('if active is not None:')
    fallback_branch = actions.index('if project.commands:', active_branch)
    assert active_branch < fallback_branch
