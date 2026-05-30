"""P2 per-agent 串行锁单测 · spec 002-worker-subscription · Phase 5 US3

测试覆盖(T030-T032):
- 同 agent_id 两次 async with lock → 串行(不重叠)
- 不同 agent_id → 不同 Lock 实例,可并发
- handle_v2_event 锁等待超时 → emit AgentSilent("锁等待超时") 不抛错

补充测试(T035 间接验证):
- v1 路径(is_v2=False)下 run() 不走 lock,行为与 main 一致
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


def _state(is_v2: bool = True) -> HarnessState:
    return HarnessState(
        run=type("R", (), {"task_id": "tsk_lock"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=is_v2,
    )


# ────────────────────────────── T030 同 agent 串行 ──────────────────────────────


@pytest.mark.asyncio
async def test_same_agent_two_holders_serial():
    """同 agent_id 两次 async with lock → 严格串行(不重叠)。"""
    state = _state()
    lock = state.get_agent_lock("material")

    order: list[str] = []

    async def hold(name: str, dur: float):
        async with lock:
            order.append(f"{name}-acq")
            await asyncio.sleep(dur)
            order.append(f"{name}-rel")

    await asyncio.gather(hold("A", 0.05), hold("B", 0.01))
    # B 必须等 A 释放;不是 [A-acq, B-acq, ...] 这种交错
    assert order == ["A-acq", "A-rel", "B-acq", "B-rel"], f"实际 {order}"


@pytest.mark.asyncio
async def test_same_agent_id_returns_same_lock():
    """同 agent_id 多次调 get_agent_lock 必须返回同一个 Lock 实例(lazy-init 幂等)。"""
    state = _state()
    l1 = state.get_agent_lock("reviewer")
    l2 = state.get_agent_lock("reviewer")
    l3 = state.get_agent_lock("reviewer")
    assert l1 is l2 is l3


# ────────────────────────────── T031 不同 agent 并行 ──────────────────────────────


@pytest.mark.asyncio
async def test_different_agents_get_different_locks():
    """不同 agent_id → 不同 Lock 实例(可并发跑)。"""
    state = _state()
    lock_a = state.get_agent_lock("material")
    lock_b = state.get_agent_lock("reviewer")
    lock_c = state.get_agent_lock("html-designer")

    assert lock_a is not lock_b
    assert lock_a is not lock_c
    assert lock_b is not lock_c


@pytest.mark.asyncio
async def test_different_agents_can_run_concurrently():
    """不同 agent 持各自锁可并发(整体耗时 ≈ 单任务,而非串行总和)。"""
    state = _state()
    lock_a = state.get_agent_lock("material")
    lock_b = state.get_agent_lock("reviewer")

    order: list[str] = []

    async def hold(name: str, lock: asyncio.Lock):
        async with lock:
            order.append(f"{name}-acq")
            await asyncio.sleep(0.05)
            order.append(f"{name}-rel")

    t0 = asyncio.get_event_loop().time()
    await asyncio.gather(hold("A", lock_a), hold("B", lock_b))
    elapsed = asyncio.get_event_loop().time() - t0

    # 串行情况下应 ≥ 0.10s(2×0.05);并发情况下 ≈ 0.05~0.08s
    assert elapsed < 0.09, f"不同 agent 应并发跑;实际耗时 {elapsed:.3f}s"
    # acq 顺序无关紧要,只要两个 rel 都出现
    assert sum("acq" in o for o in order) == 2
    assert sum("rel" in o for o in order) == 2


# ────────────────────────────── T032 锁等待超时降级 ──────────────────────────────


@pytest.mark.asyncio
async def test_handle_v2_event_lock_wait_timeout_emits_silent(tmp_outputs_dir, monkeypatch):
    """V2_LOCK_WAIT_SEC 内拿不到锁 → handle_v2_event 走 silent 降级(FR-009)。

    模拟:让 holder 持锁 0.5s,把 V2_LOCK_WAIT_SEC 临时改为 0.05s,
    第二条事件进 handle_v2_event 时拿不到锁 → 应 emit AgentSilent("锁等待超时")。
    """
    # monkeypatch 模块级常量(用 setattr 而非 setenv,避免重载模块)
    import app.orchestrator.harness as harness_mod

    task_id = "tsk_lock_timeout"
    events_path = tmp_outputs_dir / "data" / "outputs" / task_id / "events.jsonl"

    state = HarnessState(
        run=type("R", (), {"task_id": task_id})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True,
        events_jsonl_path=events_path,
    )
    state.subscriptions = SubscriptionRegistry()

    async def _noop(*a, **kw):  # type: ignore[no-untyped-def]
        return None
    # P4:用 material(非 reviewer)—— reviewer 在 v2 被特化分支拦截(不走锁逻辑);
    # 锁超时降级 silent 适用普通 work-driver agent。
    w = AgentWorker(
        agent_id="material", step_key="material_parsing", state=state,
        run_step_fn=_noop, gate_review_fn=None,
    )
    state.subscriptions.register("material", w, WORKER_PROFILE["material"])

    # 故意把超时阈值缩到 0.05s
    import app.orchestrator.subscription as sub_mod
    monkeypatch.setattr(sub_mod, "V2_LOCK_WAIT_SEC", 0.05)

    # holder 先抢锁,持 0.5s
    lock = state.get_agent_lock("material")
    holder_acquired = asyncio.Event()

    async def holder():
        async with lock:
            holder_acquired.set()
            await asyncio.sleep(0.5)

    holder_task = asyncio.create_task(holder())
    await holder_acquired.wait()

    # 触发一条 mentions=["material"] 的 speak → 走 handle_v2_event 拿锁失败 → silent
    trigger = AgentSpeak(
        task_id=task_id, **{"from": "x"},
        text="@material", intent="ask",
        mentions=["material"],
    )
    await w.handle_v2_event(trigger)

    # 等 holder 跑完释放
    await holder_task

    rows = [
        json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    silents = [
        r for r in rows
        if r.get("msg_type") == "agent.silent" and r.get("from") == "material"
    ]
    assert len(silents) >= 1, f"超时应 emit AgentSilent;实际 rows={rows}"
    assert any("超时" in s.get("reason", "") for s in silents), (
        f"silent reason 应含'超时';实际 silents={silents}"
    )


# ────────────────────────────── T035 v1 路径 run() 不走 lock ──────────────────────────────


@pytest.mark.asyncio
async def test_v1_run_does_not_use_agent_lock():
    """v1 路径下 AgentWorker.run() 不获取 lock(零开销 — FR-013)。

    通过反向断言:跑两个 v1 worker 同 agent_id,如果 run() 走 lock 会串行;
    不走 lock 则不会创建 agent_locks 字典条目。
    """
    state = _state(is_v2=False)

    # 不构造 by_key,run() 第一行 if s is None 会快速 emit failed 退出
    # 关键是它走的 v1 路径,不会触碰 get_agent_lock
    async def _noop(*a, **kw):  # type: ignore[no-untyped-def]
        return None
    w = AgentWorker(
        agent_id="material", step_key="material", state=state,
        run_step_fn=_noop, gate_review_fn=None,
    )

    state.done = asyncio.get_running_loop().create_future()
    await w.run()  # 应快速失败(by_key 没 step)但不应碰锁

    assert state.agent_locks == {}, (
        "v1 路径 run() 不应触碰 agent_locks(零开销红线 FR-013)"
    )
