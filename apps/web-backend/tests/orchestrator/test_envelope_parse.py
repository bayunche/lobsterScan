"""P5 US2 · 信封解析(spec 005-transcript-aware-prompt）

T014 · FR-007/008/012 · US2-AC4

_unwrap_envelope 六种输入:信封 / 旧格式 / 缺 action / artifact 非 dict / action 非法 / None。
"""

from __future__ import annotations

from app.orchestrator.pipeline import _unwrap_envelope


def test_envelope_format_extracts_artifact():
    action, mentions, intent, reason, artifact = _unwrap_envelope({
        "action": "speak", "mentions": ["copywriter"], "intent": "propose",
        "reason": "", "artifact": {"script_md": "hi", "slides": []},
    })
    assert action == "speak"
    assert mentions == ["copywriter"]
    assert intent == "propose"
    assert artifact == {"script_md": "hi", "slides": []}


def test_envelope_silent():
    action, mentions, _, reason, artifact = _unwrap_envelope({
        "action": "silent", "reason": "等 Outline 就绪", "mentions": [],
    })
    assert action == "silent"
    assert mentions == []
    assert reason == "等 Outline 就绪"
    assert artifact == {}


def test_envelope_done():
    action, mentions, _, _, _ = _unwrap_envelope({"action": "done", "mentions": []})
    assert action == "done"
    assert mentions == []


def test_legacy_format_whole_is_artifact():
    """无 action → 旧格式:整体当 artifact,action 推断 speak,mentions 取 handoff.to。"""
    parsed = {"script_md": "稿子", "slides": [1, 2], "handoff": {"to": "html-designer"}}
    action, mentions, intent, reason, artifact = _unwrap_envelope(parsed)
    assert action == "speak"
    assert mentions == ["html-designer"]
    assert artifact == parsed                       # 整体即 artifact(零回归关键)


def test_legacy_format_handoff_done_no_mentions():
    action, mentions, *_ = _unwrap_envelope({"x": 1, "handoff": {"to": "DONE"}})
    assert action == "speak"
    assert mentions == []                            # DONE/coordinator 不进 mentions


def test_envelope_artifact_non_dict_becomes_empty():
    _, _, _, _, artifact = _unwrap_envelope({"action": "speak", "artifact": "oops"})
    assert artifact == {}


def test_envelope_illegal_action_falls_back_speak():
    action, *_ = _unwrap_envelope({"action": "shout", "artifact": {}})
    assert action == "speak"


def test_none_returns_safe_default():
    assert _unwrap_envelope(None) == ("speak", [], "propose", "", {})


def test_non_dict_returns_safe_default():
    assert _unwrap_envelope("not a dict") == ("speak", [], "propose", "", {})


def test_envelope_mentions_non_list_coerced():
    _, mentions, *_ = _unwrap_envelope({"action": "speak", "mentions": "copywriter"})
    assert mentions == []                            # 非 list → []
