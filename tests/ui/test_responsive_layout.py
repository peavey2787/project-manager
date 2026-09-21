from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_commands_column_order_is_label_auto_status_command() -> None:
    layout = source("repo_manager/ui/controllers/layout.py")
    editor = source("repo_manager/ui/controllers/command_editor.py")
    state = source("repo_manager/ui/controllers/command_state.py")
    assert 'columns=("label", "auto", "status", "command")' in layout
    label = layout.index('self.commands_tree.heading("label", text="Label")')
    auto = layout.index('self.commands_tree.heading("auto", text="Auto order")')
    status = layout.index('self.commands_tree.heading("status", text="Window Status")')
    command = layout.index('self.commands_tree.heading("command", text="Command / Script")')
    assert label < auto < status < command
    assert 'self._command_window_status(command.command_id),\n                    command.command,' in editor
    assert 'if values[2] != status:' in state


def test_main_actions_scroll_when_vertical_space_is_tight() -> None:
    layout = source("repo_manager/ui/controllers/layout.py")
    assert 'self.main_actions_scroll = ScrollableFrame(left)' in layout
    assert 'self.main_actions_scroll.pack(fill="both", expand=True' in layout
    assert 'self._build_main_actions(self.main_actions_scroll.inner)' in layout


def test_prompts_default_to_top_bottom_half_split() -> None:
    prompts = source("repo_manager/ui/prompts.py")
    assert 'ttk.Panedwindow(parent, orient="vertical")' in prompts
    assert 'panes.add(prompt_list, weight=1)' in prompts
    assert 'panes.add(editor, weight=1)' in prompts
    assert 'self.prompts_panes.sashpos(0, height // 2)' in prompts


def test_files_default_to_equal_width_tree_and_editor() -> None:
    files = source("repo_manager/ui/files.py")
    assert 'panes.add(tree_frame, weight=1)' in files
    assert 'panes.add(editor_frame, weight=1)' in files
    assert 'self.files_panes.sashpos(0, width // 2)' in files


def test_project_status_is_in_sidebar_and_snapshot_group_follows_windows() -> None:
    layout = source("repo_manager/ui/controllers/layout.py")
    actions = source("repo_manager/ui/main_actions.py")
    project_table = layout.index("self.project_list = ttk.Treeview")
    status = layout.index('ttk.LabelFrame(left, text="Project status"')
    main_actions = layout.index("self.main_actions_scroll = ScrollableFrame(left)")
    assert project_table < status < main_actions
    overview = layout[layout.index("def _build_overview"):layout.index("def _build_commands")]
    assert 'text="Project status"' not in overview
    assert actions.index('"Windows"') < actions.index('"Snapshot"')


def test_title_and_text_buttons_preserve_full_minimum_label_width() -> None:
    app = source("repo_manager/ui/app.py")
    layout = source("repo_manager/ui/controllers/layout.py")
    files = source("repo_manager/ui/files.py")
    assert 'self.title("Project Repo Manager v0.0.49")' in app
    assert 'self.bind_class("TButton", "<Map>", self._ensure_text_button_min_width' in layout
    assert 'button.configure(width=-desired)' in layout
    assert 'text="Save"' in files
