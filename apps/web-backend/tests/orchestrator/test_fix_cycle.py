"""P4 verdict.fail 触发修复闭环（US4）

spec 004-reviewer-dual-track · FR-009~013 + SC-004

observer._on_verdict:监听 reviewer.verdict(fail)→ 转写 intervene + 重置 step needs_fix +
force_run_v2 修复 + REVIEW_FIX_MAX_RETRY 上限。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.orchestrator.coordinator_observer import CoordinatorObserver
from app.orchestrator.harness import AgentWorker, EventBus, HarnessState


def _state(events_path, task_id="tsk_f") -> HarnessState:
    st = HarnessState(
        run=type("R", (), {"task_id": task_id})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True, events_jsonl_path=events_path,
    )
    st.done = asyncio.get_running_loop().create_future()
    st.bootstrapped = True
    return st


class _Step:
    def __init__(self, step):
        self.step = step
        self.status = "success"
        self.output_json = {"x": 1}
        self.error = None
        self.started_at = self.ended_at = None
        self.total_tokens = 0


def _verdict_event(fix_agent, verdict="fail"):
    """构造一个 reviewer.verdict 的 AgentEvent payload(bus.emit 形态)。"""
    from app.orchestrator.harness import AgentEvent
    return AgentEvent(kind="reviewer.verdict", agent_id="reviewer", step_key=None,
                      payload={"verdict": verdict, "suggested_fix_agent": fix_agent})


def _make_worker(state, agent_id, step_key):
    ran = []

    async def _run_step(s, run, prev):  # type: ignore[no-untyped-def]
        ran.append(s.step)
        s.status = "success"
        s.output_json = {"x": 1}
    state.by_key[step_key] = _Step(step_key)
    w = AgentWorker(agent_id=agent_id, step_key=step_key, state=state,
                    run_step_fn=_run_step, gate_review_fn=None)
    return w, ran


def _rows(p):
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ────────────────────────────── T028 fail → 修复 ──────────────────────────────


@pytest.mark.asyncio
async def test_verdict_fail_triggers_fix(tmp_outputs_dir):
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_f" / "events.jsonl"
    state = _state(events)
    w, ran = _make_worker(state, "material", "material_parsing")
    obs = CoordinatorObserver(state=state, workers={"material": w}, goal="g")

    await obs._on_verdict(_verdict_event("material"))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if state.inflight_steps == 0 and ran:
            break

    assert obs._fix_retries.get("material") == 1
    assert state.by_key["material_parsing"].status in ("success", "needs_fix")  # 重置后又跑成功
    assert ran == ["material_parsing"], "fix_agent 应被 force_run_v2 重激活修复"
    # 转写 intervene 点名(Reviewer 不直接 @)
    assert any(r.get("msg_type") == "coordinator.intervene" for r in _rows(events))


# ────────────────────────────── T029 上限 ──────────────────────────────


@pytest.mark.asyncio
async def test_fix_retry_limit(tmp_outputs_dir, monkeypatch):
    from app.orchestrator import coordinator_observer
    monkeypatch.setattr(coordinator_observer, "REVIEW_FIX_MAX_RETRY", 2)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_f" / "events.jsonl"
    state = _state(events)
    w, ran = _make_worker(state, "material", "material_parsing")
    obs = CoordinatorObserver(state=state, workers={"material": w}, goal="g")

    await obs._on_verdict(_verdict_event("material"))  # retry 1
    await asyncio.sleep(0.05)
    await obs._on_verdict(_verdict_event("material"))  # retry 2
    await asyncio.sleep(0.05)
    await obs._on_verdict(_verdict_event("material"))  # 已达上限,不再修
    await asyncio.sleep(0.05)

    assert obs._fix_retries["material"] == 2, "达上限后不再 bump(FR-011)"


# ────────────────────────────── T030 fix_agent 缺失 ──────────────────────────────


@pytest.mark.asyncio
async def test_fix_agent_missing_no_op(tmp_outputs_dir):
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_f" / "events.jsonl"
    state = _state(events)
    w, ran = _make_worker(state, "material", "material_parsing")
    obs = CoordinatorObserver(state=state, workers={"material": w}, goal="g")

    await obs._on_verdict(_verdict_event(None))     # suggested_fix_agent=None
    await obs._on_verdict(_verdict_event("ghost"))  # 无效 agent
    await asyncio.sleep(0.05)

    assert obs._fix_retries == {}, "fix_agent 缺失/无效不触发修复(FR-013)"
    assert ran == []


# ────────────────────────────── T031 pass 不修 ──────────────────────────────


@pytest.mark.asyncio
async def test_verdict_pass_no_fix(tmp_outputs_dir):
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_f" / "events.jsonl"
    state = _state(events)
    w, ran = _make_worker(state, "material", "material_parsing")
    obs = CoordinatorObserver(state=state, workers={"material": w}, goal="g")

    await obs._on_verdict(_verdict_event("material", verdict="pass"))
    await asyncio.sleep(0.05)

    assert obs._fix_retries == {}
    assert ran == []
