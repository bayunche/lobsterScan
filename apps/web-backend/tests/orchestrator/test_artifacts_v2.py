"""T020 [US3] · artifacts_v2.write_versioned 单测

详 specs/001-v2-chat-protocol-state/tasks.md。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.orchestrator.artifacts_v2 import (
    ARTIFACT_EXT, ARTIFACT_FILENAME, next_version, read_versioned, write_versioned,
)
from app.orchestrator.events_v2 import CORE_ARTIFACTS


async def test_first_write_creates_v1_with_meta_and_latest_without(tmp_outputs_dir, stub_state):
    """A: 首次写 → version=1 / base_version=None；版本文件含 __meta__，latest 不含。"""
    task_id = "tsk_a"
    events_jsonl = Path(f"data/outputs/{task_id}/events.jsonl")
    state = stub_state(task_id, events_jsonl)

    v = await write_versioned(
        state=state, artifact_id="MaterialPool",
        payload={"items": [1, 2, 3]}, producer="material",
        base_version=None, delta_summary="首版",
    )
    assert v == 1

    task_dir = Path(f"data/outputs/{task_id}")
    versioned = task_dir / "material_pool_v1.json"
    latest = task_dir / "material_pool.json"
    assert versioned.is_file()
    assert latest.is_file()

    versioned_data = json.loads(versioned.read_text(encoding="utf-8"))
    assert versioned_data["__meta__"]["version"] == 1
    assert versioned_data["__meta__"]["base_version"] is None
    assert versioned_data["__meta__"]["producer"] == "material"
    assert versioned_data["__meta__"]["delta_summary"] == "首版"

    latest_data = json.loads(latest.read_text(encoding="utf-8"))
    assert "__meta__" not in latest_data
    assert latest_data == {"items": [1, 2, 3]}


async def test_second_write_increments_with_base(tmp_outputs_dir, stub_state):
    """B: 再写一次基于 v1 → version=2 / base_version=1；latest 覆写为 v2 payload。"""
    task_id = "tsk_b"
    state = stub_state(task_id, f"data/outputs/{task_id}/events.jsonl")

    v1 = await write_versioned(state=state, artifact_id="MaterialPool",
                               payload={"x": 1}, producer="material")
    v2 = await write_versioned(state=state, artifact_id="MaterialPool",
                               payload={"x": 2}, producer="material", base_version=v1,
                               delta_summary="updated x")
    assert v1 == 1 and v2 == 2

    task_dir = Path(f"data/outputs/{task_id}")
    v2_data = json.loads((task_dir / "material_pool_v2.json").read_text())
    assert v2_data["__meta__"]["version"] == 2
    assert v2_data["__meta__"]["base_version"] == 1

    latest = json.loads((task_dir / "material_pool.json").read_text())
    assert latest == {"x": 2}


async def test_script_markdown_meta_in_html_comment(tmp_outputs_dir, stub_state):
    """C: Script (Markdown) 版本文件顶部带 <!--__meta__: ...-->；latest 不带。"""
    task_id = "tsk_c"
    state = stub_state(task_id, f"data/outputs/{task_id}/events.jsonl")

    md = "# 我的讲稿\n第一段\n第二段"
    v = await write_versioned(state=state, artifact_id="Script",
                              payload=md, producer="copywriter",
                              delta_summary="首版")
    assert v == 1

    task_dir = Path(f"data/outputs/{task_id}")
    versioned = (task_dir / "script_v1.md").read_text(encoding="utf-8")
    assert versioned.startswith("<!--__meta__:")
    assert "Script" in versioned and '"version": 1' in versioned
    assert md in versioned          # 原文保留

    latest = (task_dir / "script.md").read_text(encoding="utf-8")
    assert not latest.startswith("<!--__meta__:")
    assert latest == md


async def test_emits_artifact_update_event_with_valid_schema(tmp_outputs_dir, stub_state):
    """D: emit ArtifactUpdate 事件，schema 校验通过，ref 指向带版本号文件。"""
    task_id = "tsk_d"
    events_jsonl = Path(f"data/outputs/{task_id}/events.jsonl")
    state = stub_state(task_id, events_jsonl)

    await write_versioned(state=state, artifact_id="ReportCore",
                          payload={"points": [1, 2]}, producer="point-extractor")
    assert len(state.emitted) == 1
    e = state.emitted[0]
    assert e["msg_type"] == "artifact.update"
    assert e["id"] == "ReportCore"
    assert e["version"] == 1
    assert e["ref"] == f"data/outputs/{task_id}/report_core_v1.json"


async def test_reject_non_core_artifact(tmp_outputs_dir, stub_state):
    """E: 试图写 HTML / 视频等非 4 核心 artifact → ValueError。"""
    task_id = "tsk_e"
    state = stub_state(task_id, f"data/outputs/{task_id}/events.jsonl")

    with pytest.raises(ValueError):
        await write_versioned(state=state, artifact_id="HTML", payload={}, producer="x")


async def test_next_version_when_empty(tmp_outputs_dir):
    """边界：空目录返回 version=1。"""
    assert next_version("tsk_z", "MaterialPool") == 1


async def test_read_versioned_roundtrip(tmp_outputs_dir, stub_state):
    """read_versioned 读 v1 取出原 payload + 完整 meta。"""
    task_id = "tsk_rt"
    state = stub_state(task_id, f"data/outputs/{task_id}/events.jsonl")
    await write_versioned(state=state, artifact_id="Outline",
                          payload={"chapters": ["ch1", "ch2"]}, producer="structure",
                          delta_summary="首版")
    payload, meta = read_versioned(task_id, "Outline", 1)
    assert payload == {"chapters": ["ch1", "ch2"]}
    assert meta["version"] == 1
    assert meta["producer"] == "structure"


def test_artifact_constants_consistent():
    """4 核心 artifact 在 events_v2.CORE_ARTIFACTS / ARTIFACT_FILENAME / ARTIFACT_EXT 三者一致。"""
    assert set(ARTIFACT_FILENAME.keys()) == CORE_ARTIFACTS
    assert set(ARTIFACT_EXT.keys()) == CORE_ARTIFACTS
    assert ARTIFACT_EXT["Script"] == "md"
    assert all(ARTIFACT_EXT[k] == "json" for k in ("MaterialPool", "ReportCore", "Outline"))
