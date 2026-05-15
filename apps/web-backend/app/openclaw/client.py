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
    """OpenClaw 在 JSON 前可能输出 config warning，找到顶层 { ... } 抠出来."""
    depth = 0
    start = -1
    for i, ch in enumerate(stdout):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(stdout[start:i + 1])
                except json.JSONDecodeError:
                    start = -1
                    continue
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
    timeout_sec: int = 180,
    extra_env: dict[str, str] | None = None,
) -> TurnResult:
    """触发一次 agent turn，返回结构化结果。

    架构：每个角色独立 OpenClaw 实例（profile = lobster-<id>），
    state/config/auth 完全隔离。运行模式 --local（embedded brain，不经 gateway）。

    extra_env: 注入给 subprocess 的环境变量；Agent 用 Bash 跑脚本时可用，
    比如 video-producer 用 minimax-tts skill 时需要 MINIMAX_API_KEY。
    """
    import os as _os
    profile = f"lobster-{agent_id}"
    cmd = [
        "openclaw", "--profile", profile,
        "agent", "--agent", "main", "--local", "--json",
        "-m", message,
    ]
    if model:
        cmd += ["--model", model]
    log.info("openclaw turn · profile=%s (%s chars) · env_extra=%s",
             profile, len(message), list((extra_env or {}).keys()))
    env = _os.environ.copy()
    if extra_env:
        env.update(extra_env)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        raise

    if proc.returncode != 0:
        raise RuntimeError(
            f"openclaw agent exit={proc.returncode}: "
            f"{(err or b'').decode('utf-8', errors='replace')[:500]}"
        )

    text = out.decode("utf-8", errors="replace")
    data = _parse_top_object(text)

    visible = _walk_find(data, "finalAssistantVisibleText") or _walk_find(data, "finalAssistantRawText") or ""
    trace = _walk_find(data, "executionTrace") or {}
    usage = _walk_find(data, "usage") or {}

    return TurnResult(
        text=visible,
        provider=trace.get("winnerProvider"),
        model=trace.get("winnerModel"),
        prompt_tokens=int(usage.get("input") or 0),
        completion_tokens=int(usage.get("output") or 0),
        cache_read_tokens=int(usage.get("cacheRead") or 0),
        total_tokens=int(usage.get("total") or 0),
        raw=data,
    )


# ---- 文本里提取 JSON 块（Agent 输出常用 ```json ... ``` 包裹） ----
_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.S)


def extract_json(text: str) -> dict[str, Any] | None:
    """从 agent 的回复里抠出第一个 JSON 代码块（Agent 提示词要求这种格式）."""
    for m in _JSON_FENCE.finditer(text):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
    # 兜底：尝试直接 parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None
