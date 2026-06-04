"""openclaw 二进制解析 · Windows 真跑前置(docs/issues/windows-real-pipeline-runnability.md)

沉淀 P8 真 LLM e2e 踩坑:未设 OPENCLAW_BIN 时,裸默认 "openclaw" 推不出 openclaw.mjs,
fallback 裸 bin → Windows CreateProcess WinError 2 每 turn 失败。修复:_find_openclaw_mjs
从本文件向上逐级找 node_modules/openclaw/openclaw.mjs,不依赖 CWD / 不强依赖 OPENCLAW_BIN。
"""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.agent_backend import OpenClawSubprocessBackend


def test_find_mjs_via_walk_up_without_env():
    """裸默认 bin(未设 OPENCLAW_BIN)→ 仍能从仓库向上找到真实 openclaw.mjs。"""
    be = OpenClawSubprocessBackend(openclaw_bin="openclaw")
    mjs = be._find_openclaw_mjs()
    assert mjs is not None, "应能向上定位 node_modules/openclaw/openclaw.mjs"
    assert mjs.is_file()
    assert mjs.name == "openclaw.mjs"


def test_find_mjs_via_explicit_bin():
    """显式 OPENCLAW_BIN(node_modules/.bin/openclaw)→ 推导 ../../openclaw/openclaw.mjs。"""
    # 从真实 mjs 反推一个 .bin/openclaw 路径,验证显式推导分支
    be0 = OpenClawSubprocessBackend(openclaw_bin="openclaw")
    real_mjs = be0._find_openclaw_mjs()
    assert real_mjs is not None
    fake_bin = str(real_mjs.parent.parent / ".bin" / "openclaw")  # node_modules/.bin/openclaw
    be = OpenClawSubprocessBackend(openclaw_bin=fake_bin)
    mjs = be._find_openclaw_mjs()
    assert mjs is not None and mjs.is_file()


def test_resolve_argv_prefix_non_windows(monkeypatch):
    """非 Windows → 原样用 bin(不绕 node)。"""
    import app.orchestrator.agent_backend as ab
    monkeypatch.setattr(ab.sys, "platform", "linux")
    be = OpenClawSubprocessBackend(openclaw_bin="openclaw")
    assert be._resolve_argv_prefix() == ["openclaw"]


def test_resolve_argv_prefix_windows_uses_node_mjs(monkeypatch):
    """Windows + 能找到 mjs → [node, openclaw.mjs](绕过 sh shim,根治 WinError 2)。"""
    import app.orchestrator.agent_backend as ab
    monkeypatch.setattr(ab.sys, "platform", "win32")
    be = OpenClawSubprocessBackend(openclaw_bin="openclaw")
    prefix = be._resolve_argv_prefix()
    assert len(prefix) == 2
    assert prefix[1].endswith("openclaw.mjs")
    assert Path(prefix[1]).is_file()


def test_resolve_argv_prefix_windows_fallback_when_no_mjs(monkeypatch, tmp_path):
    """Windows 但找不到 mjs → fallback 裸 bin(降级,不抛)。"""
    import app.orchestrator.agent_backend as ab
    monkeypatch.setattr(ab.sys, "platform", "win32")
    be = OpenClawSubprocessBackend(openclaw_bin="openclaw")
    # 强制 _find 返回 None 模拟无 node_modules 环境
    monkeypatch.setattr(be, "_find_openclaw_mjs", lambda: None)
    assert be._resolve_argv_prefix() == ["openclaw"]
