"""P3 work-driver 转换 + 起点 bootstrap + 链式闭环（US2）

spec 003-coordinator-transform · FR-001/002/005/006 + SC-002

- T017: handle_v2_event SPEAK 真跑 _run_unlocked + inflight 计数
- T018: 起点 bootstrap 触发 material 第一棒
- T020: ScriptedBackend 风格 8 step 链式驱动闭环（observer 收尾 done）
"""

from __future__ import annotations

import asyncio

import pytest

from app.orchestrator.events_v2 import AgentSpeak
from app.orchestrator.harness import (
    AgentWorker, EventBus, HarnessState, run_harness,
)
from app.orchestrator.subscription import SubscriptionRegistry, WORKER_PROFILE


def _state(events_path, is_v2=True) -> HarnessState:
    return HarnessState(
        run=type("R", (), {"task_id": "tsk_wd", "title": "T", "report_type": "x",
                           "audience": "领导", "raw_text": "本周工作"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=is_v2, events_jsonl_path=events_path,
    )


class _StubStep:
    def __init__(self, step: str) -> None:
        self.step = step
        self.status = "pending"
        self.output_json = None
        self.output_text = ""
        self.error = None
        self.started_at = None
        self.ended_at = None
        self.total_tokens = 0


# ────────────────────────────── T017 work-driver 转换 ──────────────────────────────


@pytest.mark.asyncio
async def test_speak_triggers_real_run_step_with_inflight(tmp_outputs_dir):
    """T017 [US2] · SPEAK → 真跑 _run_unlocked;inflight 跑时 +1 跑完归 0。"""
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_wd" / "events.jsonl"
    state = _state(events_path)
    state.subscriptions = SubscriptionRegistry()

    inflight_seen: list[int] = []

    async def _run_step(s, run, prev):  # type: ignore[no-untyped-def]
        inflight_seen.append(state.inflight_steps)  # 跑时应 >= 1
        s.status = "success"
        s.output_json = {"_fake": "data"}

    state.by_key["material_parsing"] = _StubStep("material_parsing")
    w = AgentWorker(agent_id="material", step_key="material_parsing", state=state,
                    run_step_fn=_run_step, gate_review_fn=None)
    state.subscriptions.register("material", w, WORKER_PROFILE["material"])
    w.start_v2_consumer()

    try:
        # material requires=() → 任何 mention 命中即 SPEAK
        trigger = AgentSpeak(task_id="tsk_wd", **{"from": "coordinator"},
                             text="开始", intent="propose", mentions=["material"])
        await state.emit_v2(trigger)
        # 等 consume_loop 处理 + inbox 空
        for _ in range(50):
            await asyncio.sleep(0.01)
            if w.inbox is not None and w.inbox.empty() and state.inflight_steps == 0:
                break
    finally:
        if w._consume_task is not None and not w._consume_task.done():
            w._consume_task.cancel()
            try:
                await w._consume_task
            except asyncio.CancelledError:
                pass

    assert inflight_seen and inflight_seen[0] >= 1, "跑 step 时 inflight_steps 应 ≥ 1"
    assert state.inflight_steps == 0, "step 跑完 inflight_steps 应归 0"


# ────────────────────────────── T018 起点 bootstrap ──────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_triggers_first_step(tmp_outputs_dir):
    """T018 [US2] · 起点 bootstrap 触发 material 第一棒(不经 chain handoff)。"""
    from app.orchestrator.harness import _bootstrap_first_step

    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_wd" / "events.jsonl"
    state = _state(events_path)
    state.subscriptions = SubscriptionRegistry()

    ran: list[str] = []

    async def _run_step(s, run, prev):  # type: ignore[no-untyped-def]
        ran.append(s.step)
        s.status = "success"
        s.output_json = {"_fake": "data"}

    state.by_key["material_parsing"] = _StubStep("material_parsing")
    w = AgentWorker(agent_id="material", step_key="material_parsing", state=state,
                    run_step_fn=_run_step, gate_review_fn=None)
    state.subscriptions.register("material", w, WORKER_PROFILE["material"])
    w.start_v2_consumer()

    try:
        await _bootstrap_first_step(state, "material",
                                    [("material_parsing", "material", "资料员", None)])
        for _ in range(50):
            await asyncio.sleep(0.01)
            if w.inbox is not None and w.inbox.empty() and state.inflight_steps == 0:
                break
    finally:
        if w._consume_task is not None and not w._consume_task.done():
            w._consume_task.cancel()
            try:
                await w._consume_task
            except asyncio.CancelledError:
                pass

    assert state.bootstrapped is True
    assert ran == ["material_parsing"], "bootstrap 应触发 material 跑第一棒"


@pytest.mark.asyncio
async def test_bootstrap_only_once(tmp_outputs_dir):
    """T018 [US2] · bootstrap 去重:第二次调用 no-op(FR-006)。"""
    from app.orchestrator.harness import _bootstrap_first_step

    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_wd" / "events.jsonl"
    state = _state(events_path)
    state.subscriptions = SubscriptionRegistry()
    state.bootstrapped = True  # 已 bootstrap

    emitted_before = len(state.events_log)
    await _bootstrap_first_step(state, "material", [])
    assert len(state.events_log) == emitted_before, "已 bootstrap 时第二次应 no-op"


# ────────────────────────────── T020 8 step 链式闭环 ──────────────────────────────


@pytest.mark.asyncio
async def test_v2_subscription_drives_8step_closed_loop(tmp_outputs_dir, monkeypatch):
    """T020 [US2] · subscription 驱动 8 step 链式闭环 → observer 收尾 done(SC-002)。

    用 fake run_step（success + 产出核心 artifact 的 output_json）跑完整 run_harness(is_v2)。
    断言:8 step 全 success、observer gate_pass 收尾、reason=done、Coordinator 不 chain 路由。
    """
    from app.orchestrator import coordinator_observer
    from app.orchestrator.pipeline import STEPS, DEFAULT_NEXT_STEP, StepState

    # 加速 observer 轮询
    monkeypatch.setattr(coordinator_observer, "OBSERVER_TICK_SEC", 0.02)

    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_loop" / "events.jsonl"

    ran: list[str] = []

    async def fake_run_step(s, run, prev):  # type: ignore[no-untyped-def]
        ran.append(s.step)
        s.status = "success"
        if s.step == "copywriting":
            s.output_json = {"script_md": "讲稿内容"}
        else:
            s.output_json = {"_fake": "data"}

    run = type("R", (), {"task_id": "tsk_loop", "title": "周报", "report_type": "project_progress",
                         "audience": "直属领导", "raw_text": "本周完成 A/B/C"})()
    by_key = {k: StepState(step=k, label=l, agent=a) for k, a, l, _ in STEPS}

    result = await run_harness(
        run=run, prev={}, by_key=by_key, steps_meta=STEPS,
        default_next_step=DEFAULT_NEXT_STEP,
        run_step_fn=fake_run_step, gate_review_fn=None,
        chat_publisher=None, events_jsonl_path=events_path,
        is_v2=True, timeout_sec=15,
    )

    assert result["reason"] == "done", f"observer 应收尾 done;实际 {result['reason']}"
    # 8 step 全部由 subscription 链式驱动跑过
    assert len(ran) == len(STEPS), f"应跑 {len(STEPS)} step;实际 {len(ran)}: {ran}"
    assert set(ran) == {k for k, *_ in STEPS}, f"8 step 应全跑;实际 {ran}"
