"""OpenClaw Client · subprocess 封装

V0 阶段我们用 `openclaw agent` CLI 触发 agent turn，避免一次性写完整 WebSocket Channel Plugin。
JSON 输出里能拿到：
  - finalAssistantVisibleText  · 该 agent 的最终回复
  - executionTrace.winnerProvider/winnerModel
  - usage.input / output / cacheRead / total · 用于 token 计费
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("openclaw.client")


@dataclass
class TurnResult:
    text: str
    provider: str | None
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    total_tokens: int
    raw: dict[str, Any]


def _parse_top_object(stdout: str) -> dict[str, Any]:
    """OpenClaw 在 JSON 前可能输出 config warning,找到顶层 { ... } 抠出来.

    用 json.JSONDecoder().raw_decode — 它会跳过空白,从指定位置读一个完整 JSON value
    并返回 (value, end_index)。对字符串内的 `{` `}` 正确处理(老版本数大括号会被字符串
    里的反引号 / json fence ```json{...}``` 撞挂,见 memory [openclaw-client-parser-bug])。
    """
    decoder = json.JSONDecoder()
    i = stdout.find("{")
    while i != -1:
        try:
            obj, _end = decoder.raw_decode(stdout, i)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i = stdout.find("{", i + 1)
    raise RuntimeError(f"no JSON object in stdout · head={stdout[:200]!r}")


def _walk_find(obj: Any, key: str) -> Any:
    """递归找第一个匹配 key 的值."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                return v
            r = _walk_find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _walk_find(v, key)
            if r is not None:
                return r
    return None


async def run_agent_turn(
    *,
    agent_id: str,
    message: str,
    model: str | None = None,
    timeout_sec: int = 900,
    extra_env: dict[str, str] | None = None,
    on_progress=None,
) -> TurnResult:
    """触发一次 agent turn,返回结构化结果。

    现在是 thin wrapper · 真正实现见 `app/orchestrator/agent_backend.py:OpenClawSubprocessBackend`,
    通过 `get_default_backend()` 单例取(测试可 set_default_backend(mock) 注入)。

    抽象的好处:
      - read-loop 读 stdout/stderr,timeout 时累积 buffer 不丢
      - on_progress callback 钩子(给前端 SSE 推 agent 中间事件)
      - 未来切换到 OpenClawGatewayBackend / DirectLLMBackend 不动 pipeline
    """
    # 延迟 import 避免循环依赖(agent_backend 反过来 import 本模块的 TurnResult)
    from ..orchestrator.agent_backend import get_default_backend
    backend = get_default_backend()
    return await backend.run_turn(
        agent_id=agent_id, message=message, model=model,
        timeout_sec=timeout_sec, extra_env=extra_env,
        on_progress=on_progress,
    )


# ---- 文本里提取 JSON 块（Agent 输出常用 ```json ... ``` 包裹） ----
_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.S)


def _fix_inner_quotes(raw: str) -> str:
    """LLM 写 `"note": "...岗位\"人工智能\"..."` 时 inner-quote 没转义。
    扫一遍 string token,把不合 JSON 文法的孤立 ASCII " 替换成 「」(配对) 或 \\"."""
    out: list[str] = []
    i = 0
    n = len(raw)
    in_str = False
    while i < n:
        c = raw[i]
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
            i += 1
            continue
        # 进入 string 内部
        if c == '\\':
            # 已经是转义,原样带过下一个字符
            out.append(c)
            if i + 1 < n:
                out.append(raw[i + 1])
                i += 2
            else:
                i += 1
            continue
        if c == '"':
            # 看后面是不是合法的 JSON string 终止符
            j = i + 1
            while j < n and raw[j] in ' \t':
                j += 1
            if j < n and raw[j] in ',:}]\n\r':
                # 合法终止
                out.append(c)
                in_str = False
                i += 1
                continue
            # 否则视为 inner-quote 没转义 — 找下一个 " 作为配对
            k = i + 1
            depth_safe = 0
            while k < n and depth_safe < 80:
                if raw[k] == '\\':
                    k += 2; depth_safe += 1; continue
                if raw[k] == '"':
                    break
                k += 1; depth_safe += 1
            if k < n and raw[k] == '"':
                # 看 k 后是不是同样不合法终止 → 是的话才说明是 inner-quote 对
                m = k + 1
                while m < n and raw[m] in ' \t':
                    m += 1
                if m < n and raw[m] in ',:}]\n\r':
                    # k 是合法 string 终止符,不是 inner-quote 对,保持
                    out.append(c)
                    in_str = False
                    i += 1
                    continue
                # k 也是 inner-quote → 替成 「」
                inner = raw[i + 1:k]
                out.append('「')
                out.append(inner.replace('"', '\\"'))
                out.append('」')
                i = k + 1
                continue
            # 没找到配对 → 保持原样
            out.append(c)
            in_str = False
            i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def _try_loose_json(raw: str) -> dict[str, Any] | None:
    """LLM 输出 JSON 偶尔有 inner-quote 没转义 / trailing comma / 单引号,做几轮宽松修复."""
    # 1. trailing comma 清掉
    cleaned = re.sub(r",(\s*[}\]])", r"\1", raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 2. raw_decode 局部解析 — 取最大可解析前缀
    try:
        dec = json.JSONDecoder()
        obj, _ = dec.raw_decode(cleaned.lstrip())
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # 3. inner-quote 救星:状态机扫一遍替换
    fixed = _fix_inner_quotes(cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    return None


def extract_json(text: str) -> dict[str, Any] | None:
    """从 agent 的回复里抠出第一个 JSON 代码块（Agent 提示词要求这种格式）."""
    # 先按 fence 严格 parse
    for m in _JSON_FENCE.finditer(text):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 再按 fence loose parse(救 inner-quote / trailing comma)
    for m in _JSON_FENCE.finditer(text):
        loose = _try_loose_json(m.group(1))
        if loose is not None:
            return loose
    # 兜底:整段直接尝试 strict + loose
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return _try_loose_json(text)
