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
    # OPENCLAW_BIN 由 scripts/dev.sh 注入(优先项目自托管的 ./node_modules/.bin/openclaw);
    # 兜底再用 PATH 里的 "openclaw",方便单独跑 uvicorn 也能工作。
    oc_bin = _os.environ.get("OPENCLAW_BIN") or "openclaw"
    cmd = [
        oc_bin, "--profile", profile,
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
    # OpenClaw 某些 plugin(如 lossless-claw)会把 agent 输出包成 payloads 数组,
    # 此时没有 finalAssistantVisibleText,visible 文本在 payloads[i].text 里。
    if not visible:
        payloads = _walk_find(data, "payloads")
        if isinstance(payloads, list) and payloads:
            parts = [p.get("text") for p in payloads if isinstance(p, dict) and p.get("text")]
            visible = "\n".join(parts) if parts else ""
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
