"""P4 质量轨:artifact 产出即时审校（US2）

spec 004-reviewer-dual-track · FR-001~004 + SC-002

monkeypatch pipeline._quick_review 控制 accept(避开 env_for_agent/backend 依赖)。
"""

from __future__ import annotations

import json

import pytest

from app.orchestrator.events_v2 import AgentSpeak, ArtifactUpdate
from app.orchestrator.harness import AgentWorker, EventBus, HarnessState


def _state(events_path, task_id="tsk_q") -> HarnessState:
    return HarnessState(
        run=type("R", (), {"task_id": task_id, "duration": "3分钟", "title": "周报"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True, events_jsonl_path=events_path,
    )


class _Step:
    def __init__(self, step):
        self.step = step
        self.agent = "material"
        self.label = "资料员 · 整理材料"
        self.output_json = {"payload": {"completed": ["A"]}}


def _reviewer(state):
    async def _noop(*a, **kw):  # type: ignore[no-untyped-def]
        return None
    return AgentWorker(agent_id="reviewer", step_key="review", state=state,
                       run_step_fn=_noop, gate_review_fn=None)


def _artifact_ev(task_id, aid="MaterialPool", version=1, producer="material"):
    fname = {"MaterialPool": "material_pool", "ReportCore": "report_core",
             "Outline": "outline", "Script": "script"}[aid]
    ext = "md" if aid == "Script" else "json"
    return ArtifactUpdate(task_id=task_id, id=aid, version=version, producer=producer,
                          ref=f"data/outputs/{task_id}/{fname}_v{version}.{ext}")


def _rows(p):
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _patch_qr(monkeypatch, accept, comment="OK", reason=""):
    from app.orchestrator import pipeline

    async def _fake(s, run, state=None):  # type: ignore[no-untyped-def]
        return {"accept": accept, "comment": comment, "reason": reason}
    monkeypatch.setattr(pipeline, "_quick_review", _fake)


# ────────────────────────────── T014 pass ──────────────────────────────


@pytest.mark.asyncio
async def test_quality_review_pass(tmp_outputs_dir, monkeypatch):
    _patch_qr(monkeypatch, accept=True)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_q" / "events.jsonl"
    state = _state(events)
    state.by_key["material_parsing"] = _Step("material_parsing")
    w = _reviewer(state)

    await w._reviewer_quality_review(_artifact_ev("tsk_q"))

    verdicts = [r for r in _rows(events) if r.get("msg_type") == "reviewer.verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["dimension"] == "quality"
    assert verdicts[0]["verdict"] == "pass"
    assert len(verdicts[0]["suggestions"]) >= 3


# ────────────────────────────── T015 fail ──────────────────────────────


@pytest.mark.asyncio
async def test_quality_review_fail_with_fix_agent(tmp_outputs_dir, monkeypatch):
    _patch_qr(monkeypatch, accept=False, reason="客户回访写具体点")
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_q" / "events.jsonl"
    state = _state(events)
    state.by_key["material_parsing"] = _Step("material_parsing")
    w = _reviewer(state)

    await w._reviewer_quality_review(_artifact_ev("tsk_q", producer="material"))

    v = [r for r in _rows(events) if r.get("msg_type") == "reviewer.verdict"][0]
    assert v["verdict"] == "fail"
    assert v["suggested_fix_agent"] == "material"
    assert len(v["suggestions"]) >= 3


# ────────────────────────────── T016 版本去重 ──────────────────────────────


@pytest.mark.asyncio
async def test_quality_review_version_dedup(tmp_outputs_dir, monkeypatch):
    _patch_qr(monkeypatch, accept=True)
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_q" / "events.jsonl"
    state = _state(events)
    state.by_key["material_parsing"] = _Step("material_parsing")
    w = _reviewer(state)

    ev = _artifact_ev("tsk_q", version=1)
    await w._reviewer_quality_review(ev)
    await w._reviewer_quality_review(ev)  # 同 (id, version) → 不重审

    verdicts = [r for r in _rows(events) if r.get("msg_type") == "reviewer.verdict"]
    assert len(verdicts) == 1, "同一 artifact 版本应只审一次(SC-002)"


# ────────────────────────────── T017 被 mention → silent ──────────────────────────────


@pytest.mark.asyncio
async def test_reviewer_mention_silent(tmp_outputs_dir):
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_q" / "events.jsonl"
    state = _state(events)
    w = _reviewer(state)

    # 非 ArtifactUpdate(被 mention 的 speak)→ silent,不跑 step
    speak = AgentSpeak(task_id="tsk_q", **{"from": "video-producer"},
                       text="@reviewer 收尾了", intent="propose", mentions=["reviewer"])
    await w._reviewer_handle(speak)

    rows = _rows(events)
    silents = [r for r in rows if r.get("msg_type") == "agent.silent" and r.get("from") == "reviewer"]
    assert len(silents) == 1
    assert not any(r.get("msg_type") == "reviewer.verdict" for r in rows), "被 mention 不出 verdict"
