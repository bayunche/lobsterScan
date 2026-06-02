"""P8 US3 · yes-man 防御(spec 008-ops-safety-net)

T015 · FR-017/018 + SC-005

- _quick_review:V2_YESMAN_DEFENSE=on → 发给 reviewer 的 message 含对立质疑段;off(默认)→ 不含;
  注入不破坏 {accept,comment,reason} 输出契约。
"""

from __future__ import annotations

import pytest

from app.orchestrator import subscription as sub
from app.orchestrator import pipeline


class _Step:
    agent = "material"
    label = "资料员 · 整理材料"
    output_json = {"payload": {"completed": ["A"]}}


class _Run:
    task_id = "tsk_y"
    duration = "3分钟"


class _Res:
    provider = "deepseek"
    model = "deepseek-chat"
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15
    text = '```json\n{"accept": true, "comment": "OK", "reason_if_reject": null}\n```'


def _patch_backend(monkeypatch):
    """捕获发给 reviewer 的 message;桩掉 env/usage 依赖。返回 captured 列表。"""
    captured: list[str] = []

    async def _fake_env(agent_id):  # type: ignore[no-untyped-def]
        return {}

    async def _fake_turn(agent_id, message, timeout_sec=90, extra_env=None, **kw):  # type: ignore[no-untyped-def]
        captured.append(message)
        return _Res()

    async def _fake_usage(**kw):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(pipeline, "env_for_agent", _fake_env)
    monkeypatch.setattr(pipeline, "run_agent_turn", _fake_turn)
    monkeypatch.setattr(pipeline, "report_usage", _fake_usage)
    return captured


_YESMAN_MARK = "对立质疑"


@pytest.mark.asyncio
async def test_yesman_on_injects_block(monkeypatch):
    monkeypatch.setattr(sub, "V2_YESMAN_DEFENSE", "on")
    captured = _patch_backend(monkeypatch)
    await pipeline._quick_review(_Step(), _Run())
    assert len(captured) == 1
    assert _YESMAN_MARK in captured[0]                 # FR-017
    assert "不要" in captured[0] and "看上去 OK" in captured[0]  # 对立质疑要点在场


@pytest.mark.asyncio
async def test_yesman_off_no_block(monkeypatch):
    monkeypatch.setattr(sub, "V2_YESMAN_DEFENSE", "off")
    captured = _patch_backend(monkeypatch)
    await pipeline._quick_review(_Step(), _Run())
    assert len(captured) == 1
    assert _YESMAN_MARK not in captured[0]             # FR-018:默认不含


@pytest.mark.asyncio
async def test_yesman_on_keeps_output_contract(monkeypatch):
    """注入对立质疑段不破坏 {accept,comment,reason} 解析(SC-005)。"""
    monkeypatch.setattr(sub, "V2_YESMAN_DEFENSE", "on")
    _patch_backend(monkeypatch)
    out = await pipeline._quick_review(_Step(), _Run())
    assert out["accept"] is True
    assert out["comment"] == "OK"
    assert out["reason"] is None
