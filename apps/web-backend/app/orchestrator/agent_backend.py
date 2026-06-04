"""Agent Backend Abstraction

把"跑一个 agent turn"这件事从具体执行机制(目前 = openclaw subprocess)中抽出来,
让 pipeline 不直接依赖 openclaw CLI,未来能在不改 pipeline 的前提下换:
  - OpenClawGatewayBackend  · 走 ws://127.0.0.1:18789(复用 model warm-up)
  - DirectLLMBackend         · 直接调 anthropic-sdk / deepseek-sdk(完全控制 retry/streaming)
  - 任何第三方 agent framework

当前唯一实现:
  - OpenClawSubprocessBackend · fork `openclaw agent --local --json -m message`

抽象的 4 个关键点:
  1. run_turn():异步跑一个 turn,返回 TurnResult(text + tokens + provider/model)
  2. on_progress 回调:Backend 跑过程中 emit 中间事件(stdout 行 / 阶段切换 / token 计数)
     给 caller(Pipeline)观察进度。OpenClaw 自己 buffer 输出,实际能 emit 的事件稀疏,
     但接口就绪;未来 DirectLLM 可以真 stream Claude SSE token。
  3. timeout 行为:必须 kill 子任务,但**保留已积累的 stdout/stderr buffer** 给上层尝试
     恢复(当前 OpenClaw subprocess 全 buffered,partial 通常是空;但读 loop 不丢)
  4. warmup() / shutdown():pool / connection 管理钩子(当前 subprocess no-op)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ..openclaw.client import TurnResult, _parse_top_object, _walk_find

log = logging.getLogger("orchestrator.agent_backend")


ProgressKind = str  # "start" | "stdout_line" | "stderr_line" | "wait" | "done" | "timeout" | "error"


# provider name → (env model key, hardcoded fallback model id)
# 注:用于 .env 兜底路径;新代码请走 _model_from_openclaw_json(管台 → openclaw.json)。
_PROVIDER_MODEL_TABLE: dict[str, tuple[str, str]] = {
    "anthropic": ("ANTHROPIC_DEFAULT_MODEL", "claude-sonnet-4-6"),
    "deepseek":  ("DEEPSEEK_DEFAULT_MODEL",  "deepseek-v4-flash"),
    "minimax":   ("MINIMAX_LLM_MODEL",       "MiniMax-Text-01"),
    "qwen":      ("QWEN_LLM_MODEL",          "qwen3-max"),
    "glm":       ("GLM_LLM_MODEL",           "glm-4-plus"),
    "openai":    ("OPENAI_DEFAULT_MODEL",    "gpt-4.1"),
    "dashscope": ("DASHSCOPE_DEFAULT_MODEL", "qwen-plus"),
}


def _model_from_openclaw_json() -> str:
    """管台→openclaw.json `providers` 节点 → openclaw `<provider>/<model>` 字符串。

    管台 PUT /admin/api/config 把用户选的 LLM provider/model 写到 openclaw.json 的
    `providers.default` 和 `providers.<provider>.model`,这里就是把那对值读出来。

    任何环节缺失返回空串,让上层 fallback 到 .env 链;**不抛错**——开发初装 / admin 未
    起 / 文件刚被改坏的瞬间都不应该让 pipeline 失败,.env 始终能兜底。
    """
    # 复用 secrets._read_oc_section,与 image/video/tts 走同一份 IO 路径
    from ..openclaw.secrets import _read_oc_section
    providers = _read_oc_section("providers")
    if not providers:
        return ""
    provider = (providers.get("default") or "").strip().lower()
    if not provider:
        return ""
    model = ((providers.get(provider) or {}).get("model") or "").strip()
    if not model:
        # default 指向的 provider 没配 model — 用该 provider 的硬编码 fallback,
        # 至少让 admin 的"选 provider 但忘填 model"的常见场景能跑起来。
        model = _PROVIDER_MODEL_TABLE.get(provider, ("", ""))[1]
        if not model:
            return ""
    return f"{provider}/{model}"


def _model_from_llm_provider_env() -> str:
    """.env 的 LLM_PROVIDER 路由出 openclaw `<provider>/<model>` 字符串。

    **仅作兜底**:openclaw.json 不可读时(开发初装 / admin 未起)用这条。
    长期路径是管台写 openclaw.json,见 _model_from_openclaw_json。
    """
    provider = (os.environ.get("LLM_PROVIDER") or "anthropic").strip().lower()
    model_key, fallback = _PROVIDER_MODEL_TABLE.get(
        provider, ("ANTHROPIC_DEFAULT_MODEL", "claude-sonnet-4-6")
    )
    model = (os.environ.get(model_key) or fallback).strip()
    return f"{provider}/{model}"


@dataclass
class AgentProgress:
    """跑过程中的中间事件 — backend 通过 on_progress callback emit."""
    kind: ProgressKind
    agent_id: str
    text: str = ""
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


ProgressHandler = Callable[[AgentProgress], Awaitable[None]]


@dataclass
class BackendCapabilities:
    """Backend 报告自己支持的能力,caller 可据此判断是否启用某些 UI / 行为."""
    name: str
    streams_progress: bool           # 中间是否有 token-level / step-level 进度
    supports_warmup: bool            # warmup() 是否真有效果(预热模型)
    supports_pool: bool              # 是否复用实例(避免每 turn fork)
    supports_partial_on_timeout: bool  # timeout 时能否给回 partial 输出


class AgentBackend(ABC):
    """Agent 执行后端接口。一个 pipeline 用一个 backend 实例。"""

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        ...

    @abstractmethod
    async def run_turn(
        self,
        *,
        agent_id: str,
        message: str,
        model: str | None = None,
        timeout_sec: int = 1800,
        extra_env: dict[str, str] | None = None,
        on_progress: Optional[ProgressHandler] = None,
    ) -> TurnResult:
        """跑一个 agent turn。失败抛异常(包括 timeout / non-zero exit / parse fail)."""
        ...

    async def warmup(self, agent_ids: list[str]) -> None:
        """提前热身指定 agent — 默认 no-op."""
        return None

    async def shutdown(self) -> None:
        """关闭/清理资源 — 默认 no-op."""
        return None


# ─────────────────────────────────────────────────────────────
# OpenClawSubprocessBackend · fork-and-wait,但用 read-loop 而不是 communicate()
# ─────────────────────────────────────────────────────────────


class OpenClawSubprocessBackend(AgentBackend):
    """当前实际实现:fork openclaw agent CLI · 一次一个 turn.

    比老 `run_agent_turn` 改进:
      - 读 stdout/stderr line-by-line(async for line in stream),不用 communicate()
      - 累积 buffer 到 list,timeout / non-zero 时仍能拿 partial 尝试 parse
      - 每行 emit on_progress(kind=stdout_line/stderr_line),给前端 SSE 用
    """

    def __init__(self, *, openclaw_bin: str | None = None,
                 default_model: str | None = None,
                 thinking: str | None = None) -> None:
        self._bin = openclaw_bin or os.environ.get("OPENCLAW_BIN") or "openclaw"
        # Fix A(Windows pipeline runnability):node_modules/.bin/openclaw 是 POSIX sh shim,
        # Windows CreateProcess 不识别 shebang → [WinError 193]。改用 `node <openclaw.mjs>`。
        self._argv_prefix = self._resolve_argv_prefix()
        # **不在 __init__ 算定 default_model**——管台改 openclaw.json 后要立刻生效,
        # 见 _resolve_default_model:每个 turn 才读。external override 保留为 explicit 字段。
        self._explicit_default_model = default_model
        self._thinking = thinking or os.environ.get("OPENCLAW_THINKING") or "low"

    def _find_openclaw_mjs(self) -> Path | None:
        """定位 `node_modules/openclaw/openclaw.mjs`(`#!/usr/bin/env node` 入口)。

        候选顺序:
          ① 显式 bin(OPENCLAW_BIN / 构造参数,通常 `.bin/openclaw`)推导 `../../openclaw/openclaw.mjs`;
          ② 从**本文件**向上逐级找 `node_modules/openclaw/openclaw.mjs`——根治裸默认 "openclaw"
             推导失败(P8 e2e 踩坑:不设 OPENCLAW_BIN 时 fallback 裸 bin → Windows WinError 2),
             且不依赖 CWD。返回首个存在者,否则 None。
        """
        candidates: list[Path] = []
        # ① 显式 bin 推导(仅当不是裸默认名时,裸名 resolve 受 CWD 影响不可靠)
        if self._bin and self._bin != "openclaw":
            try:
                candidates.append(Path(self._bin).resolve().parent.parent / "openclaw" / "openclaw.mjs")
            except Exception:  # noqa: BLE001
                pass
        # ② 从本文件向上逐级找 node_modules(不依赖 CWD / 不强依赖 OPENCLAW_BIN)
        for parent in Path(__file__).resolve().parents:
            candidates.append(parent / "node_modules" / "openclaw" / "openclaw.mjs")
        for c in candidates:
            try:
                if c.is_file():
                    return c
            except Exception:  # noqa: BLE001
                continue
        return None

    def _resolve_argv_prefix(self) -> list[str]:
        """命令前缀:非 Windows = [bin];Windows = [node, openclaw.mjs](绕过 sh shim)。

        Windows CreateProcess 不识别无扩展名的 POSIX sh shim(`node_modules/.bin/openclaw`)→
        WinError 2/193。故 Windows 下解析出 `openclaw.mjs` 用 `node` 直跑;
        找不到 mjs / 非 Windows 时原样用 self._bin。详 docs/issues/windows-real-pipeline-runnability.md。
        """
        if sys.platform != "win32":
            return [self._bin]
        mjs = self._find_openclaw_mjs()
        if mjs is not None:
            node = shutil.which("node") or "node"
            log.info("Windows: openclaw via node %s", mjs)
            return [node, str(mjs)]
        log.warning("openclaw.mjs not found (bin=%s); fallback to bare bin", self._bin)
        return [self._bin]

    def _resolve_default_model(self) -> str:
        """决定本 turn 给 openclaw 的 --model 参数。优先级:

        1. __init__ 显式传入的 default_model(测试 / 特殊场景 override)
        2. **管台 → openclaw.json**(`providers.default` + `providers.<name>.model`)
        3. .env 的 `OPENCLAW_DEFAULT_MODEL`(老式 escape hatch,保留向后兼容)
        4. .env 的 `LLM_PROVIDER` 派生(开发初装 / admin 未起时兜底)

        2 是"管台是单一真值"的实现位置;3/4 仅作降级,长期不依赖。
        """
        return (
            self._explicit_default_model
            or _model_from_openclaw_json()
            or os.environ.get("OPENCLAW_DEFAULT_MODEL")
            or _model_from_llm_provider_env()
        )

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name="openclaw-subprocess",
            streams_progress=False,        # openclaw 自己 buffer 输出
            supports_warmup=False,
            supports_pool=False,
            supports_partial_on_timeout=True,  # buffer 保留,但通常为空
        )

    async def run_turn(
        self,
        *,
        agent_id: str,
        message: str,
        model: str | None = None,
        timeout_sec: int = 1800,
        extra_env: dict[str, str] | None = None,
        on_progress: Optional[ProgressHandler] = None,
    ) -> TurnResult:
        profile = f"lobster-{agent_id}"
        session_id = uuid.uuid4().hex
        effective_model = model or self._resolve_default_model()
        cmd = [
            *self._argv_prefix, "--profile", profile,
            "agent", "--agent", "main", "--local", "--json",
            "--session-id", session_id,
            "-m", message,
            "--model", effective_model,
        ]
        if self._thinking and self._thinking != "off":
            cmd += ["--thinking", self._thinking]

        log.info("openclaw turn · profile=%s thinking=%s (%s chars) · env_extra=%s",
                 profile, self._thinking, len(message), list((extra_env or {}).keys()))

        async def _emit(kind: str, text: str = "", **payload):
            if on_progress is None:
                return
            try:
                await on_progress(AgentProgress(
                    kind=kind, agent_id=agent_id, text=text, payload=payload,
                ))
            except Exception as e:  # noqa: BLE001
                log.warning("on_progress(%s) handler crashed: %s", kind, e)

        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        await _emit("start", payload={"model": effective_model, "session_id": session_id})

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        stdout_buf: list[bytes] = []
        stderr_buf: list[bytes] = []

        async def _drain(stream, kind: str, buf: list[bytes]) -> None:
            while True:
                line = await stream.readline()
                if not line:
                    return
                buf.append(line)
                # 只 emit 非空 + 解码可读的 line
                try:
                    s = line.decode("utf-8", errors="replace").rstrip()
                    if s:
                        await _emit(kind, text=s)
                except Exception:  # noqa: BLE001
                    pass

        timed_out = False
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _drain(proc.stdout, "stdout_line", stdout_buf),
                    _drain(proc.stderr, "stderr_line", stderr_buf),
                    proc.wait(),
                ),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

        stdout_text = b"".join(stdout_buf).decode("utf-8", errors="replace")
        stderr_text = b"".join(stderr_buf).decode("utf-8", errors="replace")

        if timed_out:
            await _emit("timeout", payload={
                "timeout_sec": timeout_sec,
                "stdout_bytes": sum(len(b) for b in stdout_buf),
                "stderr_bytes": sum(len(b) for b in stderr_buf),
            })
            # 尝试 partial parse — 给 caller 一个机会
            partial = _try_parse_partial(stdout_text)
            if partial is not None:
                log.warning("timeout but recovered partial JSON from stdout(%d bytes)", len(stdout_text))
                return partial
            raise asyncio.TimeoutError(
                f"openclaw turn timeout after {timeout_sec}s · "
                f"stdout={len(stdout_text)}B stderr={len(stderr_text)}B · "
                f"stderr tail: {stderr_text[-300:]}"
            )

        if proc.returncode != 0:
            await _emit("error", text=f"exit {proc.returncode}",
                        payload={"exit_code": proc.returncode,
                                 "stderr_tail": stderr_text[-500:]})
            raise RuntimeError(
                f"openclaw agent exit={proc.returncode}: {stderr_text[-500:]}"
            )

        await _emit("done", payload={
            "exit_code": 0,
            "stdout_bytes": len(stdout_text),
        })

        # 跟原 run_agent_turn 等价的 parse + extract
        data = _parse_top_object(stdout_text)
        visible = _walk_find(data, "finalAssistantVisibleText") or _walk_find(data, "finalAssistantRawText") or ""
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


def _try_parse_partial(stdout_text: str) -> TurnResult | None:
    """timeout 时尝试从已累积 stdout 抠出可用结果.

    OpenClaw 通常全 buffer,partial 一般是空;但 stream 设计就绪,未来真正
    streaming 的 backend(DirectLLM)能用上。
    """
    if not stdout_text.strip():
        return None
    try:
        data = _parse_top_object(stdout_text)
        visible = _walk_find(data, "finalAssistantVisibleText") or _walk_find(data, "finalAssistantRawText") or ""
        return TurnResult(
            text=visible, provider=None, model=None,
            prompt_tokens=0, completion_tokens=0, cache_read_tokens=0, total_tokens=0,
            raw={"_partial": True, **data},
        )
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────
# 单例
# ─────────────────────────────────────────────────────────────

_default: AgentBackend | None = None


def get_default_backend() -> AgentBackend:
    global _default
    if _default is None:
        _default = OpenClawSubprocessBackend()
    return _default


def set_default_backend(backend: AgentBackend) -> None:
    """测试时可注入 mock backend."""
    global _default
    _default = backend
