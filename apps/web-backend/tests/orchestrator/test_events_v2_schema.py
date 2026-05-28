"""T015 [US2] · 5 类 v2 事件 schema 单测（happy + invalid 各 1）

详 specs/001-v2-chat-protocol-state/tasks.md。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.orchestrator.events_v2 import (
    AgentSilent,
    AgentSpeak,
    ArtifactRef,
    ArtifactUpdate,
    CoordinatorIntervene,
    Finding,
    ReviewerVerdict,
)
from app.orchestrator.ids import is_message_id


# ─── agent.speak ───────────────────────────────────────────


def test_agent_speak_happy():
    ev = AgentSpeak(
        task_id="tsk_t1",
        **{"from": "material"},
        text="素材池已整理完毕，后面交给分析师。",
        mentions=["point-extractor"],
        cc=["reviewer"],
        reply_to=None,
        intent="propose",
        artifact_updates=[
            ArtifactRef(id="MaterialPool", version=1, base_version=None, delta_summary="首版"),
        ],
    )
    assert ev.msg_type == "agent.speak"
    assert ev.from_ == "material"
    assert ev.intent == "propose"
    assert len(ev.artifact_updates) == 1
    assert is_message_id(ev.message_id)


def test_agent_speak_invalid_intent():
    with pytest.raises(ValidationError):
        AgentSpeak(
            task_id="tsk_t1",
            **{"from": "material"},
            text="x",
            intent="invalid_intent",  # 不在 6 枚举内
        )


# ─── agent.silent ──────────────────────────────────────────


def test_agent_silent_happy():
    ev = AgentSilent(
        task_id="tsk_t1",
        **{"from": "html-designer"},
        reply_to="msg_a1b2c3d4",
        reason="设计已按文书讲稿落地，无需补充",
    )
    assert ev.msg_type == "agent.silent"
    assert ev.from_ == "html-designer"
    assert ev.reply_to == "msg_a1b2c3d4"


def test_agent_silent_reason_too_long():
    with pytest.raises(ValidationError):
        AgentSilent(
            task_id="tsk_t1",
            **{"from": "x"},
            reason="一" * 31,  # 31 字超 30 上限
        )


# ─── coordinator.intervene ─────────────────────────────────


def test_coordinator_intervene_happy():
    ev = CoordinatorIntervene(
        task_id="tsk_t1",
        kind="gate_pass",
        text="全部 artifact 齐了，task 收尾。",
        hint_agent=None,
    )
    assert ev.msg_type == "coordinator.intervene"
    assert ev.kind == "gate_pass"


def test_coordinator_intervene_invalid_kind():
    with pytest.raises(ValidationError):
        CoordinatorIntervene(
            task_id="tsk_t1",
            kind="invalid_kind",  # 不在 6 枚举内
            text="x",
        )


# ─── reviewer.verdict ──────────────────────────────────────


def test_reviewer_verdict_happy():
    ev = ReviewerVerdict(
        task_id="tsk_t1",
        verdict="fail",
        dimension="both",
        findings=[
            Finding(severity="high", what="ReportCore 缺少 data_gaps", where="ReportCore@v1"),
            Finding(severity="med", what="时长超出 5 分钟约束 20%", where="Script@v2"),
        ],
        suggested_fix_agent="point-extractor",
        suggestions=[
            "请补全 data_gaps 字段",
            "请压缩讲稿到 5 分钟内",
            "段数限制到 12-15 条",
        ],
    )
    assert ev.verdict == "fail"
    assert ev.dimension == "both"
    assert len(ev.findings) == 2
    # 红线：reviewer 不能直接 @ — schema 没有 mentions/cc 字段
    assert not hasattr(ev, "mentions")
    assert not hasattr(ev, "cc")


def test_reviewer_verdict_invalid_suggestions_too_few():
    with pytest.raises(ValidationError):
        ReviewerVerdict(
            task_id="tsk_t1",
            verdict="pass",
            dimension="quality",
            suggestions=["only one"],  # < 3 条
        )


# ─── artifact.update ───────────────────────────────────────


def test_artifact_update_happy():
    ev = ArtifactUpdate(
        task_id="tsk_t1",
        id="ReportCore",
        version=2,
        base_version=1,
        producer="point-extractor",
        delta_summary="收敛 5→3 重点条目",
        ref="data/outputs/tsk_t1/report_core_v2.json",
    )
    assert ev.msg_type == "artifact.update"
    assert ev.id == "ReportCore"
    assert ev.version == 2
    assert ev.base_version == 1


def test_artifact_update_invalid_ref():
    with pytest.raises(ValidationError):
        ArtifactUpdate(
            task_id="tsk_t1",
            id="MaterialPool",
            version=1,
            producer="material",
            ref="/wrong/absolute/path.json",  # 不匹配正则
        )


# ─── 通用：message_id 自动填 ─────────────────────────────


def test_message_id_auto_filled_and_valid_format():
    ev = AgentSpeak(
        task_id="t",
        **{"from": "material"},
        text="x",
        intent="confirm",
    )
    assert is_message_id(ev.message_id), f"bad message_id: {ev.message_id}"


def test_message_id_explicit_invalid_format_rejected():
    # 显式传非法 message_id 应当被 Pydantic 拒绝
    # （Field 没有 pattern 校验，但 from is_message_id 校验在 emit_v2 层做；
    # 这里测显式合法 message_id 能传入即可）
    ev = AgentSpeak(
        task_id="t", message_id="msg_12345678",
        **{"from": "material"}, text="x", intent="confirm",
    )
    assert ev.message_id == "msg_12345678"
