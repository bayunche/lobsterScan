"""P8 US1 · 预算硬上限 + 触顶软着陆(spec 008-ops-safety-net)

T011 · FR-006~012 + SC-002/003

- spent_tokens 随 step 累计;cap=0 短路;spent_tokens>=cap 触发 _on_budget_exceeded;
  gate 齐→done / 不齐→partial;budget_exceeded 后 force_run_v2 短路;只软着陆一次;
  emit intervene(kind=budget) 脱敏。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.orchestrator import coordinator_observer, subscription
from app.orchestrator.artifacts_v2 import write_versioned
from app.orchestrator.coordinator_observer import CoordinatorObserver
from app.orchestrator.harness import AgentWorker, EventBus, HarnessState
from app.orchestrator.subscription import SubscriptionRegistry


def _hstate(events_path, task_id="tsk_b") -> HarnessState:
    st = HarnessState(
        run=type("R", (), {"task_id": task_id, "title": "周报", "report_type": "x",
                           "audience": "领导", "raw_text": "本周", "duration": "3分钟"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True, events_jsonl_path=events_path,
    )
    st.done = asyncio.get_running_loop().create_future()
    st.bootstrapped = True
    st.subscriptions = SubscriptionRegistry()
    return st


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


async def _write_all_artifacts(state) -> None:
    for aid, prod in [("MaterialPool", "material"), ("ReportCore", "point-extractor"),
                      ("Outline", "structure"), ("Script", "copywriter")]:
        payload = "讲稿内容" if aid == "Script" else {"x": 1}
        await write_versioned(state=state, artifact_id=aid, payload=payload,
                              producer=prod, base_version=None, delta_summary="x")


def _rows(events_path):
    return [json.loads(l) for l in events_path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ────────────────────────── 检测开关 ──────────────────────────

@pytest.mark.asyncio
async def test_budget_cap_zero_no_detection(tmp_outputs_dir, monkeypatch):
    """cap=0(默认)→ 即使 spent_tokens 很大也不触顶(FR-007/SC-001)。"""
    monkeypatch.setattr(subscription, "V2_BUDGET_CAP", 0)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_b" / "events.jsonl"
    state = _hstate(events)
    state.spent_tokens = 10**9
    obs = CoordinatorObserver(state=state, workers={}, goal="g")
    assert obs._budget_exceeded_now() is False
    assert state.budget_exceeded is False


@pytest.mark.asyncio
async def test_budget_exceeded_now_at_cap(tmp_outputs_dir, monkeypatch):
    """spent_tokens>=cap → True;<cap → False(FR-008)。"""
    monkeypatch.setattr(subscription, "V2_BUDGET_CAP", 100)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_b" / "events.jsonl"
    state = _hstate(events)
    obs = CoordinatorObserver(state=state, workers={}, goal="g")

    state.spent_tokens = 99
    assert obs._budget_exceeded_now() is False
    state.spent_tokens = 100
    assert obs._budget_exceeded_now() is True
    state.spent_tokens = 150
    assert obs._budget_exceeded_now() is True


# ────────────────────────── 软着陆决策 ──────────────────────────

@pytest.mark.asyncio
async def test_budget_soft_land_partial_when_incomplete(tmp_outputs_dir, monkeypatch):
    """触顶 + 产物不齐 → partial,emit intervene(kind=budget)(FR-009)。"""
    monkeypatch.setattr(subscription, "V2_BUDGET_CAP", 100)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_b" / "events.jsonl"
    state = _hstate(events)
    state.spent_tokens = 120
    obs = CoordinatorObserver(state=state, workers={}, goal="g")

    await obs._on_budget_exceeded()

    assert state.budget_exceeded is True
    assert state.done.done() and state.done.result() == "partial"
    budget_rows = [r for r in _rows(events)
                   if r.get("msg_type") == "coordinator.intervene" and r.get("kind") == "budget"]
    assert len(budget_rows) == 1


@pytest.mark.asyncio
async def test_budget_soft_land_done_when_complete(tmp_outputs_dir, stub_state, monkeypatch):
    """触顶 + 4 核心 artifact 齐 → done(FR-009);已完成产物保留(FR-010,写过即在)。"""
    monkeypatch.setattr(subscription, "V2_BUDGET_CAP", 100)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_b" / "events.jsonl"
    state = _hstate(events)
    await _write_all_artifacts(stub_state("tsk_b", events))
    state.spent_tokens = 120
    obs = CoordinatorObserver(state=state, workers={}, goal="g")

    await obs._on_budget_exceeded()

    assert state.done.done() and state.done.result() == "done"


@pytest.mark.asyncio
async def test_budget_soft_land_once(tmp_outputs_dir, monkeypatch):
    """已触顶 → _budget_exceeded_now 返回 False(去重,只软着陆一次,FR-012)。"""
    monkeypatch.setattr(subscription, "V2_BUDGET_CAP", 100)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_b" / "events.jsonl"
    state = _hstate(events)
    state.spent_tokens = 200
    obs = CoordinatorObserver(state=state, workers={}, goal="g")

    assert obs._budget_exceeded_now() is True
    await obs._on_budget_exceeded()
    assert state.budget_exceeded is True
    assert obs._budget_exceeded_now() is False  # 不再重复触发


@pytest.mark.asyncio
async def test_budget_intervene_redacted(tmp_outputs_dir, monkeypatch):
    """budget 发声脱敏:不含 token 数 / task_id / agent_id(FR-005/SC-003)。"""
    monkeypatch.setattr(subscription, "V2_BUDGET_CAP", 100)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_b" / "events.jsonl"
    state = _hstate(events)
    state.spent_tokens = 12345
    obs = CoordinatorObserver(state=state, workers={}, goal="g")

    await obs._on_budget_exceeded()

    txt = [r for r in _rows(events) if r.get("kind") == "budget"][0]["text"]
    for banned in ("12345", "tsk_b", "token", "agent_id", "spent"):
        assert banned not in txt


# ────────────────────────── 派发短路 + 累计 ──────────────────────────

@pytest.mark.asyncio
async def test_force_run_v2_short_circuits_when_exceeded(tmp_outputs_dir):
    """budget_exceeded 后 force_run_v2 不启动新 turn;inflight 由 finally 配平(FR-008)。"""
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_b" / "events.jsonl"
    state = _hstate(events)
    state.budget_exceeded = True
    ran: list[str] = []

    async def _run_step(s, run, prev):  # type: ignore[no-untyped-def]
        ran.append(s.step)
        s.status = "success"

    state.by_key["material_parsing"] = _StubStep("material_parsing")
    w = AgentWorker(agent_id="material", step_key="material_parsing", state=state,
                    run_step_fn=_run_step, gate_review_fn=None)
    state.inflight_steps = 1  # 模拟 observer 激活前的 +1
    await w.force_run_v2()

    assert ran == [], "触顶后不应启动新 turn"
    assert state.inflight_steps == 0, "inflight 必须配平归零"


@pytest.mark.asyncio
async def test_spent_tokens_accumulates(tmp_outputs_dir):
    """_run_unlocked 跑完一个 step → spent_tokens 累加该 turn 的 total_tokens(FR-006)。"""
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_b" / "events.jsonl"
    state = _hstate(events)

    async def _run_step(s, run, prev):  # type: ignore[no-untyped-def]
        s.status = "success"
        s.output_json = {"_fake": "d"}
        s.total_tokens = 42

    state.by_key["material_parsing"] = _StubStep("material_parsing")
    w = AgentWorker(agent_id="material", step_key="material_parsing", state=state,
                    run_step_fn=_run_step, gate_review_fn=None)

    await w._run_unlocked()

    assert state.spent_tokens == 42
