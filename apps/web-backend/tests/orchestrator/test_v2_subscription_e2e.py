"""P2 e2e 集成测试 · spec 002-worker-subscription · T022 [US2]

端到端验证:is_v2=True 状态下,手工 emit_v2 一条 AgentSpeak(mentions=[X]) →
X(point-extractor)通过订阅链路自动响应(SPEAK or SILENT)→ events.jsonl
出现带 reply_to 的响应事件,且 reply_to 精确指向原 speak 的 message_id。

本测试不跑完整 pipeline(成本太高且依赖 LLM mock);只跑 harness 层的
emit_v2 → dispatch → consume_loop → handle_v2_event → emit_v2(response)链路。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.orchestrator.events_v2 import AgentSpeak
from app.orchestrator.harness import AgentWorker, EventBus, HarnessState
from app.orchestrator.subscription import (
    SubscriptionRegistry,
    WORKER_PROFILE,
)


async def _drain_inboxes(workers: dict, timeout: float = 1.0) -> None:
    """等到所有 worker 的 inbox 清空(代表 consume_loop 都处理完了)。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if all(
            w.inbox is None or w.inbox.empty()
            for w in workers.values()
        ):
            # 多等一拍让 emit_v2 写完
            await asyncio.sleep(0.02)
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_mention_triggers_silent_when_artifact_missing(tmp_outputs_dir):
    """T022 · mention=["point-extractor"] → point-extractor 因 MaterialPool 缺失 emit AgentSilent。

    point-extractor profile.requires = ("MaterialPool",);
    任务初期没有 MaterialPool artifact → decide_to_speak → SILENT。
    """
    task_id = "tsk_p2_e2e"
    events_path = tmp_outputs_dir / "data" / "outputs" / task_id / "events.jsonl"

    state = HarnessState(
        run=type("R", (), {"task_id": task_id})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True,
        events_jsonl_path=events_path,
    )
    state.done = asyncio.get_running_loop().create_future()
    state.subscriptions = SubscriptionRegistry()

    # 手工构造并注册 point-extractor worker(走真实 start_v2_consumer)
    async def _noop_step(*a, **kw):  # type: ignore[no-untyped-def]
        return None
    w_pe = AgentWorker(
        agent_id="point-extractor", step_key="point-extractor",
        state=state, run_step_fn=_noop_step, gate_review_fn=None,
    )
    state.subscriptions.register("point-extractor", w_pe, WORKER_PROFILE["point-extractor"])
    w_pe.start_v2_consumer()

    try:
        # material agent 发一条 speak @ point-extractor
        trigger = AgentSpeak(
            task_id=task_id, **{"from": "material"},
            text="MaterialPool 我准备得差不多了,@ point-extractor 看看",
            intent="propose",
            mentions=["point-extractor"],
        )
        await state.emit_v2(trigger)

        # 等 consume_loop 把订阅事件处理完
        await _drain_inboxes({"pe": w_pe})
    finally:
        # 清理 consume_task
        if w_pe._consume_task is not None and not w_pe._consume_task.done():
            w_pe._consume_task.cancel()
            try:
                await w_pe._consume_task
            except asyncio.CancelledError:
                pass

    # 校验 events.jsonl:原 speak + 一条来自 point-extractor 的响应(silent)
    assert events_path.is_file(), "v2 路径应写 events.jsonl"
    rows = [
        json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) >= 2, f"应有 ≥ 2 条事件(trigger + 响应);实际 {len(rows)}"

    # 找 point-extractor 的响应事件
    pe_responses = [
        r for r in rows
        if r.get("from") == "point-extractor" and r.get("reply_to") == trigger.message_id
    ]
    assert len(pe_responses) == 1, (
        f"point-extractor 应自动响应 1 条;实际 {len(pe_responses)} 条;rows={rows}"
    )
    resp = pe_responses[0]
    assert resp["msg_type"] == "agent.silent", (
        f"MaterialPool 缺失时应 SILENT;实际 msg_type={resp['msg_type']}"
    )
    assert "MaterialPool" in resp.get("reason", ""), (
        f"silent reason 应含缺失 artifact 名;实际 reason={resp.get('reason')!r}"
    )


@pytest.mark.asyncio
async def test_mention_triggers_speak_when_requires_satisfied(tmp_outputs_dir):
    """mention=["html-designer"] + Script artifact 已就绪 → html-designer SPEAK。

    需要 monkeypatch artifacts_v2.next_version 让"Script 已 v1"。
    """
    from app.orchestrator import artifacts_v2

    task_id = "tsk_p2_e2e_speak"
    events_path = tmp_outputs_dir / "data" / "outputs" / task_id / "events.jsonl"

    # 真写一个 Script_v1.md 让 next_version("Script") 返回 2(即已有 v1)
    art_dir = tmp_outputs_dir / "data" / "outputs" / task_id
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "script_v1.md").write_text(
        "<!--__meta__: {\"version\": 1}-->\nfake script\n",
        encoding="utf-8",
    )

    state = HarnessState(
        run=type("R", (), {"task_id": task_id})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True,
        events_jsonl_path=events_path,
    )
    state.done = asyncio.get_running_loop().create_future()
    state.subscriptions = SubscriptionRegistry()

    async def _noop(*a, **kw):  # type: ignore[no-untyped-def]
        return None
    w_html = AgentWorker(
        agent_id="html-designer", step_key="html-designer",
        state=state, run_step_fn=_noop, gate_review_fn=None,
    )
    state.subscriptions.register("html-designer", w_html, WORKER_PROFILE["html-designer"])
    w_html.start_v2_consumer()

    try:
        trigger = AgentSpeak(
            task_id=task_id, **{"from": "copywriter"},
            text="讲稿写好了,@ html-designer 接着做",
            intent="propose",
            mentions=["html-designer"],
        )
        await state.emit_v2(trigger)
        await _drain_inboxes({"html": w_html})
    finally:
        if w_html._consume_task is not None and not w_html._consume_task.done():
            w_html._consume_task.cancel()
            try:
                await w_html._consume_task
            except asyncio.CancelledError:
                pass

    rows = [
        json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pe_responses = [
        r for r in rows
        if r.get("from") == "html-designer" and r.get("reply_to") == trigger.message_id
    ]
    assert len(pe_responses) == 1
    assert pe_responses[0]["msg_type"] == "agent.speak", (
        f"Script 已就绪时应 SPEAK;实际 {pe_responses[0]['msg_type']}"
    )
    assert pe_responses[0]["intent"] == "confirm"


@pytest.mark.asyncio
async def test_mention_unknown_agent_does_nothing(tmp_outputs_dir):
    """edge case:mentions=["unknown-agent"] 时订阅找不到 worker → 不影响任务。"""
    task_id = "tsk_p2_e2e_unknown"
    events_path = tmp_outputs_dir / "data" / "outputs" / task_id / "events.jsonl"

    state = HarnessState(
        run=type("R", (), {"task_id": task_id})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True,
        events_jsonl_path=events_path,
    )
    state.subscriptions = SubscriptionRegistry()
    # 不注册任何 worker

    trigger = AgentSpeak(
        task_id=task_id, **{"from": "material"},
        text="@ ghost-agent 在吗",
        intent="ask",
        mentions=["unknown-agent"],
    )
    # 不应抛错
    await state.emit_v2(trigger)

    # events.jsonl 只有 trigger 那一行;没有响应
    rows = [
        json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1, "未知 mention 不应产生额外事件"
    assert rows[0]["message_id"] == trigger.message_id
