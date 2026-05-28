"""v2 群聊事件 Pydantic 模型

详见 specs/001-v2-chat-protocol-state/data-model.md 与 contracts/*.schema.json。

5 类事件：
- AgentSpeak           (msg_type=agent.speak)
- AgentSilent          (msg_type=agent.silent)
- CoordinatorIntervene (msg_type=coordinator.intervene)
- ReviewerVerdict      (msg_type=reviewer.verdict)
- ArtifactUpdate       (msg_type=artifact.update)

宪章原则 IV 红线：ReviewerVerdict **没有** mentions/cc 字段（reviewer 不直接 @ 别人）；
宪章原则 III 降级：emit 前 schema 校验失败由调用方包装为 agent.failed，不抛到任务层。
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ids import new_message_id

__all__ = [
    "Intent",
    "InterveneKind",
    "Verdict",
    "VerdictDimension",
    "Severity",
    "ArtifactId",
    "V2Base",
    "V2EventBase",
    "ArtifactRef",
    "Finding",
    "AgentSpeak",
    "AgentSilent",
    "CoordinatorIntervene",
    "ReviewerVerdict",
    "ArtifactUpdate",
    "CORE_ARTIFACTS",
]

# ─────────────────────────────────────────────────────────────
# 枚举（与 contracts/*.schema.json 严格对齐）
# ─────────────────────────────────────────────────────────────

Intent = Literal["ask", "propose", "challenge", "confirm", "yield", "done"]
InterveneKind = Literal[
    "loop_detected", "stagnation", "drift", "budget", "gate_pass", "gate_reject"
]
Verdict = Literal["pass", "fail"]
VerdictDimension = Literal["quality", "process_logic", "both"]
Severity = Literal["high", "med", "low"]
ArtifactId = Literal["MaterialPool", "ReportCore", "Outline", "Script"]

# 4 核心 artifact 集合（artifacts_v2.write_versioned 入参校验复用）
CORE_ARTIFACTS: set[str] = {"MaterialPool", "ReportCore", "Outline", "Script"}


# ─────────────────────────────────────────────────────────────
# 基类
# ─────────────────────────────────────────────────────────────


class V2Base(BaseModel):
    """所有 v2 Pydantic 模型的共同配置。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        populate_by_name=True,  # 允许用 from_ 或 alias from 两种命名 init
    )


class V2EventBase(V2Base):
    """v2 事件公共字段。子类用 Literal 锁住 msg_type。"""

    msg_type: str
    message_id: str = Field(default_factory=new_message_id)
    task_id: str = Field(min_length=1)
    ts: float = Field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────
# 嵌套模型
# ─────────────────────────────────────────────────────────────


class ArtifactRef(V2Base):
    """AgentSpeak.artifact_updates 列表项：指向某一版核心 artifact。"""

    id: ArtifactId
    version: int = Field(ge=1)
    base_version: int | None = Field(default=None, ge=1)
    delta_summary: str = Field(default="", max_length=60)


class Finding(V2Base):
    """ReviewerVerdict.findings 列表项：单条问题。"""

    severity: Severity
    what: str = Field(min_length=1, max_length=200)
    where: str = Field(default="", max_length=80)


# ─────────────────────────────────────────────────────────────
# 5 类事件
# ─────────────────────────────────────────────────────────────


class AgentSpeak(V2EventBase):
    """agent.speak — agent 主动发言（带 mentions / cc / reply_to / intent / artifact_updates）。"""

    msg_type: Literal["agent.speak"] = "agent.speak"
    from_: str = Field(alias="from", min_length=1)
    text: str = Field(min_length=1, max_length=4000)
    mentions: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    reply_to: str | None = None
    intent: Intent
    artifact_updates: list[ArtifactRef] = Field(default_factory=list)


class AgentSilent(V2EventBase):
    """agent.silent — agent 被 @ 但没东西可说。"""

    msg_type: Literal["agent.silent"] = "agent.silent"
    from_: str = Field(alias="from", min_length=1)
    reply_to: str | None = None
    reason: str = Field(min_length=1, max_length=30)


class CoordinatorIntervene(V2EventBase):
    """coordinator.intervene — 流程纠偏发言（**不路由 next-speaker**，宪章原则 IV 红线）。"""

    msg_type: Literal["coordinator.intervene"] = "coordinator.intervene"
    kind: InterveneKind
    text: str = Field(min_length=1, max_length=200)
    hint_agent: str | None = None


class ReviewerVerdict(V2EventBase):
    """reviewer.verdict — 质量 + 流程逻辑双轨结论（**无 mentions/cc**，宪章原则 IV）。"""

    msg_type: Literal["reviewer.verdict"] = "reviewer.verdict"
    verdict: Verdict
    dimension: VerdictDimension
    findings: list[Finding] = Field(default_factory=list)
    suggested_fix_agent: str | None = None
    suggestions: list[str] = Field(min_length=3)


class ArtifactUpdate(V2EventBase):
    """artifact.update — 4 核心 artifact 写入后由 artifacts_v2 emit。"""

    msg_type: Literal["artifact.update"] = "artifact.update"
    id: ArtifactId
    version: int = Field(ge=1)
    base_version: int | None = Field(default=None, ge=1)
    producer: str = Field(min_length=1)
    delta_summary: str = Field(default="", max_length=60)
    ref: str = Field(
        pattern=r"^data/outputs/[^/]+/(material_pool|report_core|outline|script)_v\d+\.(json|md)$"
    )
