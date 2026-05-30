"""P3 observer:stagnation（US3）+ gatekeeper（US5）

spec 003-coordinator-transform · FR-009~012 / 018~020 + SC-004/006

- T027 _is_quiescent 真值表
- T028 死锁 → stagnation 激活就绪静默 worker
- T029 stagnation 激活集合 = requires 满足且未产出（不指定 next-speaker）
- T030 stagnation 无解 ×MAX_RETRY → partial
- T034 ArtifactGate.check 齐/缺
- T035 quiescence + 齐 → gate_pass + done
- T036 quiescence + 无解 → gate_reject + partial + 点名
- T037 任意收尾给确定终止状态码
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.orchestrator.artifacts_v2 import write_versioned
from app.orchestrator.coordinator_observer import (
    AGENT_PRODUCES, ArtifactGate, CoordinatorObserver,
)
from app.orchestrator.harness import AgentWorker, EventBus, HarnessState
from app.orchestrator.subscription import SubscriptionRegistry, WORKER_PROFILE


def _hstate(events_path, task_id="tsk_o") -> HarnessState:
    st = HarnessState(
        run=type("R", (), {"task_id": task_id, "title": "周报", "report_type": "x",
                           "audience": "领导", "raw_text": "本周"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True, events_jsonl_path=events_path,
    )
    st.done = asyncio.get_running_loop().create_future()
    st.bootstrapped = True
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


# ────────────────────────────── T027 quiescence ──────────────────────────────


@pytest.mark.asyncio
async def test_is_quiescent_truth_table(tmp_outputs_dir):
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_o" / "events.jsonl"
    state = _hstate(events_path)
    obs = CoordinatorObserver(state=state, workers={}, goal="g")
    obs._process_reviewed = True  # P4:跳过流程逻辑审,直接测 gatekeeper 决策

    assert obs._is_quiescent() is True  # bootstrapped + inflight0 + 未完成 + 无 worker

    state.inflight_steps = 1
    assert obs._is_quiescent() is False  # 有 in-flight
    state.inflight_steps = 0

    state.bootstrapped = False
    assert obs._is_quiescent() is False  # 启动态
    state.bootstrapped = True

    state.done.set_result("x")
    assert obs._is_quiescent() is False  # 已完成


# ────────────────────────────── T034 ArtifactGate ──────────────────────────────


@pytest.mark.asyncio
async def test_artifact_gate_check(tmp_outputs_dir, stub_state):
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_g" / "events.jsonl"
    state = stub_state("tsk_g", events_path)
    gate = ArtifactGate()

    r0 = gate.check("tsk_g")
    assert r0.passed is False
    assert set(r0.missing) == {"MaterialPool", "ReportCore", "Outline", "Script"}

    await _write_all_artifacts(state)
    r1 = gate.check("tsk_g")
    assert r1.passed is True
    assert r1.missing == ()


# ────────────────────────────── T035/T037 gate_pass → done ──────────────────────────────


@pytest.mark.asyncio
async def test_quiescence_gate_pass_done(tmp_outputs_dir, stub_state):
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_o" / "events.jsonl"
    state = _hstate(events_path)
    # 用 stub 写 artifact（写到同 task_id 目录）
    await _write_all_artifacts(stub_state("tsk_o", events_path))

    obs = CoordinatorObserver(state=state, workers={}, goal="g")
    obs._process_reviewed = True  # P4:跳过流程逻辑审,直接测 gatekeeper 决策
    await obs._on_quiescence()

    assert state.done.done() and state.done.result() == "done"
    rows = _rows(events_path)
    assert any(r.get("msg_type") == "coordinator.intervene" and r.get("kind") == "gate_pass"
               for r in rows), "应 emit gate_pass"


# ────────────────────────────── T028/T029 stagnation 激活 ──────────────────────────────


@pytest.mark.asyncio
async def test_stagnation_activates_ready_silent_worker(tmp_outputs_dir):
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_o" / "events.jsonl"
    state = _hstate(events_path)
    state.subscriptions = SubscriptionRegistry()

    ran: list[str] = []

    async def _run_step(s, run, prev):  # type: ignore[no-untyped-def]
        ran.append(s.step)
        s.status = "success"
        s.output_json = {"_fake": "data"}

    state.by_key["material_parsing"] = _StubStep("material_parsing")
    w = AgentWorker(agent_id="material", step_key="material_parsing", state=state,
                    run_step_fn=_run_step, gate_review_fn=None)
    obs = CoordinatorObserver(state=state, workers={"material": w}, goal="g")
    obs._process_reviewed = True  # P4:跳过流程逻辑审,直接测 stagnation 激活

    # gate 不齐(无任何 artifact)→ 激活 material（requires=() 满足,MaterialPool 未产）
    await obs._on_quiescence()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if state.inflight_steps == 0 and ran:
            break

    assert ran == ["material_parsing"], "stagnation 应激活 material 跑 step"
    rows = _rows(events_path)
    assert any(r.get("kind") == "stagnation" for r in rows), "应 emit stagnation"


@pytest.mark.asyncio
async def test_stagnation_only_activates_requires_satisfied(tmp_outputs_dir, stub_state):
    """T029 · 激活集合 = requires 满足且未产出（不指定 next-speaker,FR-011）。"""
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_o" / "events.jsonl"
    state = _hstate(events_path)
    state.subscriptions = SubscriptionRegistry()

    # 只写 MaterialPool → point-extractor(requires MaterialPool)可激活;
    # structure(requires ReportCore)不可激活(ReportCore 缺)
    await write_versioned(state=stub_state("tsk_o", events_path), artifact_id="MaterialPool",
                          payload={"x": 1}, producer="material", base_version=None, delta_summary="x")

    def _mk(agent_id, step):
        async def _rs(s, run, prev):  # type: ignore[no-untyped-def]
            s.status = "success"
            s.output_json = {"_fake": "d"}
        state.by_key[step] = _StubStep(step)
        return AgentWorker(agent_id=agent_id, step_key=step, state=state,
                           run_step_fn=_rs, gate_review_fn=None)

    workers = {
        "material": _mk("material", "material_parsing"),       # 已产 MaterialPool → 不激活
        "point-extractor": _mk("point-extractor", "point_extraction"),  # requires 满足 → 激活
        "structure": _mk("structure", "structure_building"),   # requires ReportCore 缺 → 不激活
    }
    obs = CoordinatorObserver(state=state, workers=workers, goal="g")

    ready = obs._ready_silent_workers()
    ready_ids = {w.agent_id for w in ready}
    assert ready_ids == {"point-extractor"}, f"只 point-extractor 可激活;实际 {ready_ids}"


# ────────────────────────────── T030/T036 无解 → partial ──────────────────────────────


@pytest.mark.asyncio
async def test_stagnation_unresolvable_to_partial(tmp_outputs_dir, monkeypatch):
    """T030/T036 · 无可激活 worker ×MAX_RETRY → gate_reject + partial（SC-004/006）。"""
    from app.orchestrator import coordinator_observer

    monkeypatch.setattr(coordinator_observer, "STAGNATION_MAX_RETRY", 2)

    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_o" / "events.jsonl"
    state = _hstate(events_path)
    state.subscriptions = SubscriptionRegistry()

    # 无任何 artifact + 无 worker 注册 → _ready_silent_workers 为空(无可激活)
    obs = CoordinatorObserver(state=state, workers={}, goal="周报主题")
    obs._process_reviewed = True  # P4:跳过流程逻辑审,直接测无解→partial

    await obs._on_quiescence()  # retry 1
    assert not state.done.done()
    await obs._on_quiescence()  # retry 2 == MAX → partial

    assert state.done.done() and state.done.result() == "partial"
    rows = _rows(events_path)
    assert any(r.get("kind") == "gate_reject" for r in rows), "应 emit gate_reject"


@pytest.mark.asyncio
async def test_gatekeeper_always_terminates(tmp_outputs_dir, monkeypatch):
    """T037 · 任意收尾路径都给出确定终止状态码,done 必被 set（FR-020）。"""
    from app.orchestrator import coordinator_observer

    monkeypatch.setattr(coordinator_observer, "STAGNATION_MAX_RETRY", 1)
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_o" / "events.jsonl"
    state = _hstate(events_path)
    state.subscriptions = SubscriptionRegistry()
    obs = CoordinatorObserver(state=state, workers={}, goal="g")
    obs._process_reviewed = True  # P4:跳过流程逻辑审,直接测 gatekeeper 决策

    await obs._on_quiescence()  # 无 artifact 无 worker → 立即达 MAX(1) → partial
    assert state.done.done(), "gatekeeper 必须给确定终止状态(done 被 set)"
    assert state.done.result() in ("done", "partial")
