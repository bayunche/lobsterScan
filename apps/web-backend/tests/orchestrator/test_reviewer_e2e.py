"""P4 收尾决策纳入 verdict（US5）

spec 004-reviewer-dual-track · FR-014/015 + SC-005/006

observer._on_quiescence 双因子(artifact 完整性 + 未解决 verdict.fail)决定 done/partial。
"""

from __future__ import annotations

import asyncio

import pytest

from app.orchestrator.artifacts_v2 import write_versioned
from app.orchestrator.coordinator_observer import CoordinatorObserver
from app.orchestrator.harness import EventBus, HarnessState


def _state(events_path, task_id="tsk_e") -> HarnessState:
    st = HarnessState(
        run=type("R", (), {"task_id": task_id})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True, events_jsonl_path=events_path,
    )
    st.done = asyncio.get_running_loop().create_future()
    st.bootstrapped = True
    return st


async def _write_all(stub_state, task_id, events_path):
    state = stub_state(task_id, events_path)
    for aid, prod in [("MaterialPool", "material"), ("ReportCore", "point-extractor"),
                      ("Outline", "structure"), ("Script", "copywriter")]:
        payload = "讲稿" if aid == "Script" else {"x": 1}
        await write_versioned(state=state, artifact_id=aid, payload=payload,
                              producer=prod, base_version=None, delta_summary="x")


# ────────────────────────────── T035 齐 + pass → done ──────────────────────────────


@pytest.mark.asyncio
async def test_complete_and_pass_to_done(tmp_outputs_dir, stub_state):
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_e" / "events.jsonl"
    state = _state(events)
    await _write_all(stub_state, "tsk_e", events)
    # artifact_log 给流程逻辑审(顺序对)
    obs = CoordinatorObserver(state=state, workers={}, goal="g")
    obs._artifact_log = [{"id": a, "version": 1, "producer": ""} for a in
                         ("MaterialPool", "ReportCore", "Outline", "Script")]

    await obs._on_quiescence()   # ① 流程逻辑审(pass)→ return
    await obs._on_quiescence()   # ② 双因子:齐 + 无未解决 fail → done

    assert state.done.done() and state.done.result() == "done"


# ────────────────────────────── T036 齐但有未解决 fail → partial ──────────────────────────────


@pytest.mark.asyncio
async def test_complete_but_unresolved_fail_to_partial(tmp_outputs_dir, stub_state, monkeypatch):
    from app.orchestrator import coordinator_observer
    monkeypatch.setattr(coordinator_observer, "REVIEW_FIX_MAX_RETRY", 1)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_e" / "events.jsonl"
    state = _state(events)
    await _write_all(stub_state, "tsk_e", events)
    obs = CoordinatorObserver(state=state, workers={}, goal="g")
    obs._artifact_log = [{"id": a, "version": 1, "producer": ""} for a in
                         ("MaterialPool", "ReportCore", "Outline", "Script")]
    obs._process_reviewed = True            # 跳过流程逻辑审
    obs._fix_retries = {"material": 1}      # 达上限(MAX=1)的未解决 fail

    await obs._on_quiescence()              # 双因子:齐但 unresolved → partial

    assert state.done.done() and state.done.result() == "partial"


# ────────────────────────────── T037 e2e 修复闭环 → done ──────────────────────────────


@pytest.mark.asyncio
async def test_e2e_fix_then_done(tmp_outputs_dir, stub_state, monkeypatch):
    """质量审 fail → 修复重产 → 重审 pass → 收尾 done(SC-006 简化 e2e)。"""
    from app.orchestrator import coordinator_observer
    from app.orchestrator.harness import AgentWorker

    events = tmp_outputs_dir / "data" / "outputs" / "tsk_e2e" / "events.jsonl"
    state = _state(events, "tsk_e2e")

    # material worker:被 force_run_v2 修复时重产 MaterialPool
    fixed = {"n": 0}

    async def _run_step(s, run, prev):  # type: ignore[no-untyped-def]
        fixed["n"] += 1
        s.status = "success"
        s.output_json = {"x": 1}

    class _Step:
        step = "material_parsing"; status = "needs_fix"; output_json = {"x": 1}
        error = None; started_at = ended_at = None; total_tokens = 0
    state.by_key["material_parsing"] = _Step()
    w = AgentWorker(agent_id="material", step_key="material_parsing", state=state,
                    run_step_fn=_run_step, gate_review_fn=None)

    # 先备齐 4 artifact(参与度/完整性满足),修复闭环验证"fail→重激活→done"
    await _write_all(stub_state, "tsk_e2e", events)
    obs = CoordinatorObserver(state=state, workers={"material": w}, goal="g")

    # 质量 fail → _on_verdict 触发 material 修复
    from app.orchestrator.harness import AgentEvent
    await obs._on_verdict(AgentEvent(kind="reviewer.verdict", agent_id="reviewer",
                                     payload={"verdict": "fail", "suggested_fix_agent": "material"}))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if state.inflight_steps == 0 and fixed["n"]:
            break
    assert fixed["n"] == 1, "fail → material 被重激活修复"

    # 修复后收尾:artifact 齐 + 无未解决 fail(retry=1 < MAX 默认 2)→ done
    obs._artifact_log = [{"id": a, "version": 1, "producer": ""} for a in
                         ("MaterialPool", "ReportCore", "Outline", "Script")]
    await obs._on_quiescence()   # 流程逻辑审
    await obs._on_quiescence()   # 双因子 → done
    assert state.done.done() and state.done.result() == "done"
