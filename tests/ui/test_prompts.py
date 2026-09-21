from pathlib import Path

from repo_manager.domain import default_prompts


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_default_prompts_include_requested_reviews() -> None:
    prompts = default_prompts()
    assert len(prompts) == 4
    texts = [prompt.text for prompt in prompts]
    assert texts[0] == "Are there any vulnerabilities or potential vulnerabilities/weaknesses/flaws/gaps/etc.? (Look extra close/deep at any serializers/parsers/etc.)"
    assert texts[1] == "Are any of the tests tautological/vacuous/false positive/useless/etc.?"
    assert "Production‑ready and ready for consumers? Enterprise‑grade?" in texts[2]
    assert "Organized file/folder structure" in texts[3]
    assert "Low CRAP/CC score, aim for <= 12 CC" in texts[3]
    assert "stay DRY" in texts[3]


def test_prompts_tab_is_dynamic_and_persistent() -> None:
    layout = _source("repo_manager/ui/controllers/layout.py")
    actions = _source("repo_manager/ui/main_actions.py")
    prompts = _source("repo_manager/ui/prompts.py")
    config = _source("repo_manager/config/repository.py")
    assert 'self.notebook.add(self.prompts_tab, text="Prompts")' in layout
    assert 'Paste Prompt into ChatGPT' in actions
    assert "def _add_prompt" in prompts
    assert "def _remove_prompt" in prompts
    assert "def _move_prompt" in prompts
    assert "def _save_prompt_editor" in prompts
    assert "self._save_config()" in prompts
    assert 'data.get("prompts")' in config


def test_prompt_and_error_paste_share_one_chatgpt_composer_path() -> None:
    prompts = _source("repo_manager/ui/prompts.py")
    chat = _source("repo_manager/ui/controllers/chat_status.py")
    assert "self._paste_text_into_chatgpt(" in prompts
    assert chat.count("def _paste_text_into_chatgpt") == 1
    assert "_paste_text_into_chatgpt(" in chat[chat.index("def _paste_error_output_into_chatgpt"):]
    helper = chat[chat.index("def _paste_text_into_chatgpt"):chat.index("def _paste_error_output_into_chatgpt")]
    assert '"Ask ChatGPT"' in helper
    assert "paste_clipboard_with_ctrl_v" in helper
    assert "Send prompt" not in helper


def test_projects_own_independent_prompt_sets_and_selected_prompt_ids() -> None:
    from repo_manager.domain import Project

    first = Project(name="One")
    second = Project(name="Two")
    assert len(first.prompts) == 4
    assert len(second.prompts) == 4
    assert first.prompts is not second.prompts
    first.prompts[0].label = "Project one only"
    assert second.prompts[0].label != "Project one only"

    first.selected_prompt_id = first.prompts[1].prompt_id
    second.selected_prompt_id = second.prompts[0].prompt_id
    assert first.selected_prompt_id != second.selected_prompt_id


def test_legacy_global_prompts_migrate_into_each_project_without_sharing_objects() -> None:
    from repo_manager.config.repository import _project_from

    legacy = [{"label": "Legacy prompt", "text": "Keep me", "prompt_id": "legacy-prompt"}]
    first = _project_from({"name": "One"}, legacy_prompts=legacy)
    second = _project_from({"name": "Two"}, legacy_prompts=legacy)
    assert first.prompts[0].text == "Keep me"
    assert second.prompts[0].text == "Keep me"
    assert first.prompts is not second.prompts
    assert first.prompts[0] is not second.prompts[0]


def test_project_switch_refreshes_project_specific_prompt_and_note_views() -> None:
    settings = _source("repo_manager/ui/controllers/project_settings.py")
    sidebar = _source("repo_manager/ui/controllers/project_sidebar.py")
    assert "self._refresh_prompts()" in settings
    assert "self._refresh_notes()" in settings
    assert "self._refresh_prompts()" in sidebar
    assert "self._refresh_notes()" in sidebar


def test_main_actions_chatgpt_primary_action_order() -> None:
    actions = _source("repo_manager/ui/main_actions.py")
    section = actions[actions.index('"ChatGPT"'):actions.index('"Windows"')]
    positions = [
        section.index('"download-chatgpt-files"'),
        section.index('"focus-chatgpt"'),
        section.index('"close-chatgpt"'),
        section.index('"open-chatgpt"'),
    ]
    assert positions == sorted(positions)
