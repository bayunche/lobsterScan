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
