from __future__ import annotations

from repo_manager.platform.windows.msaa import accessibility_items as accessibility_module
from repo_manager.platform.windows.msaa.constants import ROLE_SYSTEM_STATICTEXT
from repo_manager.platform.windows.msaa.models import MsaaElement, MsaaTreeSnapshot


def _snapshot(name: str = "Ask ChatGPT", role: int = ROLE_SYSTEM_STATICTEXT) -> MsaaTreeSnapshot:
    return MsaaTreeSnapshot(
        source_hwnd=222,
        object_id=-4,
        available=True,
        composer_state="",
        composer_name="",
        error_text="",
        response_fingerprint="",
        response_text_length=0,
        download_count=0,
        document_name="",
        document_value="",
        document_key="",
        elements=(
            MsaaElement(index=7, name=name, role=role, default_action="clickAncestor"),
        ),
    )


def test_chatgpt_composer_falls_back_to_same_rich_tree_as_accessibility_explorer(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(accessibility_module, "invoke_named_accessibility_item_msaa", lambda *args, **kwargs: "")
    monkeypatch.setattr(accessibility_module, "inspect_accessibility_items_msaa", lambda *args, **kwargs: _snapshot())

    def fake_invoke(source_hwnd, object_id, index, expected_name="", max_nodes=20000):
        calls.append((source_hwnd, object_id, index, expected_name, max_nodes))
        return expected_name

    monkeypatch.setattr(accessibility_module, "invoke_accessibility_item_msaa", fake_invoke)
    clicked = accessibility_module.invoke_chatgpt_composer_msaa(111, "Ask ChatGPT", role=ROLE_SYSTEM_STATICTEXT)

    assert clicked == "Ask ChatGPT"
    assert calls == [(222, -4, 7, "Ask ChatGPT", 30000)]


def test_main_prompt_combobox_has_black_readable_field_and_popup() -> None:
    layout = open("repo_manager/ui/controllers/layout.py", encoding="utf-8").read()
    actions = open("repo_manager/ui/main_actions.py", encoding="utf-8").read()
    prompts = open("repo_manager/ui/prompts.py", encoding="utf-8").read()
    assert 'style="Prompt.TCombobox"' in actions
    assert 'fieldbackground="#000000"' in layout
    assert 'foreground="#ffffff"' in layout
    assert 'style_popup=self._style_prompt_picker_dropdown' in actions
    assert 'postcommand=style_popup' in actions
    assert '"-background", "#000000"' in prompts
    assert '"-foreground", "#ffffff"' in prompts


def test_chatgpt_composer_accepts_chat_with_chatgpt_alias_and_role_drift(monkeypatch) -> None:
    named_calls = []
    invoke_calls = []

    def fake_named(_hwnd, name, **_kwargs):
        named_calls.append(name)
        return ""

    monkeypatch.setattr(accessibility_module, "invoke_named_accessibility_item_msaa", fake_named)
    monkeypatch.setattr(
        accessibility_module,
        "inspect_accessibility_items_msaa",
        lambda *args, **kwargs: _snapshot("Chat with ChatGPT", role=0x2A),
    )

    def fake_invoke(source_hwnd, object_id, index, expected_name="", max_nodes=20000):
        invoke_calls.append((source_hwnd, object_id, index, expected_name, max_nodes))
        return expected_name

    monkeypatch.setattr(accessibility_module, "invoke_accessibility_item_msaa", fake_invoke)
    clicked = accessibility_module.invoke_chatgpt_composer_msaa(111)

    assert clicked == "Chat with ChatGPT"
    assert named_calls[:2] == ["Ask ChatGPT", "Chat with ChatGPT"]
    assert invoke_calls == [(222, -4, 7, "Chat with ChatGPT", 30000)]
