"""P5 US1 · transcript-aware prompt(spec 005-transcript-aware-prompt）

T008/T009 · FR-001/002/003/004/005/013/014

- _transcript_block:有发言+artifact→含中文段、超 K 截断、空→空串、observer None→空串
- _step_prompt 集成:envelope 模式含群聊上下文段;legacy 不含
"""

from __future__ import annotations

import pytest

from app.orchestrator import subscription as sub
from app.orchestrator import pipeline


class _Obs:
    """极小 observer mock:只暴露 _recent + _artifact_log。"""
    def __init__(self, recent, artifacts):
        self._recent = recent
        self._artifact_log = artifacts


class _State:
    def __init__(self, observer):
        self.observer = observer


# ────────────────────────── _transcript_block(T008) ──────────────────────────

def test_transcript_block_renders_speaks_and_artifacts():
    obs = _Obs(
        recent=["资料员:素材就绪", "分析师:三条重点"],
        artifacts=[{"id": "ReportCore", "version": 1, "producer": "point-extractor"}],
    )
    out = pipeline._transcript_block(_State(obs), k=8)
    assert "群聊上下文" in out
    assert "资料员:素材就绪" in out
    assert "分析师:三条重点" in out
    assert "当前可见产物" in out
    assert "ReportCore v1" in out
    # producer 用 AGENT_DISPLAY 中文名
    assert "分析师" in out


def test_transcript_block_caps_at_k():
    obs = _Obs(recent=[f"发言{i}" for i in range(20)], artifacts=[])
    out = pipeline._transcript_block(_State(obs), k=5)
    assert "发言19" in out and "发言15" in out   # 最近 5 条
    assert "发言14" not in out                    # 被截断
    assert "发言0" not in out


def test_transcript_block_empty_when_no_activity():
    """任务起点:无发言无 artifact → 空串(FR-004)。"""
    assert pipeline._transcript_block(_State(_Obs([], [])), k=8) == ""


def test_transcript_block_empty_when_no_observer():
    """observer None → 空串,不报错(FR-016 降级)。"""
    assert pipeline._transcript_block(_State(None), k=8) == ""
    # state 完全没有 observer 属性也不炸
    assert pipeline._transcript_block(object(), k=8) == ""


def test_transcript_block_default_k_reads_config(monkeypatch):
    monkeypatch.setattr(sub, "V2_TRANSCRIPT_K", 3)
    obs = _Obs(recent=[f"x{i}" for i in range(10)], artifacts=[])
    out = pipeline._transcript_block(_State(obs))   # k=None → 读 config
    assert "x9" in out and "x7" in out and "x6" not in out


# ────────────────────────── _step_prompt 集成(T009) ──────────────────────────

def _run():
    return type("R", (), {
        "report_type": "project_progress", "audience": "直属领导",
        "duration": "1分钟", "style": "", "supplement": "",
        "agent_briefs": {},
    })()


def test_step_prompt_includes_transcript_in_envelope_mode(monkeypatch):
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "envelope")
    obs = _Obs(recent=["资料员:素材就绪"], artifacts=[])
    prev = {"__state__": _State(obs)}
    prompt = pipeline._step_prompt("structure_building", _run(), prev)
    assert "群聊上下文" in prompt
    assert "资料员:素材就绪" in prompt


def test_step_prompt_no_transcript_in_legacy_mode(monkeypatch):
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "legacy")
    obs = _Obs(recent=["资料员:素材就绪"], artifacts=[])
    prev = {"__state__": _State(obs)}
    prompt = pipeline._step_prompt("structure_building", _run(), prev)
    assert "群聊上下文" not in prompt   # legacy 短路(FR-014)


def test_step_prompt_envelope_uses_envelope_rule(monkeypatch):
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "envelope")
    prompt = pipeline._step_prompt("structure_building", _run(), {"__state__": _State(None)})
    assert "群聊信封" in prompt          # _ENVELOPE_RULE 生效(FR-006)
    assert '"action"' in prompt


def test_step_prompt_legacy_uses_json_rule(monkeypatch):
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "legacy")
    prompt = pipeline._step_prompt("structure_building", _run(), {})
    assert "群聊信封" not in prompt
    assert "结构化结果" in prompt        # JSON_RULE 生效
