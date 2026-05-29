"""v2 群聊订阅分发 · 闸门 · per-agent 锁(P2, spec 002-worker-subscription)

模块定位(详 specs/002-worker-subscription/research.md §0):
- P2 阶段 subscription = **chat overlay**;不重跑 _run_step
- Coordinator 仍是 LLM 工作的唯一驱动(v1 路径)
- 本模块只负责把 v2 事件按谓词分发给感兴趣的 worker,worker 自己跑闸门

设计要点:
- 谓词:Callable[[V2EventBase, str], bool] + 3 个预制 helper
- 闸门:决定性规则(spec FR-006 4 条),不调 LLM(decide_to_speak 在 Phase 4 落地)
- 锁:per-agent asyncio.Lock,守住 OpenClaw agentDir 不共享(宪章原则 V)
- v1 路径:本模块的 SubscriptionRegistry / agent_locks 根本不构造,零开销(FR-003)

配置(env var override,详 research.md §7):
  V2_MENTION_LIMIT  默认 2  · 同任务被 @ 次数上限
  V2_LOCK_WAIT_SEC  默认 60 · per-agent lock 等待超时(秒)
  V2_INBOX_MAX      默认 32 · worker inbox 队列容量
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

from .events_v2 import (
    AgentSpeak,
    ArtifactUpdate,
    CoordinatorIntervene,
    V2EventBase,
)

if TYPE_CHECKING:
    from .harness import AgentWorker

log = logging.getLogger("orchestrator.subscription")

__all__ = [
    "V2_MENTION_LIMIT",
    "V2_LOCK_WAIT_SEC",
    "V2_INBOX_MAX",
    "Predicate",
    "mention_includes",
    "hint_agent_is",
    "artifact_id_in",
    "WorkerProfile",
    "WORKER_PROFILE",
    "DecisionResult",
    "MentionCounter",
    "ReplyToRegistry",
    "SubscriptionRegistry",
    "decide_to_speak",
]


# ────────────────────────────── 配置常量 ──────────────────────────────
# T003 · spec FR-006/009/017 默认值,可通过 env var 覆盖(研发 / 演示用)

V2_MENTION_LIMIT: int = int(os.environ.get("V2_MENTION_LIMIT", "2"))
V2_LOCK_WAIT_SEC: float = float(os.environ.get("V2_LOCK_WAIT_SEC", "60"))
V2_INBOX_MAX: int = int(os.environ.get("V2_INBOX_MAX", "32"))


# ────────────────────────────── 谓词 ──────────────────────────────
# T004 · 3 个 helper 覆盖 9 个 agent 的所有订阅需求;新增谓词请就地写工厂函数

Predicate = Callable[[V2EventBase, str], bool]
"""(event, self_agent_id) → True 表示"该 worker 对该事件感兴趣"。

约定:
  - 非匹配 event 类型一律返回 False(不抛错),由 isinstance 守护
  - 谓词内部禁止做 IO / 调 LLM(同步、轻量)
