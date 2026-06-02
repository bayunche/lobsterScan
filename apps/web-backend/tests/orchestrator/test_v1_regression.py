"""T012 [US1] · v1 路径零回归护栏

v1 路径必须与改造前行为完全一致 —— 不出现任何 v2 字段 / msg_type / 版本化文件。

本测试不依赖跑完整 pipeline（成本高且 mock 维护成本大），而是测试关键约束：
- HarnessState.emit_v2 在 is_v2=False 时是 no-op（不写 events.jsonl、不发总线）
- TaskRun.harness_version 默认 "v1"
- 任意非法 harness_version 都降级为 "v1"
- 显式 "v2" 才启用 v2 路径

集成层面的逐字段比对放在 T027 manual smoke。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.orchestrator.events_v2 import AgentSpeak
from app.orchestrator.ids import MessageIdRegistry


class _MiniState:
    """极小 HarnessState mock（避免引入 EventBus 复杂度）。"""
    def __init__(self, is_v2: bool, events_path: Path):
        self.is_v2 = is_v2
        self.events_jsonl_path = events_path
        self.message_id_registry = MessageIdRegistry()
        self.run = type("R", (), {"task_id": "tsk_test"})()
        self.bus_called = False

    # 复制 HarnessState.emit_v2 的精简版（与生产代码 1:1 同步）
    async def emit_v2(self, event) -> None:
        if not self.is_v2:
            return
        if not self.message_id_registry.add_or_reject(event.message_id):
            return
        row = event.model_dump(mode="json", by_alias=True)
        self.events_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.bus_called = True


async def test_emit_v2_is_no_op_when_v1(tmp_outputs_dir):
    """v1 路径下 emit_v2 不写 events.jsonl，不发总线。"""
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_v1" / "events.jsonl"
    state = _MiniState(is_v2=False, events_path=events)

    ev = AgentSpeak(task_id="tsk_v1", **{"from": "material"}, text="x", intent="propose")
    await state.emit_v2(ev)

    assert not events.exists(), "v1 路径不应写入 events.jsonl"
    assert state.bus_called is False, "v1 路径不应发总线"


async def test_emit_v2_writes_when_v2(tmp_outputs_dir):
    """v2 路径下 emit_v2 正常写入。"""
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_v2" / "events.jsonl"
    state = _MiniState(is_v2=True, events_path=events)

    ev = AgentSpeak(task_id="tsk_v2", **{"from": "material"}, text="x", intent="propose")
    await state.emit_v2(ev)

    assert events.is_file()
    row = json.loads(events.read_text(encoding="utf-8").strip())
    assert row["msg_type"] == "agent.speak"
    assert state.bus_called is True


def test_taskrun_default_harness_version_is_v1():
    """TaskRun 默认 harness_version='v1'。需要 fastapi 栈，跳过若不可用。"""
    try:
        from app.orchestrator.pipeline import TaskRun
    except ImportError as e:
        pytest.skip(f"pipeline.py 依赖不可用: {e}")

    run = TaskRun(
        task_id="t", title="T", report_type="daily", audience="直属领导",
        duration="3分钟", style="简洁正式", raw_text="x",
    )
    assert run.harness_version == "v1"


def test_create_task_invalid_harness_version_falls_back_to_v1():
    """非 v1/v2 一律降级为 v1（FR-002 防御）。"""
    try:
        from app.orchestrator import pipeline as pl
    except ImportError as e:
        pytest.skip(f"pipeline.py 依赖不可用: {e}")

    for bad in (None, "", "v3", "weird", "V2", " v2"):
        run = pl.create_task(
            task_id=f"tsk_bad_{abs(hash(str(bad))) % 1000}",
            title="T", report_type="daily", audience="直属领导",
            duration="3分钟", style="简洁正式", raw_text="x",
            harness_version=bad,
        )
        assert run.harness_version == "v1", f"bad input {bad!r} should fallback to v1"


def test_create_task_v2_explicit_sets_v2():
    try:
        from app.orchestrator import pipeline as pl
    except ImportError as e:
        pytest.skip(f"pipeline.py 依赖不可用: {e}")

    run = pl.create_task(
        task_id="tsk_v2x",
        title="T", report_type="daily", audience="直属领导",
        duration="3分钟", style="简洁正式", raw_text="x",
        harness_version="v2",
    )
    assert run.harness_version == "v2"


async def test_duplicate_message_id_rejected(tmp_outputs_dir):
    """重复 message_id 第二次拒写（FR-005 + FR-020 降级）。"""
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_dup" / "events.jsonl"
    state = _MiniState(is_v2=True, events_path=events)

    fixed = "msg_aaaaaaaa"
    ev1 = AgentSpeak(task_id="t", message_id=fixed, **{"from": "x"}, text="a", intent="ask")
    ev2 = AgentSpeak(task_id="t", message_id=fixed, **{"from": "x"}, text="b", intent="ask")
    await state.emit_v2(ev1)
    await state.emit_v2(ev2)

    rows = [json.loads(l) for l in events.read_text(encoding="utf-8").strip().splitlines() if l]
    assert len(rows) == 1, f"重复 message_id 应只写一条,实际 {len(rows)}"
    assert rows[0]["text"] == "a"


# ────────────────────────────── P2 v1 零回归 ──────────────────────────────
# T012-T015 · spec 002-worker-subscription · US1 P1 红线护栏
#
# 这 4 个 case 锁住 P2 新字段在 v1 路径下永远是 None / 空 dict;后续 Phase 4 T028
# 会让 is_v2=True 时分配这些字段,但 v1 路径必须永远短路保持原行为(FR-003 / FR-013)。

from app.orchestrator.harness import AgentWorker, EventBus, HarnessState
from app.orchestrator.subscription import SubscriptionRegistry


def _make_state(is_v2: bool) -> HarnessState:
    """构造一个最小的真实 HarnessState(不起 done future,不跑 run_harness)。"""
    return HarnessState(
        run=type("R", (), {"task_id": "tsk_test"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=is_v2,
    )


def _make_worker(state: HarnessState, agent_id: str = "material") -> AgentWorker:
    async def _noop_run_step(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None
    return AgentWorker(
        agent_id=agent_id, step_key=agent_id, state=state,
        run_step_fn=_noop_run_step, gate_review_fn=None,
    )


def test_v1_state_subscriptions_is_none_and_locks_empty():
    """T012 [US1] · v1 路径下 state.subscriptions is None 且 agent_locks 是空 dict。

    若 Phase 4 T028 误在 v1 路径下分配 SubscriptionRegistry,本 case 会失败。
    """
    state = _make_state(is_v2=False)

    assert state.subscriptions is None, (
        "v1 路径 state.subscriptions 必须为 None(FR-003 零开销红线)"
    )
    assert state.agent_locks == {}, (
        "v1 路径 state.agent_locks 必须为空 dict(只在 v2 路径下 lazy-init)"
    )


def test_v1_worker_inbox_and_consume_task_default_none():
    """T013 [US1] · v1 路径下 AgentWorker.inbox 与 _consume_task 默认 None。

    若 Phase 4 T025 误在 v1 路径下调 start_v2_consumer(),本 case 会失败。
    """
    state = _make_state(is_v2=False)
    worker = _make_worker(state)

    assert worker.inbox is None, "v1 路径 worker.inbox 必须为 None"
    assert worker._consume_task is None, "v1 路径 worker._consume_task 必须为 None"


async def test_v1_events_jsonl_contains_no_msg_type_field(tmp_outputs_dir):
    """T014 [US1] · v1 emit 写入的 events.jsonl 不含任何 msg_type 字段(SC-001)。

    v1 行使用 kind 字段(AgentEvent.to_dict);v2 行才有 msg_type。
    若 P2 内部错把 v2 emit 路径误用到 v1,grep "msg_type" 会命中。
    """
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_v1_grep" / "events.jsonl"
    state = HarnessState(
        run=type("R", (), {"task_id": "tsk_v1_grep"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=False,
        events_jsonl_path=events_path,
    )

    # 1) v1 emit 一些事件
    await state.emit("task.start", "coordinator", None, {"steps": ["a", "b"]})
    await state.emit("agent.start", "material", "material", {"visit_no": 1})
    await state.emit("agent.done", "material", "material", {"tokens": 100})

    # 2) v1 路径下即便调 emit_v2 也不应写入(短路守护,T015 重点验证)
    await state.emit_v2(
        AgentSpeak(task_id="tsk_v1_grep", **{"from": "material"},
                   text="should-not-appear", intent="propose")
    )

    # 3) 校验
    assert events_path.is_file(), "v1 emit 应写 events.jsonl"
    raw = events_path.read_text(encoding="utf-8")
    assert '"msg_type"' not in raw, (
        f"v1 路径 events.jsonl 不应含 msg_type 字段;实际内容:\n{raw[:500]}"
    )
    # 校验 kind 字段存在(v1 协议)
    assert '"kind"' in raw, "v1 路径 events.jsonl 应使用 kind 字段"


async def test_v1_emit_v2_does_not_dispatch_to_subscriptions(tmp_outputs_dir):
    """T015 [US1] · v1 路径下 emit_v2 不写盘 + 不发 bus + 即使误挂 SubscriptionRegistry 也不 dispatch。

    模拟一个"v2 字段不小心被 P2 构造但 is_v2=False"的 corner case,
    确认 emit_v2 的 short-circuit 守住红线,subscriptions.dispatch 不会被调用。
    """
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_v1_dispatch" / "events.jsonl"
    state = HarnessState(
        run=type("R", (), {"task_id": "tsk_v1_dispatch"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=False,
        events_jsonl_path=events_path,
    )

    # 故意挂一个 spy registry — 即便它存在,v1 短路必须保证 dispatch 不被调用
    dispatch_calls: list = []
    spy_registry = SubscriptionRegistry()

    def _spy_dispatch(event):
        dispatch_calls.append(event)
        return 0

    spy_registry.dispatch = _spy_dispatch  # type: ignore[method-assign]
    state.subscriptions = spy_registry

    # 也挂个 bus emit 计数,确保 short-circuit 真生效
    bus_calls: list = []

    async def _bus_count(_ev):
        bus_calls.append(_ev)

    state.bus.on_any(_bus_count)

    await state.emit_v2(
        AgentSpeak(task_id="tsk_v1_dispatch", **{"from": "material"},
                   text="ignored", intent="propose")
    )

    assert not events_path.exists(), "v1 路径 emit_v2 不应写 events.jsonl"
    assert dispatch_calls == [], (
        "v1 路径 emit_v2 必须在 dispatch 前 return("
        "FR-003 + emit_v2 第一行 if not self.is_v2: return)"
    )
    assert bus_calls == [], "v1 路径 emit_v2 不应触达 bus.emit"


# ────────────────────────────── P3 v1 零回归 ──────────────────────────────
# T011-T013 · spec 003-coordinator-transform · US1 P1 红线护栏
#
# 锁住 P3 新字段/行为在 v1 路径完全短路:observer 不构造、inflight 不动、
# Coordinator 仍 chain 路由、events.jsonl 无 P3 新事件。

from app.orchestrator.harness import AgentEvent, Coordinator


def test_v1_observer_and_inflight_default():
    """T011 [US1] · v1 路径 observer is None / inflight_steps==0 / bootstrapped False。"""
    state = _make_state(is_v2=False)
    assert state.observer is None, "v1 路径 observer 必须为 None(零开销红线)"
    assert state.inflight_steps == 0, "v1 路径 inflight_steps 必须为 0"
    assert state.bootstrapped is False, "v1 路径 bootstrapped 必须为 False"


def _make_coordinator(state, spy_workers):
    """构造一个最小 Coordinator(material → material_parsing 单步链)。"""
    return Coordinator(
        state=state, workers=spy_workers,
        agent_to_step={"material": "material_parsing"},
        step_to_agent={"material_parsing": "material"},
        default_next_step={"material_parsing": "DONE"},
        agent_display={"material": "资料员"},
    )


class _SpyWorker:
    def __init__(self) -> None:
        self.ran = 0

    async def run(self) -> None:
        self.ran += 1


async def test_v1_coordinator_still_chain_routes():
    """T012 [US1] · v1 路径 Coordinator.on_handoff 不 short-circuit(仍 chain 派单)。"""
    state = _make_state(is_v2=False)
    spy = _SpyWorker()
    coord = _make_coordinator(state, {"material": spy})

    ev = AgentEvent(kind="agent.handoff", agent_id="coordinator", step_key=None,
                    payload={"to": "material"})
    await coord.on_handoff(ev)
    await asyncio.sleep(0.01)  # 让 create_task(spy.run()) 跑起来

    assert spy.ran == 1, "v1 路径 Coordinator 应 chain 派单(on_handoff 不 short-circuit)"


async def test_v2_coordinator_short_circuits_chain():
    """T019 对照 [US2] · v2 路径 Coordinator.on_handoff short-circuit(不 chain 派单)。"""
    state = _make_state(is_v2=True)
    spy = _SpyWorker()
    coord = _make_coordinator(state, {"material": spy})

    ev = AgentEvent(kind="agent.handoff", agent_id="coordinator", step_key=None,
                    payload={"to": "material"})
    await coord.on_handoff(ev)
    await asyncio.sleep(0.01)

    assert spy.ran == 0, "v2 路径 Coordinator 必须 short-circuit(驱动交给 subscription)"


async def test_v1_no_p3_events_in_log(tmp_outputs_dir):
    """T013 [US1] · v1 emit 的 events.jsonl 无 P3 新事件(stagnation/drift/gate)。"""
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_v1_p3" / "events.jsonl"
    state = HarnessState(
        run=type("R", (), {"task_id": "tsk_v1_p3"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=False, events_jsonl_path=events_path,
    )
    await state.emit("task.start", "coordinator", None, {"steps": ["a"]})
    await state.emit("agent.handoff", "coordinator", None, {"to": "material"})

    raw = events_path.read_text(encoding="utf-8")
    for forbidden in ("stagnation", "drift", "gate_pass", "gate_reject", "quiescence"):
        assert forbidden not in raw, f"v1 events.jsonl 不应含 P3 词 {forbidden!r}"


# ────────────────────────────── P4 v1 零回归 ──────────────────────────────
# T009-T010 · spec 004-reviewer-dual-track · US1 P1 红线


def test_v1_reviewer_worker_reviewed_empty():
    """T009 [US1] · v1 路径 reviewer worker `_reviewed` 为空(质量轨版本去重未启用)。"""
    state = _make_state(is_v2=False)
    w = _make_worker(state, "reviewer")
    assert w._reviewed == set(), "v1 路径 reviewer 不做质量轨,_reviewed 应为空"


async def test_v1_no_reviewer_verdict_events(tmp_outputs_dir):
    """T010 [US1] · v1 emit 的 events.jsonl 无 P4 词(reviewer.verdict/needs_fix/process_logic)。"""
    events_path = tmp_outputs_dir / "data" / "outputs" / "tsk_v1_p4" / "events.jsonl"
    state = HarnessState(
        run=type("R", (), {"task_id": "tsk_v1_p4"})(),
        prev={}, by_key={}, bus=EventBus(),
        is_v2=False, events_jsonl_path=events_path,
    )
    await state.emit("task.start", "coordinator", None, {"steps": ["a"]})
    await state.emit("agent.done", "reviewer", "review", {"tokens": 0})

    raw = events_path.read_text(encoding="utf-8")
    for forbidden in ("reviewer.verdict", "needs_fix", "process_logic", "suggested_fix_agent"):
        assert forbidden not in raw, f"v1 events.jsonl 不应含 P4 词 {forbidden!r}"


# ────────────────────────── P5 legacy 零回归(T017 · FR-014 / SC-001) ──────────────────────────


def test_p5_envelope_disabled_in_legacy_mode():
    """legacy → _envelope_enabled() False;envelope → True(flag 控制)。"""
    from app.orchestrator import pipeline, subscription
    orig = subscription.V2_PROMPT_MODE
    try:
        subscription.V2_PROMPT_MODE = "legacy"
        assert pipeline._envelope_enabled() is False
        subscription.V2_PROMPT_MODE = "envelope"
        assert pipeline._envelope_enabled() is True
    finally:
        subscription.V2_PROMPT_MODE = orig


def test_p5_legacy_step_prompt_no_transcript_uses_json_rule():
    """legacy 模式 _step_prompt:不含群聊上下文 + 用 JSON_RULE(与 P4 字段级一致)。"""
    from app.orchestrator import pipeline, subscription
    run = type("R", (), {"report_type": "x", "audience": "领导", "duration": "1分钟",
                         "style": "", "supplement": "", "agent_briefs": {}})()
    orig = subscription.V2_PROMPT_MODE
    try:
        subscription.V2_PROMPT_MODE = "legacy"
        prompt = pipeline._step_prompt("structure_building", run, {})
        assert "群聊上下文" not in prompt
        assert "群聊信封" not in prompt
        assert "结构化结果" in prompt
    finally:
        subscription.V2_PROMPT_MODE = orig


def test_p5_legacy_unwrap_identity_on_old_format():
    """旧格式 dict 经 _unwrap_envelope 整体即 artifact(output_json 不变 → 零回归)。"""
    from app.orchestrator.pipeline import _unwrap_envelope
    typed = {"chapters": [1, 2], "pattern": "总分总"}
    _, _, _, _, artifact = _unwrap_envelope(typed)
    assert artifact == typed


# ────────────────────────── P6 fanout off 零回归(T012 · FR-011 / SC-001) ──────────────────────────


def test_p6_fanout_disabled_by_default():
    """fanout off → _fanout_enabled() / _fanout_enabled_safe() False;on → True。"""
    from app.orchestrator import pipeline, subscription
    from app.orchestrator.harness import _fanout_enabled_safe
    orig = subscription.V2_FANOUT
    try:
        subscription.V2_FANOUT = "off"
        assert pipeline._fanout_enabled() is False
        assert _fanout_enabled_safe() is False
        subscription.V2_FANOUT = "on"
        assert pipeline._fanout_enabled() is True
        assert _fanout_enabled_safe() is True
    finally:
        subscription.V2_FANOUT = orig


@pytest.mark.asyncio
async def test_p6_fanout_off_eventbus_serial(tmp_outputs_dir):
    """fanout off:EventBus.emit 串行,注册顺序执行(FR-009 / SC-001)。"""
    from app.orchestrator import subscription
    from app.orchestrator.harness import EventBus, AgentEvent
    orig = subscription.V2_FANOUT
    try:
        subscription.V2_FANOUT = "off"
        bus = EventBus()
        order: list[str] = []

        async def h1(e):
            await asyncio.sleep(0.02)
            order.append("h1")

        async def h2(e):
            order.append("h2")

        bus.on("k", h1)
        bus.on("k", h2)
        await bus.emit(AgentEvent(kind="k", agent_id="a", step_key=None, payload={}))
        assert order == ["h1", "h2"]   # 串行(h1 先,即便慢)
    finally:
        subscription.V2_FANOUT = orig
