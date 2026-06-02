"""P6 US2 · EventBus fan-out 并发分发(spec 006-concurrency-fanout）

T010/T011 · FR-006/007/009 · US2-AC1/2/3/4

- fanout on:多 handler 并发执行 + 单 handler 抛错隔离
- fanout off:串行顺序保持
"""

from __future__ import annotations

import asyncio

import pytest

from app.orchestrator import subscription as sub
from app.orchestrator.harness import EventBus, AgentEvent


def _ev(kind="x"):
    return AgentEvent(kind=kind, agent_id="a", step_key=None, payload={})


@pytest.mark.asyncio
async def test_fanout_on_runs_handlers_concurrently(monkeypatch):
    """fanout on:多 handler 并发(总耗时 ≈ 最慢单个,而非求和)。"""
    monkeypatch.setattr(sub, "V2_FANOUT", "on")
    bus = EventBus()
    order: list[str] = []

    async def h_slow(e):
        await asyncio.sleep(0.05)
        order.append("slow")

    async def h_fast(e):
        order.append("fast")

    bus.on("x", h_slow)
    bus.on("x", h_fast)
    await bus.emit(_ev())
    # 并发:fast 不必等 slow 完成才开始 → fast 先 append
    assert set(order) == {"slow", "fast"}
    assert order[0] == "fast"   # 并发下快的先完成


@pytest.mark.asyncio
async def test_fanout_on_isolates_handler_exception(monkeypatch):
    """fanout on:单 handler 抛错被隔离,其余照常,emit 不抛(FR-007)。"""
    monkeypatch.setattr(sub, "V2_FANOUT", "on")
    bus = EventBus()
    ran: list[str] = []

    async def h_boom(e):
        raise RuntimeError("boom")

    async def h_ok(e):
        ran.append("ok")

    bus.on("x", h_boom)
    bus.on("x", h_ok)
    await bus.emit(_ev())          # 不应抛
    assert ran == ["ok"]           # 另一个照常


@pytest.mark.asyncio
async def test_fanout_on_wildcard_and_kind_both_run(monkeypatch):
    """fanout on:wildcard + kind 订阅都被并发分发到。"""
    monkeypatch.setattr(sub, "V2_FANOUT", "on")
    seen: list[str] = []

    async def wild(e):
        seen.append("wild")

    async def kind(e):
        seen.append("kind")

    bus = EventBus()
    bus.on_any(wild)
    bus.on("x", kind)
    await bus.emit(_ev())
    assert set(seen) == {"wild", "kind"}


@pytest.mark.asyncio
async def test_fanout_off_serial_order(monkeypatch):
    """fanout off(默认):串行,注册顺序执行(US2-AC3 / FR-009)。"""
    monkeypatch.setattr(sub, "V2_FANOUT", "off")
    bus = EventBus()
    order: list[str] = []

    async def h1(e):
        await asyncio.sleep(0.02)
        order.append("h1")

    async def h2(e):
        order.append("h2")

    bus.on("x", h1)
    bus.on("x", h2)
    await bus.emit(_ev())
    assert order == ["h1", "h2"]   # 串行:h1 先(即便慢)


@pytest.mark.asyncio
async def test_fanout_default_is_off(monkeypatch):
    """不设 V2_FANOUT → _fanout_enabled_safe False(串行)。"""
    from app.orchestrator.harness import _fanout_enabled_safe
    monkeypatch.setattr(sub, "V2_FANOUT", "off")
    assert _fanout_enabled_safe() is False
    monkeypatch.setattr(sub, "V2_FANOUT", "on")
    assert _fanout_enabled_safe() is True
