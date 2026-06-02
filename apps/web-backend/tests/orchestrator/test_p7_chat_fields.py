"""P7 后端 · chat.message additive 字段(spec 007-chat-ux）

T011/T015 · FR-001/002/003/004 · SC-005

_chat_msg 透传 mentions / silent_reason / artifact_delta;不传则不出现(零回归)。
"""

from __future__ import annotations

from app.orchestrator.pipeline import _chat_msg, ARTIFACT_DISPLAY


def test_chat_msg_legacy_no_additive_fields():
    """旧调用(不传新 kw)→ payload 不含 P7 字段(零回归 FR-004/SC-005)。"""
    m = _chat_msg("material", "素材就绪")
    assert set(m.keys()) == {"id", "agent", "display_name", "avatar", "ts", "kind", "text"}
    assert "mentions" not in m
    assert "silent_reason" not in m
    assert "artifact_delta" not in m
    assert m["kind"] == "result"


def test_chat_msg_silent_carries_reason():
    """kind=silent + silent_reason → payload 含(FR-002)。"""
    m = _chat_msg("structure", "", kind="silent", silent_reason="等大纲就绪")
    assert m["kind"] == "silent"
    assert m["silent_reason"] == "等大纲就绪"


def test_chat_msg_silent_empty_reason_still_field():
    """silent_reason 空串也写入(前端省略理由,不报错)。"""
    m = _chat_msg("structure", "", kind="silent", silent_reason="")
    assert "silent_reason" in m and m["silent_reason"] == ""


def test_chat_msg_artifact_delta():
    """artifact_delta(version≥2)→ payload 含,id 用中文友好名(FR-003/008)。"""
    delta = {"id": ARTIFACT_DISPLAY["Outline"], "version": 2, "summary": "补充风险章节"}
    m = _chat_msg("structure", "", artifact_delta=delta)
    assert m["artifact_delta"]["id"] == "大纲"
    assert m["artifact_delta"]["version"] == 2
    assert m["artifact_delta"]["summary"] == "补充风险章节"


def test_chat_msg_mentions():
    """mentions 非空 → payload 含(FR-001)。"""
    m = _chat_msg("copywriter", "讲稿好了", mentions=["html-designer", "video-producer"])
    assert m["mentions"] == ["html-designer", "video-producer"]


def test_chat_msg_empty_mentions_omitted():
    """mentions 空列表 → 不写入(additive,只在有内容时出现)。"""
    m = _chat_msg("copywriter", "x", mentions=[])
    assert "mentions" not in m


def test_artifact_display_covers_4_core():
    """4 核心 artifact 都有中文友好名(脱敏 FR-008)。"""
    assert ARTIFACT_DISPLAY == {
        "MaterialPool": "素材池", "ReportCore": "重点", "Outline": "大纲", "Script": "讲稿",
    }
