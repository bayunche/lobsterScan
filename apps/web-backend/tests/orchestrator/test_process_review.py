"""P4 流程逻辑轨:收尾全局校验（US3）

spec 004-reviewer-dual-track · FR-005~008 + SC-003

ProcessReviewer 3 纯规则:版本一致 / 依赖图 / 参与度。无 LLM,真测试。
"""

from __future__ import annotations

import pytest

from app.orchestrator.artifacts_v2 import write_versioned
from app.orchestrator.process_review import ProcessReviewer


def _log(*items):
    """items: (id, version[, producer])"""
    out = []
    for it in items:
        aid, ver = it[0], it[1]
        producer = it[2] if len(it) > 2 else ""
        out.append({"id": aid, "version": ver, "producer": producer})
    return out


async def _write_all(stub_state, task_id, events_path):
    state = stub_state(task_id, events_path)
    for aid, prod in [("MaterialPool", "material"), ("ReportCore", "point-extractor"),
                      ("Outline", "structure"), ("Script", "copywriter")]:
        payload = "讲稿" if aid == "Script" else {"x": 1}
        await write_versioned(state=state, artifact_id=aid, payload=payload,
                              producer=prod, base_version=None, delta_summary="x")


# ────────────────────────────── T021 版本一致 ──────────────────────────────


@pytest.mark.asyncio
async def test_version_consistency_fail(tmp_outputs_dir, stub_state):
    """某 artifact 版本号不连续(跳号)→ fail。"""
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_p" / "events.jsonl"
    await _write_all(stub_state, "tsk_p", events)  # 参与度满足,隔离版本规则
    # MaterialPool 出现 v1, v3(缺 v2)→ 不连续
    result = ProcessReviewer().check("tsk_p", _log(
        ("MaterialPool", 1, "material"), ("MaterialPool", 3, "material"),
        ("ReportCore", 1), ("Outline", 1), ("Script", 1),
    ))
    assert not result.passed
    assert any("MaterialPool" in f.what and "版本" in f.what for f in result.findings)
    assert "material" in result.fix_targets


# ────────────────────────────── T022 依赖图 ──────────────────────────────


@pytest.mark.asyncio
async def test_dependency_order_fail(tmp_outputs_dir, stub_state):
    """Outline 早于 ReportCore 出现 → 依赖违例。"""
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_p" / "events.jsonl"
    await _write_all(stub_state, "tsk_p", events)
    result = ProcessReviewer().check("tsk_p", _log(
        ("MaterialPool", 1), ("Outline", 1), ("ReportCore", 1), ("Script", 1),
    ))
    assert not result.passed
    assert any("Outline" in f.what for f in result.findings)


# ────────────────────────────── T023 参与度 ──────────────────────────────


@pytest.mark.asyncio
async def test_participation_fail(tmp_outputs_dir, stub_state):
    """缺核心 artifact(只写 MaterialPool)→ fail + fix_targets 含缺失 producer。"""
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_p2" / "events.jsonl"
    state = stub_state("tsk_p2", events)
    await write_versioned(state=state, artifact_id="MaterialPool", payload={"x": 1},
                          producer="material", base_version=None, delta_summary="x")
    result = ProcessReviewer().check("tsk_p2", _log(("MaterialPool", 1)))
    assert not result.passed
    # ReportCore/Outline/Script 缺 → 它们的 producer 在 fix_targets
    assert {"point-extractor", "structure", "copywriter"} <= set(result.fix_targets)


# ────────────────────────────── T024 全 pass ──────────────────────────────


@pytest.mark.asyncio
async def test_all_pass(tmp_outputs_dir, stub_state):
    """4 artifact 齐 + 版本链一致 + 顺序对 → passed。"""
    events = tmp_outputs_dir / "data" / "outputs" / "tsk_ok" / "events.jsonl"
    await _write_all(stub_state, "tsk_ok", events)
    result = ProcessReviewer().check("tsk_ok", _log(
        ("MaterialPool", 1), ("ReportCore", 1), ("Outline", 1), ("Script", 1),
    ))
    assert result.passed, f"findings: {[f.what for f in result.findings]}"
    assert result.findings == ()
