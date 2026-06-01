"""P5 US2/US3 · envelope 模式 _run_step 解析端到端(spec 005-transcript-aware-prompt）

T015/T016 · SC-002/SC-005 · US2-AC1/2/5

用 ScriptedBackend 让 agent 返回信封 text → _run_step 解析 → 断言:
- envelope 模式:artifact 回填 s.output_json、信封头存 s._envelope
- envelope 包裹再解出的产物与 legacy 直出逐字段一致(SC-005)
"""

from __future__ import annotations

import json

import pytest

from app.orchestrator import subscription as sub
from app.orchestrator import pipeline
from app.orchestrator.pipeline import StepState, TaskRun, _run_step
from app.openclaw.client import TurnResult


def _run() -> TaskRun:
    return TaskRun(
        task_id="tsk_p5", title="周报", report_type="project_progress",
        audience="直属领导", duration="1分钟", style="", supplement="",
        raw_text="本周完成 A/B/C",
    )


class _OneShot:
    """单次返回固定 text 的 backend。"""
    def __init__(self, text):
        self.text = text

    @property
    def capabilities(self):
        from app.orchestrator.agent_backend import BackendCapabilities
        return BackendCapabilities(name="oneshot", streams_progress=False,
                                   supports_warmup=False, supports_pool=False,
                                   supports_partial_on_timeout=False)

    async def run_turn(self, *, agent_id, message, model=None, timeout_sec=1800,
                       extra_env=None, on_progress=None):
        return TurnResult(text=self.text, provider="scripted", model="mock",
                          prompt_tokens=0, completion_tokens=0,
                          cache_read_tokens=0, total_tokens=0, raw={})


def _set_backend(monkeypatch, text):
    from app.orchestrator import agent_backend
    monkeypatch.setattr(agent_backend, "_default_backend", _OneShot(text), raising=False)
    # pipeline 经 get_default_backend / run_agent_turn 取;直接 patch get_default_backend
    monkeypatch.setattr(agent_backend, "get_default_backend", lambda: _OneShot(text))


ARTIFACT = {"chapters": [{"title": "进展"}], "pattern": "总分总"}


@pytest.mark.asyncio
async def test_envelope_run_step_unwraps_artifact(monkeypatch, tmp_outputs_dir):
    """envelope 模式:信封 text → artifact 回填 output_json + _envelope 暂存(US2-AC1)。"""
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "envelope")
    envelope = {"action": "speak", "mentions": ["upward-opt"], "intent": "propose",
                "reason": "", "artifact": ARTIFACT}
    text = f"思考过程……\n\n```json\n{json.dumps(envelope, ensure_ascii=False)}\n```"
    _set_backend(monkeypatch, text)

    s = StepState(step="structure_building", label="结构师", agent="structure")
    await _run_step(s, _run(), {})

    assert s.status == "success"
    assert s.output_json == ARTIFACT                       # artifact 回填(FR-008)
    assert s._envelope["action"] == "speak"                # 信封头暂存(FR-009)
    assert s._envelope["mentions"] == ["upward-opt"]


@pytest.mark.asyncio
async def test_envelope_vs_legacy_artifact_field_identical(monkeypatch, tmp_outputs_dir):
    """SC-005:envelope 包裹再解出 == legacy 直出,逐字段一致。"""
    # legacy:agent 直接输出 typed JSON
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "legacy")
    legacy_text = f"思考……\n\n```json\n{json.dumps(ARTIFACT, ensure_ascii=False)}\n```"
    _set_backend(monkeypatch, legacy_text)
    s_legacy = StepState(step="structure_building", label="结构师", agent="structure")
    await _run_step(s_legacy, _run(), {})

    # envelope:同一份 typed JSON 包进 artifact
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "envelope")
    env = {"action": "speak", "mentions": [], "artifact": ARTIFACT}
    env_text = f"思考……\n\n```json\n{json.dumps(env, ensure_ascii=False)}\n```"
    _set_backend(monkeypatch, env_text)
    s_env = StepState(step="structure_building", label="结构师", agent="structure")
    await _run_step(s_env, _run(), {})

    assert s_legacy.output_json == s_env.output_json       # 逐字段一致


@pytest.mark.asyncio
async def test_envelope_silent_sets_envelope_action(monkeypatch, tmp_outputs_dir):
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "envelope")
    env = {"action": "silent", "reason": "等上游", "mentions": []}
    text = f"想了想……\n\n```json\n{json.dumps(env, ensure_ascii=False)}\n```"
    _set_backend(monkeypatch, text)
    s = StepState(step="structure_building", label="结构师", agent="structure")
    await _run_step(s, _run(), {})
    assert s._envelope["action"] == "silent"
    assert s.output_json == {}                              # silent 无 artifact
