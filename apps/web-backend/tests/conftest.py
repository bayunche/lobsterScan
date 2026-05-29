"""共享 pytest fixtures · P1 v2 群聊协议 + 状态模型层

详 specs/001-v2-chat-protocol-state/tasks.md T011 与 research.md §6。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.orchestrator.agent_backend import (
    AgentBackend, BackendCapabilities, get_default_backend, set_default_backend,
)
from app.openclaw.client import TurnResult


class ScriptedBackend(AgentBackend):
    """按声明顺序返回每个 step 的 TurnResult,不调 LLM。

    用法：
        scripts = [
            {"text": "...", "json_payload": {...}, "tokens": 100},
            ...
        ]
        backend = ScriptedBackend(scripts)
        # 测试中调用 pipeline.execute(...) 即可,backend 会按 scripts 顺序消化
    """

    def __init__(self, scripts: list[dict[str, Any]]) -> None:
        self.scripts = scripts
        self.i = 0
        self.calls: list[dict[str, Any]] = []

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name="scripted-test",
            streams_progress=False,
            supports_warmup=False,
            supports_pool=False,
            supports_partial_on_timeout=False,
        )

    async def run_turn(self, *, agent_id, message, model=None, timeout_sec=1800,
                       extra_env=None, on_progress=None) -> TurnResult:
        self.calls.append({"agent_id": agent_id, "message": message[:200]})
        if self.i >= len(self.scripts):
            # 没脚本了 → 返回空 TurnResult(让 pipeline 走"无 JSON"分支)
            return TurnResult(text="", provider="scripted", model="mock",
                              prompt_tokens=0, completion_tokens=0,
                              cache_read_tokens=0, total_tokens=0, raw={})
        spec = self.scripts[self.i]
        self.i += 1
        text = spec.get("text", "")
        if "json_payload" in spec and "```json" not in text:
            text = f"{text}\n\n```json\n{json.dumps(spec['json_payload'], ensure_ascii=False)}\n```"
        return TurnResult(
            text=text, provider="scripted", model=spec.get("model", "mock"),
            prompt_tokens=spec.get("prompt_tokens", 0),
            completion_tokens=spec.get("completion_tokens", 0),
            cache_read_tokens=0,
            total_tokens=spec.get("prompt_tokens", 0) + spec.get("completion_tokens", 0),
            raw={"_scripted": True, "agent_id": agent_id},
        )


@pytest.fixture
def mock_backend():
    """注入 ScriptedBackend 并在 teardown 时还原。

    用法：
        def test_xxx(mock_backend):
            backend = mock_backend([
                {"text": "...", "json_payload": {...}},
                ...
            ])
            # ... 运行 pipeline ...
            assert backend.i == 8     # 8 步都被调用
    """
    original = get_default_backend()

    def _make(scripts: list[dict[str, Any]]) -> ScriptedBackend:
        backend = ScriptedBackend(scripts)
        set_default_backend(backend)
        return backend

    yield _make
    set_default_backend(original)


@pytest.fixture
def tmp_outputs_dir(tmp_path, monkeypatch):
    """临时切到 tmp dir 跑测试,避免污染真 data/outputs/.

    yield 的路径就是 tmp 工作目录;cwd 已切到此处,data/outputs/ 相对路径有效。
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "outputs").mkdir(parents=True, exist_ok=True)
    yield tmp_path


def read_events_jsonl(path: Path | str) -> list[dict]:
    """逐行解析 events.jsonl,空行跳过。"""
    p = Path(path)
    if not p.is_file():
        return []
    rows: list[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


@pytest.fixture
def events_reader():
    """fixture 形式暴露 read_events_jsonl,便于参数注入。"""
    return read_events_jsonl


class StubRun:
    """轻量 stub TaskRun(只暴露 emit_v2/write_versioned 用到的字段)。"""
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


class StubState:
    """轻量 stub HarnessState（用于不需要起 EventBus 的单测）。

    模拟 emit_v2 接口（写 events.jsonl + 追加到 self.emitted），便于断言。
    """
    def __init__(self, task_id: str, events_jsonl: Path | str, is_v2: bool = True) -> None:
        self.run = StubRun(task_id)
        self.is_v2 = is_v2
        self.events_jsonl_path = Path(events_jsonl)
        self.emitted: list[dict] = []

    async def emit_v2(self, event) -> None:
        if not self.is_v2:
            return
        row = event.model_dump(mode="json", by_alias=True)
        self.emitted.append(row)
        self.events_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.fixture
def stub_state():
    """构造 (task_id, events_jsonl_path) → StubState。"""
    def _make(task_id: str, events_jsonl: Path | str, is_v2: bool = True) -> StubState:
        return StubState(task_id, events_jsonl, is_v2)
    return _make


@pytest.fixture
def stub_state_v2(stub_state):
    """便利 fixture · P2 v2 路径单测：默认 is_v2=True 的 StubState 工厂。

    用法：
        def test_xxx(stub_state_v2, tmp_outputs_dir):
            state = stub_state_v2("tsk_001", tmp_outputs_dir / "events.jsonl")
            await state.emit_v2(some_event)
            assert len(state.emitted) == 1
    """
    def _make(task_id: str, events_jsonl: Path | str) -> StubState:
        return stub_state(task_id, events_jsonl, is_v2=True)
    return _make


@pytest.fixture
def stub_state_v1(stub_state):
    """便利 fixture · P2 v1 回归断言：默认 is_v2=False 的 StubState 工厂。

    用法：
        def test_xxx(stub_state_v1, tmp_outputs_dir):
            state = stub_state_v1("tsk_001", tmp_outputs_dir / "events.jsonl")
            await state.emit_v2(some_event)
            assert state.emitted == []  # v1 短路,不写不发
    """
    def _make(task_id: str, events_jsonl: Path | str) -> StubState:
        return stub_state(task_id, events_jsonl, is_v2=False)
    return _make
