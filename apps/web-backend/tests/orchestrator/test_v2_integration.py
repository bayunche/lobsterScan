"""T019 + T023 [US2 + US3] · v2 端到端集成

P1 阶段 v2 协议层 + artifact 版本化的端到端集成（不真跑 agent，
直接调 emit_v2 + write_versioned 模拟收尾期 _emit_v2_finalization 行为）。

完整跑 pipeline.execute() 用真 ScriptedBackend 的 mocked-LLM 路径成本太高（要 mock 8
个 step 的复杂 JSON），留到 P5 之后做。这里覆盖核心契约：
- 5 类事件至少各 1 条
- 4 核心 artifact 各 ≥ 1 个版本化文件
- events.jsonl schema 全绿
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.orchestrator import replay_check
from app.orchestrator.artifacts_v2 import write_versioned
from app.orchestrator.events_v2 import (
    AgentSilent,
    AgentSpeak,
    ArtifactRef,
    ArtifactUpdate,
    CoordinatorIntervene,
    Finding,
    ReviewerVerdict,
)


async def test_v2_events_emitted_end_to_end(tmp_outputs_dir, stub_state):
    """5 类新事件在 events.jsonl 至少各 1 条。"""
    task_id = "tsk_v2int1"
    events_jsonl = Path(f"data/outputs/{task_id}/events.jsonl")
    state = stub_state(task_id, events_jsonl)

    # 模拟 _emit_v2_finalization 内 5 类事件
    await state.emit_v2(AgentSpeak(
        task_id=task_id, **{"from": "material"}, text="素材池已整理",
        mentions=["point-extractor"], intent="propose",
    ))
    await state.emit_v2(AgentSilent(
        task_id=task_id, **{"from": "html-designer"}, reason="无需补充",
    ))
    await state.emit_v2(CoordinatorIntervene(
        task_id=task_id, kind="gate_pass", text="全部齐了",
    ))
    await state.emit_v2(ReviewerVerdict(
        task_id=task_id, verdict="pass", dimension="quality",
        suggestions=["s1", "s2", "s3"],
    ))
    # ArtifactUpdate 由 write_versioned 自动 emit
    await write_versioned(
        state=state, artifact_id="MaterialPool",
        payload={"x": 1}, producer="material",
    )

    # 校验 events.jsonl
    rep = replay_check.replay_check(events_jsonl)
    assert rep["schema_invalid"] == 0, f"schema invalid: {rep}"
    for kind in ("agent.speak", "agent.silent",
                 "coordinator.intervene", "reviewer.verdict", "artifact.update"):
        assert rep["v2_per_kind"].get(kind, 0) >= 1, f"missing {kind} in {rep}"
    assert rep["v1_events"] == 0, "v2-only test 不应出现 v1 行"


async def test_v2_4_core_artifacts_each_have_versioned(tmp_outputs_dir, stub_state):
    """4 核心 artifact 每个 ≥ 1 个 _v<N> 文件 + 1 个 latest 副本。"""
    task_id = "tsk_v2int2"
    events_jsonl = Path(f"data/outputs/{task_id}/events.jsonl")
    state = stub_state(task_id, events_jsonl)

    await write_versioned(state=state, artifact_id="MaterialPool",
                          payload={"items": []}, producer="material")
    await write_versioned(state=state, artifact_id="ReportCore",
                          payload={"points": []}, producer="point-extractor")
    await write_versioned(state=state, artifact_id="Outline",
                          payload={"chapters": []}, producer="structure")
    await write_versioned(state=state, artifact_id="Script",
                          payload="# 标题\n第一段", producer="copywriter")

    task_dir = Path(f"data/outputs/{task_id}")
    files = sorted(p.name for p in task_dir.iterdir() if p.is_file())
    # 必须有版本文件 + latest
    for stem, ext in (("material_pool", "json"), ("report_core", "json"),
                      ("outline", "json"), ("script", "md")):
        assert f"{stem}.{ext}" in files, f"missing latest {stem}.{ext} in {files}"
        assert f"{stem}_v1.{ext}" in files, f"missing v1 {stem}_v1.{ext} in {files}"


async def test_message_id_uniqueness_within_task(tmp_outputs_dir, stub_state):
    """所有 v2 事件的 message_id 全任务内唯一。"""
    task_id = "tsk_v2int3"
    state = stub_state(task_id, f"data/outputs/{task_id}/events.jsonl")

    for _ in range(10):
        await state.emit_v2(AgentSpeak(
            task_id=task_id, **{"from": "material"}, text="x", intent="confirm",
        ))

    ids = [e["message_id"] for e in state.emitted]
    assert len(ids) == 10
    assert len(set(ids)) == 10, "message_id 必须全任务内唯一"


async def test_replay_check_returns_zero_invalid(tmp_outputs_dir, stub_state):
    """replay_check.py 跑 v2 events 输出 schema_invalid=0。"""
    task_id = "tsk_v2int4"
    events_jsonl = Path(f"data/outputs/{task_id}/events.jsonl")
    state = stub_state(task_id, events_jsonl)

    await state.emit_v2(AgentSpeak(
        task_id=task_id, **{"from": "material"}, text="x", intent="propose",
    ))
    await write_versioned(state=state, artifact_id="MaterialPool",
                          payload={}, producer="material")

    rep = replay_check.replay_check(events_jsonl)
    assert rep["schema_invalid"] == 0
    assert rep["v2_events"] >= 2
