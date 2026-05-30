"""P3 drift 纠偏（US4）· spec 003-coordinator-transform · FR-013~017 + SC-005

⚠️ 依赖 Phase 0 宪章修订（1.0.0 → 1.1.0,原则 IV drift 受限 LLM 例外）— 已完成。

- T041 drifted=True → emit intervene(kind=drift)
- T042 drifted=False → 不 emit
- T043 judge crash → 降级 log warn 不挂
- T044 drift 只发声:不路由 next-speaker、不改产物
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.orchestrator.coordinator_observer import CoordinatorObserver
from app.orchestrator.events_v2 import AgentSpeak
from app.orchestrator.harness import EventBus, HarnessState
from app.orchestrator.subscription import SubscriptionRegistry


def _hstate(events_path, task_id="tsk_d") -> HarnessState:
    st = HarnessState(
        run=type("R", (), {"task_id": task_id, "title": "周报", "report_type": "x",
                           "audience": "领导", "raw_text": "本周项目进度"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=True, events_jsonl_path=events_path,
    )
    st.done = asyncio.get_running_loop().create_future()
    st.bootstrapped = True
    st.subscriptions = SubscriptionRegistry()
    return st


def _rows(events_path):
    if not events_path.is_file():
        return []
    return [json.loads(l) for l in events_path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _drift_rows(events_path):
    return [r for r in _rows(events_path)
            if r.get("msg_type") == "coordinator.intervene" and r.get("kind") == "drift"]


# ────────────────────────────── T041 drifted → intervene ──────────────────────────────


@pytest.mark.asyncio
async def test_drift_intervene_when_drifted(tmp_outputs_dir, mock_drift):
    mock_drift(drifted=True, text="请回到本周项目进度主题")
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_d" / "events.jsonl"
    state = _hstate(events_path)
    obs = CoordinatorObserver(state=state, workers={}, goal="本周项目进度")
    obs._recent = ["我们来聊点别的吧", "周末去哪玩"]  # 非空,触发 judge

    await obs._check_drift()

    drift = _drift_rows(events_path)
    assert len(drift) == 1, f"drifted 时应 emit 1 条 drift;实际 {len(drift)}"
    assert "请回到本周项目进度主题" in drift[0]["text"]


# ────────────────────────────── T042 not drifted → silent ──────────────────────────────


@pytest.mark.asyncio
async def test_drift_silent_when_not_drifted(tmp_outputs_dir, mock_drift):
    mock_drift(drifted=False)
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_d" / "events.jsonl"
    state = _hstate(events_path)
    obs = CoordinatorObserver(state=state, workers={}, goal="本周项目进度")
    obs._recent = ["本周完成了客户回访", "活动页初稿就绪"]

    await obs._check_drift()
    assert _drift_rows(events_path) == [], "未跑题时不应 emit drift"


@pytest.mark.asyncio
async def test_drift_skipped_when_no_recent_speaks(tmp_outputs_dir, mock_drift):
    """recent 为空 → 不调 judge,不 emit（避免空上下文盲判）。"""
    mock_drift(drifted=True, text="x")
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_d" / "events.jsonl"
    state = _hstate(events_path)
    obs = CoordinatorObserver(state=state, workers={}, goal="g")
    obs._recent = []  # 空

    await obs._check_drift()
    assert _drift_rows(events_path) == []


# ────────────────────────────── T043 judge crash → 降级 ──────────────────────────────


@pytest.mark.asyncio
async def test_drift_degrades_on_judge_crash(tmp_outputs_dir, mock_drift):
    """T043 · judge 抛异常 → _check_drift 仅 log warn,不挂、不 emit（FR-017）。"""
    mock_drift(raise_exc=True)
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_d" / "events.jsonl"
    state = _hstate(events_path)
    obs = CoordinatorObserver(state=state, workers={}, goal="g")
    obs._recent = ["随便聊聊"]

    # 不应抛
    await obs._check_drift()
    assert _drift_rows(events_path) == [], "judge crash 时不应 emit drift"
    assert not state.done.done(), "drift 降级不应影响任务状态"


# ────────────────────────────── T044 drift 只发声 ──────────────────────────────


@pytest.mark.asyncio
async def test_drift_does_not_route_or_mutate(tmp_outputs_dir, mock_drift):
    """T044 · drift intervene 不路由 next-speaker、不改产物（FR-016）。"""
    mock_drift(drifted=True, text="回到主题")
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_d" / "events.jsonl"
    state = _hstate(events_path)

    # spy:若 drift 误触发 worker.run / force_run_v2,会被记录
    ran: list[str] = []

    class _SpyWorker:
        agent_id = "material"
        inbox = None

        async def run(self):
            ran.append("run")

        async def force_run_v2(self):
            ran.append("force")

    obs = CoordinatorObserver(state=state, workers={"material": _SpyWorker()}, goal="g")
    obs._recent = ["跑题了"]

    await obs._check_drift()

    drift = _drift_rows(events_path)
    assert len(drift) == 1
    # coordinator.intervene schema 本就无 mentions/cc 字段(宪章 IV:不路由)
    assert "mentions" not in drift[0], "drift 事件不应含 mentions(不路由 next-speaker)"
    assert "cc" not in drift[0]
    # 没有 worker 被 drift 触发跑活
    assert ran == [], "drift 只发声,不应触发任何 worker 跑 step"
