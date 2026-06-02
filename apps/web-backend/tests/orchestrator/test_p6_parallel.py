"""P6 US1 · html/video 真并行触发(spec 006-concurrency-fanout）

T006/T007 · FR-001/005 · US1-AC1/AC4

copywriting 完成 → fanout on 时 overlay mentions 含 html-designer + video-producer;
off 时仅单目标(html-designer)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.orchestrator import subscription as sub
from app.orchestrator import pipeline
from app.orchestrator.pipeline import StepState, _emit_v2_step_overlay, COPYWRITING_FANOUT


class _CapState:
    """捕获 emit_v2 的 AgentSpeak.mentions。"""
    def __init__(self, task_id, events_path):
        self.run = type("R", (), {"task_id": task_id})()
        self.is_v2 = True
        self.events_jsonl_path = Path(events_path)
        self.emitted = []

    async def emit_v2(self, event):
        self.emitted.append(event.model_dump(mode="json", by_alias=True))
        self.events_jsonl_path.parent.mkdir(parents=True, exist_ok=True)


def _copy_step():
    s = StepState(step="copywriting", label="文书", agent="copywriter")
    s.status = "success"
    s.output_json = {"script_md": "讲稿", "slides": []}
    return s


def _speak_mentions(state):
    for ev in state.emitted:
        if ev.get("msg_type") == "agent.speak":
            return ev.get("mentions") or []
    return None


@pytest.mark.asyncio
async def test_copywriting_fanout_on_mentions_html_and_video(monkeypatch, tmp_outputs_dir):
    """fanout on:copywriting 完成 → mentions 含 html-designer + video-producer(US1-AC1)。"""
    monkeypatch.setattr(sub, "V2_FANOUT", "on")
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "legacy")
    st = _CapState("tsk_p6", tmp_outputs_dir / "data/outputs/tsk_p6/events.jsonl")
    s = _copy_step()
    await _emit_v2_step_overlay(st, st.run, s, handoff={"to": "html-designer"},
                                producer_agent_id="copywriter")
    mentions = _speak_mentions(st)
    assert "html-designer" in mentions
    assert "video-producer" in mentions
    assert set(COPYWRITING_FANOUT) <= set(mentions)


@pytest.mark.asyncio
async def test_copywriting_fanout_off_single_mention(monkeypatch, tmp_outputs_dir):
    """fanout off(默认):copywriting → 仅单目标 html-designer(US1-AC4 / FR-005)。"""
    monkeypatch.setattr(sub, "V2_FANOUT", "off")
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "legacy")
    st = _CapState("tsk_p6b", tmp_outputs_dir / "data/outputs/tsk_p6b/events.jsonl")
    s = _copy_step()
    await _emit_v2_step_overlay(st, st.run, s, handoff={"to": "html-designer"},
                                producer_agent_id="copywriter")
    mentions = _speak_mentions(st)
    assert mentions == ["html-designer"]      # 单目标,无 video-producer
    assert "video-producer" not in mentions


@pytest.mark.asyncio
async def test_fanout_on_only_affects_copywriting(monkeypatch, tmp_outputs_dir):
    """fanout on 不影响非 copywriting step 的 mentions(只 copywriting 双 @)。"""
    monkeypatch.setattr(sub, "V2_FANOUT", "on")
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "legacy")
    st = _CapState("tsk_p6c", tmp_outputs_dir / "data/outputs/tsk_p6c/events.jsonl")
    s = StepState(step="structure_building", label="结构师", agent="structure")
    s.status = "success"
    s.output_json = {"chapters": []}
    await _emit_v2_step_overlay(st, st.run, s, handoff={"to": "upward-opt"},
                                producer_agent_id="structure")
    mentions = _speak_mentions(st)
    assert "video-producer" not in mentions    # structure 不受 fanout 影响


@pytest.mark.asyncio
async def test_copywriting_fanout_envelope_supplements_missing(monkeypatch, tmp_outputs_dir):
    """envelope 模式 fanout on:LLM 只给 html → 补齐 video(双保险,FR-001)。"""
    monkeypatch.setattr(sub, "V2_FANOUT", "on")
    monkeypatch.setattr(sub, "V2_PROMPT_MODE", "envelope")
    st = _CapState("tsk_p6d", tmp_outputs_dir / "data/outputs/tsk_p6d/events.jsonl")
    s = _copy_step()
    s._envelope = {"action": "speak", "mentions": ["html-designer"], "intent": "propose"}
    await _emit_v2_step_overlay(st, st.run, s, handoff={},
                                producer_agent_id="copywriter")
    mentions = _speak_mentions(st)
    assert "html-designer" in mentions
    assert "video-producer" in mentions        # 补齐缺失者