"""


def mention_includes(self_id: str) -> Predicate:
    """工厂:返回判断 AgentSpeak.mentions 含 self_id 的谓词。"""
    def _p(event: V2EventBase, _: str) -> bool:
        return isinstance(event, AgentSpeak) and self_id in (event.mentions or [])
    return _p


def hint_agent_is(self_id: str) -> Predicate:
    """工厂:返回判断 CoordinatorIntervene.hint_agent == self_id 的谓词。"""
    def _p(event: V2EventBase, _: str) -> bool:
        return (
            isinstance(event, CoordinatorIntervene)
            and event.hint_agent == self_id
        )
    return _p


def artifact_id_in(artifact_ids: set[str]) -> Predicate:
    """工厂:返回判断 ArtifactUpdate.id ∈ artifact_ids 的谓词。"""
    def _p(event: V2EventBase, _: str) -> bool:
        return isinstance(event, ArtifactUpdate) and event.id in artifact_ids
    return _p


# ────────────────────────────── WorkerProfile + 9 agent 注册表 ──────────────────────────────
# T005 · WorkerProfile frozen dataclass
# T006 · WORKER_PROFILE 静态注册表;run_harness 启动时按 agent_id 注册到 SubscriptionRegistry

@dataclass(frozen=True, slots=True)
class WorkerProfile:
    """单个 worker 的订阅声明 + artifact 依赖。任务内不可变。

    interests: 谓词元组,**any-of 语义** — 任一谓词返回 True 即感兴趣
    requires:  4 核心 artifact 子集(MaterialPool / ReportCore / Outline / Script);
               空元组表示"无依赖,总能 speak"
    """
    interests: tuple[Predicate, ...]
    requires: tuple[str, ...]


WORKER_PROFILE: dict[str, WorkerProfile] = {
    "material": WorkerProfile(
        interests=(
            mention_includes("material"),
            hint_agent_is("material"),
        ),
        requires=(),
    ),
    "point-extractor": WorkerProfile(
        interests=(
            mention_includes("point-extractor"),
            hint_agent_is("point-extractor"),
            artifact_id_in({"MaterialPool"}),
        ),
        requires=("MaterialPool",),
    ),
    "structure": WorkerProfile(
        interests=(
            mention_includes("structure"),
            artifact_id_in({"ReportCore"}),
        ),
        requires=("ReportCore",),
    ),
    "upward-opt": WorkerProfile(
        interests=(
            mention_includes("upward-opt"),
            artifact_id_in({"ReportCore"}),
        ),
        requires=("ReportCore",),
    ),
    "copywriter": WorkerProfile(
        interests=(
            mention_includes("copywriter"),
            artifact_id_in({"ReportCore", "Outline"}),
        ),
        requires=("ReportCore", "Outline"),
    ),
    "html-designer": WorkerProfile(
        interests=(
            mention_includes("html-designer"),
            artifact_id_in({"Script"}),
        ),
        requires=("Script",),
    ),
    "video-producer": WorkerProfile(
        interests=(
            mention_includes("video-producer"),
            artifact_id_in({"Script"}),
        ),
        requires=("Script",),
    ),
    "reviewer": WorkerProfile(
        interests=(
            mention_includes("reviewer"),
            artifact_id_in({"MaterialPool", "ReportCore", "Outline", "Script"}),
        ),
        requires=(),  # 任何 artifact 出来都能发声;不强制依赖
    ),
    "coordinator": WorkerProfile(
        interests=(),  # P2 不订阅 — observer 行为留给 P3
        requires=(),
    ),
}


# ────────────────────────────── 闸门返回值 ──────────────────────────────
# T007 · DecisionResult enum(spec FR-005/006)

class DecisionResult(Enum):
    """decide-to-speak 闸门的 3 个出口(FR-005/006)。"""
    SPEAK = "speak"    # emit AgentSpeak;bump mention_counter + mark reply_to
    SILENT = "silent"  # emit AgentSilent;bump + mark
    IGNORE = "ignore"  # 仅 log;不 emit,不 bump,不 mark


# ────────────────────────────── 任务级状态容器 ──────────────────────────────
# T008 · MentionCounter + ReplyToRegistry(FR-006 防 flap + 去重)

@dataclass
class MentionCounter:
    """任务内 agent_id → 被 @ 次数;用于 FR-006 防 flap(默认阈值 V2_MENTION_LIMIT=2)。"""
    _counts: dict[str, int] = field(default_factory=dict)

    def bump(self, agent_id: str) -> int:
        self._counts[agent_id] = self._counts.get(agent_id, 0) + 1
        return self._counts[agent_id]

    def get(self, agent_id: str) -> int:
        return self._counts.get(agent_id, 0)


@dataclass
class ReplyToRegistry:
    """任务内 (agent_id, reply_to_message_id) → 已响应过;用于 FR-006 去重。"""
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def has_responded(self, agent_id: str, reply_to: str | None) -> bool:
        if reply_to is None:
            return False
        return (agent_id, reply_to) in self._seen

    def mark(self, agent_id: str, reply_to: str | None) -> None:
        if reply_to is not None:
            self._seen.add((agent_id, reply_to))


# ────────────────────────────── SubscriptionRegistry ──────────────────────────────
# T009 · 任务级订阅注册表 + dispatch 占位
# is_v2=True 时由 run_harness 构造一次,挂在 HarnessState.subscriptions
# dispatch() 完整实现由 Phase 4 US2 的 T024 落地

@dataclass
class SubscriptionRegistry:
    """任务级订阅注册表 + dispatch。

    职责:
      - register(): 把 worker 和 profile 挂到注册表(run_harness 启动时调)
      - dispatch(): 同步分发 — 遍历 profiles,对感兴趣的 worker 调 enqueue_v2
      - mention_counter / reply_to_registry: 闸门用的任务级计数器(给 decide_to_speak)

    is_v2=True 时由 run_harness 构造一次(详 research.md §6)。
    v1 路径下 SubscriptionRegistry **根本不构造**,零开销(FR-003)。
    """
    workers: dict[str, "AgentWorker"] = field(default_factory=dict)
    profiles: dict[str, WorkerProfile] = field(default_factory=dict)
    mention_counter: MentionCounter = field(default_factory=MentionCounter)
    reply_to_registry: ReplyToRegistry = field(default_factory=ReplyToRegistry)

    def register(
        self,
        agent_id: str,
        worker: "AgentWorker",
        profile: WorkerProfile,
    ) -> None:
        """注册一个 worker;空 interests 跳过(如 coordinator)。"""
        if not profile.interests:
            return
        self.workers[agent_id] = worker
        self.profiles[agent_id] = profile

    def dispatch(self, event: V2EventBase) -> int:
        """同步分发;返回成功入队的 worker 数(T024)。

        对每个注册 worker 跑 any-of 谓词;命中即 worker.enqueue_v2(event)。
        谓词异常按 FR-016 降级 — 仅 log.warning,不抛错,不影响其他 worker。
        """
        delivered = 0
        for agent_id, profile in self.profiles.items():
            try:
                if any(p(event, agent_id) for p in profile.interests):
                    worker = self.workers.get(agent_id)
                    if worker is not None and worker.enqueue_v2(event):
                        delivered += 1
            except Exception as e:  # noqa: BLE001 — FR-016 降级
                log.warning(
                    "subscription dispatch crash for %s on msg_type=%s: %s",
                    agent_id, getattr(event, "msg_type", "?"), e,
                )
        return delivered


# ────────────────────────────── decide-to-speak 闸门 ──────────────────────────────
# T023 · spec FR-005/006 4 条规则;纯函数便于单测


def decide_to_speak(
    *,
    event: V2EventBase,
    agent_id: str,
    profile: WorkerProfile,
    mention_counter: MentionCounter,
    reply_to_registry: ReplyToRegistry,
    available_artifacts: dict[str, int],
    mention_limit: int = V2_MENTION_LIMIT,
) -> tuple[DecisionResult, str]:
    """decide-to-speak 闸门(spec FR-005/006)。

    规则按优先级判定;返回 (decision, reason_or_text_seed)。

    1. 若 worker 已对**同一 reply_to** 响应过 → IGNORE
    2. 若 worker 当前任务被 @ 次数 ≥ mention_limit → IGNORE(防 flap)
    3. 若 profile.requires 全部满足(每个 artifact_id 都 ∈ available_artifacts)→ SPEAK
    4. 否则(有 require 缺失)→ SILENT(reason 含缺失 artifact 列表)

    **纯函数特性**:所有依赖经参数传入,无 IO / 无 LLM / 无副作用;
    便于单测直接 (decision, reason) 断言(不需要 mock state / async)。
    """
    # 1. reply_to dedup 优先于其他规则(避免双重 IGNORE 计数失真)
    reply_to = getattr(event, "reply_to", None)
    if reply_to_registry.has_responded(agent_id, reply_to):
        return DecisionResult.IGNORE, f"已响应同一 reply_to={reply_to}"

    # 2. 防 flap
    if mention_counter.get(agent_id) >= mention_limit:
        return DecisionResult.IGNORE, f"被 @ 次数已达 {mention_limit}"

    # 3 / 4. requires 检查
    missing = [a for a in profile.requires if a not in available_artifacts]
    if missing:
        return DecisionResult.SILENT, f"等 {' / '.join(missing)} 先就绪"

    return DecisionResult.SPEAK, "依赖齐全,准备发言"
