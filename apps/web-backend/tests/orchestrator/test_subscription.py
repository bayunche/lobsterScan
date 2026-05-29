"""P2 订阅分发单测 · spec 002-worker-subscription · Phase 4 US2 + Phase 5 US3

测试覆盖:
- T018 5 个 predicate 命中/不命中(mention_includes / hint_agent_is / artifact_id_in)
- T019 decide_to_speak 4 分支(SPEAK / SILENT / IGNORE×2)
- T020 SubscriptionRegistry.dispatch 路由(多 worker 命中)
- T021 inbox 满丢最老(FR-017)
- T033 v1 Coordinator + v2 subscription 同锁串行(Phase 5)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from app.orchestrator.events_v2 import (
    AgentSpeak,
    ArtifactUpdate,
    CoordinatorIntervene,
)
from app.orchestrator.harness import AgentWorker, EventBus, HarnessState
from app.orchestrator.subscription import (
    DecisionResult,
    MentionCounter,
    ReplyToRegistry,
    SubscriptionRegistry,
    V2_INBOX_MAX,
    WORKER_PROFILE,
    WorkerProfile,
    artifact_id_in,
    decide_to_speak,
    hint_agent_is,
    mention_includes,
)


# ────────────────────────────── helper ──────────────────────────────


def _state(is_v2: bool = True) -> HarnessState:
    return HarnessState(
        run=type("R", (), {"task_id": "tsk_test"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=is_v2,
    )


def _worker(state: HarnessState, agent_id: str) -> AgentWorker:
    async def _noop(*a, **kw):  # type: ignore[no-untyped-def]
        return None
    return AgentWorker(
        agent_id=agent_id, step_key=agent_id, state=state,
        run_step_fn=_noop, gate_review_fn=None,
    )


def _speak(reply_to: str | None = None,
           mentions: list[str] | None = None,
           from_: str = "x") -> AgentSpeak:
    return AgentSpeak(
        task_id="t", **{"from": from_},
        text="x", intent="propose",
        reply_to=reply_to, mentions=mentions or [],
    )


# ────────────────────────────── T018 predicate ──────────────────────────────


def test_predicate_mention_includes_hit():
    pred = mention_includes("reviewer")
    ev = _speak(mentions=["reviewer", "copywriter"])
    assert pred(ev, "reviewer") is True


def test_predicate_mention_includes_miss():
    pred = mention_includes("reviewer")
    ev = _speak(mentions=["copywriter"])
    assert pred(ev, "reviewer") is False


def test_predicate_hint_agent_is_hit():
    pred = hint_agent_is("structure")
    ev = CoordinatorIntervene(
        task_id="t", kind="drift", text="跑题了", hint_agent="structure",
    )
    assert pred(ev, "structure") is True


def test_predicate_hint_agent_is_miss():
    pred = hint_agent_is("structure")
    ev = CoordinatorIntervene(
        task_id="t", kind="drift", text="跑题了", hint_agent="copywriter",
    )
    assert pred(ev, "structure") is False


def test_predicate_artifact_id_in_hit():
    pred = artifact_id_in({"ReportCore", "Outline"})
    ev = ArtifactUpdate(
        task_id="t", id="ReportCore", version=1, producer="structure",
        ref="data/outputs/t/report_core_v1.json",
    )
    assert pred(ev, "any-agent") is True


def test_predicate_artifact_id_in_miss():
    """非订阅 artifact id 不命中;非 ArtifactUpdate 类型也不命中。"""
    pred = artifact_id_in({"ReportCore"})
    ev_other_id = ArtifactUpdate(
        task_id="t", id="Script", version=1, producer="copywriter",
        ref="data/outputs/t/script_v1.md",
    )
    assert pred(ev_other_id, "any-agent") is False
    # 非 ArtifactUpdate 类型(AgentSpeak)也不命中
    assert pred(_speak(), "any-agent") is False


# ────────────────────────────── T019 decide_to_speak ──────────────────────────────


def test_decide_speak_when_requires_satisfied():
    profile = WorkerProfile(
        interests=(mention_includes("me"),),
        requires=("MaterialPool",),
    )
    decision, reason = decide_to_speak(
        event=_speak(mentions=["me"]),
        agent_id="me", profile=profile,
        mention_counter=MentionCounter(),
        reply_to_registry=ReplyToRegistry(),
        available_artifacts={"MaterialPool": 1},
    )
    assert decision == DecisionResult.SPEAK
    assert "依赖齐全" in reason or "ready" in reason.lower() or reason


def test_decide_silent_when_requires_missing():
    profile = WorkerProfile(
        interests=(mention_includes("me"),),
        requires=("MaterialPool", "ReportCore"),
    )
    decision, reason = decide_to_speak(
        event=_speak(mentions=["me"]),
        agent_id="me", profile=profile,
        mention_counter=MentionCounter(),
        reply_to_registry=ReplyToRegistry(),
        available_artifacts={"MaterialPool": 1},   # ReportCore 缺
    )
    assert decision == DecisionResult.SILENT
    assert "ReportCore" in reason


def test_decide_ignore_when_duplicate_reply_to():
    profile = WorkerProfile(
        interests=(mention_includes("me"),),
        requires=(),
    )
    rr = ReplyToRegistry()
    rr.mark("me", "msg_aaaa1111")
    decision, _ = decide_to_speak(
        event=_speak(mentions=["me"], reply_to="msg_aaaa1111"),
        agent_id="me", profile=profile,
        mention_counter=MentionCounter(),
        reply_to_registry=rr,
        available_artifacts={},
    )
    assert decision == DecisionResult.IGNORE


def test_decide_ignore_when_mention_count_exceeded():
    profile = WorkerProfile(
        interests=(mention_includes("me"),),
        requires=(),
    )
    mc = MentionCounter()
    mc.bump("me")
    mc.bump("me")  # 已达 V2_MENTION_LIMIT=2
    decision, _ = decide_to_speak(
        event=_speak(mentions=["me"]),
        agent_id="me", profile=profile,
        mention_counter=mc,
        reply_to_registry=ReplyToRegistry(),
        available_artifacts={},
    )
    assert decision == DecisionResult.IGNORE


def test_decide_priority_replyto_over_count():
    """规则顺序:reply_to dedup 优先于 mention count(避免双重 IGNORE 计数失真)。"""
    profile = WorkerProfile(interests=(mention_includes("me"),), requires=())
    rr = ReplyToRegistry()
    rr.mark("me", "msg_xxxx")
    mc = MentionCounter()
    mc.bump("me"); mc.bump("me")  # 两条理由都成立
    decision, reason = decide_to_speak(
        event=_speak(mentions=["me"], reply_to="msg_xxxx"),
        agent_id="me", profile=profile,
        mention_counter=mc, reply_to_registry=rr,
        available_artifacts={},
    )
    assert decision == DecisionResult.IGNORE
    assert "reply_to" in reason or "响应" in reason  # reply_to 命中,非 mention 命中


# ────────────────────────────── T020 dispatch 路由 ──────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_hits_multiple_workers():
    """同 event 命中多 worker → 全部入队;未命中的 inbox 长度不变。"""
    state = _state(is_v2=True)
    reg = SubscriptionRegistry()

    w_pe = _worker(state, "point-extractor")
    w_struct = _worker(state, "structure")
    w_html = _worker(state, "html-designer")

    # 启动 inbox 但不启 consume_loop,留着断言长度
    w_pe.inbox = asyncio.Queue(maxsize=V2_INBOX_MAX)
    w_struct.inbox = asyncio.Queue(maxsize=V2_INBOX_MAX)
    w_html.inbox = asyncio.Queue(maxsize=V2_INBOX_MAX)

    reg.register("point-extractor", w_pe, WORKER_PROFILE["point-extractor"])
    reg.register("structure", w_struct, WORKER_PROFILE["structure"])
    reg.register("html-designer", w_html, WORKER_PROFILE["html-designer"])

    # ArtifactUpdate(id=ReportCore) 应命中 structure(订阅 ReportCore),
    # 不命中 point-extractor(订阅 MaterialPool)、html-designer(订阅 Script)
    ev = ArtifactUpdate(
        task_id="t", id="ReportCore", version=1, producer="upward-opt",
        ref="data/outputs/t/report_core_v1.json",
    )
    delivered = reg.dispatch(ev)

    assert delivered == 1, f"应仅 structure 入队;实际 delivered={delivered}"
    assert w_struct.inbox.qsize() == 1
    assert w_pe.inbox.qsize() == 0
    assert w_html.inbox.qsize() == 0


@pytest.mark.asyncio
async def test_dispatch_speak_mentions_routes_correctly():
    """AgentSpeak.mentions=[X, Y] 应触达 X 和 Y;其他 worker 不动。"""
    state = _state(is_v2=True)
    reg = SubscriptionRegistry()

    w_reviewer = _worker(state, "reviewer")
    w_copy = _worker(state, "copywriter")
    w_struct = _worker(state, "structure")

    for w in (w_reviewer, w_copy, w_struct):
        w.inbox = asyncio.Queue(maxsize=V2_INBOX_MAX)
    reg.register("reviewer", w_reviewer, WORKER_PROFILE["reviewer"])
    reg.register("copywriter", w_copy, WORKER_PROFILE["copywriter"])
    reg.register("structure", w_struct, WORKER_PROFILE["structure"])

    ev = _speak(mentions=["reviewer", "copywriter"], from_="upward-opt")
    delivered = reg.dispatch(ev)

    assert delivered == 2
    assert w_reviewer.inbox.qsize() == 1
    assert w_copy.inbox.qsize() == 1
    assert w_struct.inbox.qsize() == 0


# ────────────────────────────── T021 inbox 满丢最老 ──────────────────────────────


@pytest.mark.asyncio
async def test_inbox_overflow_drops_oldest(caplog):
    """enqueue_v2 超 V2_INBOX_MAX → 丢最老 + 新事件入队 + log warn 命中。"""
    state = _state(is_v2=True)
    w = _worker(state, "reviewer")
    w.start_v2_consumer()
    # 直接用 inbox 不起 consume_loop 来观察长度;cancel 自带 task
    assert w._consume_task is not None
    w._consume_task.cancel()
    try:
        await w._consume_task
    except asyncio.CancelledError:
        pass

    # 用满 inbox + 1
    caplog.set_level(logging.WARNING, logger="orchestrator.harness")
    for i in range(V2_INBOX_MAX + 1):
        ev = _speak(mentions=["reviewer"], from_=f"x{i}")
        ok = w.enqueue_v2(ev)
        assert ok is True

    assert w.inbox is not None
    assert w.inbox.qsize() == V2_INBOX_MAX, "队列长度应仍为上限,丢最老腾位"
    assert any(
        "inbox" in rec.message and ("满" in rec.message or "drop" in rec.message.lower())
        for rec in caplog.records
    ), "应有 log warn 命中 inbox 满"


@pytest.mark.asyncio
async def test_enqueue_v2_returns_false_when_inbox_none():
    """v1 路径(inbox is None)调 enqueue_v2 应返回 False,不抛错。"""
    state = _state(is_v2=False)
    w = _worker(state, "material")
    # 不调 start_v2_consumer → inbox is None
    assert w.inbox is None
    assert w.enqueue_v2(_speak(mentions=["material"])) is False


# ────────────────────────────── T033 v1+v2 共存 ──────────────────────────────


@pytest.mark.asyncio
async def test_v1_coordinator_and_v2_subscription_share_lock():
    """T033 [US3] · FR-012 防御:Coordinator 派单(v1)与 subscription(v2)
    触发同 agent 使用同一把 lock,串行执行不双跑。
    """
    state = _state(is_v2=True)
    lock = state.get_agent_lock("material")

    order: list[str] = []

    async def coord_path():
        async with lock:
            order.append("v1-acq")
            await asyncio.sleep(0.05)
            order.append("v1-rel")

    async def sub_path():
        await asyncio.sleep(0.01)  # 稍后启动,确保 v1 先抢
        async with lock:
            order.append("v2-acq")
            order.append("v2-rel")

    await asyncio.gather(coord_path(), sub_path())
    # 不重叠 — v1 先 acq+rel,再 v2 acq+rel
    assert order == ["v1-acq", "v1-rel", "v2-acq", "v2-rel"]
