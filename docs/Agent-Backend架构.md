# Agent Backend 架构

> 配套：`docs/开发文档.md` §pipeline 编排 / `docs/OpenClaw集群接入说明.md`
> 目标读者：要给 pipeline 换执行后端（Gateway / 直 SDK）、写单测 mock、或为 agent 加 streaming UI 的工程师
> 关键文件：`apps/web-backend/app/orchestrator/agent_backend.py`、`apps/web-backend/app/openclaw/client.py`

---

## 一、背景：为什么要抽

V0 阶段所有 agent turn 都直接 fork `openclaw agent --local --json` subprocess。这个直耦合在长期会出几个问题：

| 痛点 | 后果 |
| --- | --- |
| **Fork 启动开销** | 每个 step fork 一次新进程，profile init 重，3-5 分钟汇报 8 步 = 8 次冷启 |
| **黑盒中间过程** | 之前用 `proc.communicate()` 一次性拿全部输出，timeout 时所有已积累内容全丢 |
| **无进度信号** | agent 思考过程对外完全不可见，前端 SSE 只能等 step done 才更新 |
| **跟 OpenClaw 死耦合** | 想换 anthropic-sdk / deepseek-sdk 直调，要改 pipeline.py 几百处 |

抽象目的：让 pipeline 不直接依赖 OpenClaw CLI，未来能在不改 pipeline 的前提下换执行机制。

---

## 二、新架构

```
pipeline.py / harness.py
     │
     │ await run_agent_turn(agent_id, message, model, timeout, on_progress)
     ▼
openclaw/client.py:run_agent_turn         · thin wrapper（22 行）
     │
     │ get_default_backend().run_turn(...)
     ▼
orchestrator/agent_backend.py:AgentBackend (ABC)
     │
     ├─ OpenClawSubprocessBackend  ← 当前唯一实现
     ├─ (future) OpenClawGatewayBackend  · ws://127.0.0.1:18789 复用 warm-up
     ├─ (future) DirectLLMBackend         · 直调 anthropic / deepseek SDK
     └─ MockBackend(单测)
```

### `client.py:run_agent_turn` 现在是什么

```python
# apps/web-backend/app/openclaw/client.py:72-98
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

    现在是 thin wrapper · 真正实现见 agent_backend.py:OpenClawSubprocessBackend,
    通过 get_default_backend() 单例取(测试可 set_default_backend(mock) 注入)。
    """
    from ..orchestrator.agent_backend import get_default_backend
    backend = get_default_backend()
    return await backend.run_turn(
        agent_id=agent_id, message=message, model=model,
        timeout_sec=timeout_sec, extra_env=extra_env,
        on_progress=on_progress,
    )
```

> ⚠️ 保留这个函数是为了让 pipeline.py / harness.py 调用点不破。新代码可以直接 `get_default_backend().run_turn(...)` 跳过 wrapper。

---

## 三、ABC 接口

```python
# apps/web-backend/app/orchestrator/agent_backend.py

@dataclass
class BackendCapabilities:
    name: str                              # 标识符 (e.g. "openclaw-subprocess")
    streams_progress: bool                 # 真有 token/step-level 进度？
    supports_warmup: bool                  # warmup() 真有效（预热模型）？
    supports_pool: bool                    # 复用实例避免每 turn fork？
    supports_partial_on_timeout: bool      # timeout 时能给回 partial？

@dataclass
class AgentProgress:
    kind: ProgressKind   # "start" | "stdout_line" | "stderr_line" | "wait" | "done" | "timeout" | "error"
    agent_id: str
    text: str = ""
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

class AgentBackend(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities: ...

    @abstractmethod
    async def run_turn(
        self, *,
        agent_id: str,
        message: str,
        model: str | None = None,
        timeout_sec: int = 1800,
        extra_env: dict[str, str] | None = None,
        on_progress: Optional[ProgressHandler] = None,
    ) -> TurnResult: ...

    async def warmup(self, agent_ids: list[str]) -> None:
        return None       # 默认 no-op

    async def shutdown(self) -> None:
        return None       # 默认 no-op
```

### 设计约束（写新 backend 必看）

1. **run_turn 一定要抛异常**：timeout / non-zero exit / parse fail 一律抛，不能默默返回空 TurnResult。pipeline 靠异常落 step `failed` 状态。
2. **on_progress 回调出错不能炸 run_turn**：handler 自己 try-catch，log warn 但继续跑（OpenClawSubprocessBackend `_emit` 已示范）。
3. **partial 优先于 timeout 异常**：若 backend 声明 `supports_partial_on_timeout=True`，timeout 时先 try partial parse；拿到就返回 partial TurnResult（raw 里带 `_partial: True` 标记），拿不到才抛 TimeoutError。
4. **不要 fire-and-forget on_progress**：必须 await，handler 是 async 的，错过 await 会导致事件乱序。

---

## 四、当前实现：OpenClawSubprocessBackend

```python
class OpenClawSubprocessBackend(AgentBackend):
    """fork `openclaw agent` CLI · 一次一个 turn.

    比老 run_agent_turn 的改进:
      - 读 stdout/stderr line-by-line(async for line in stream)而非 communicate()
      - 累积 buffer 到 list,timeout / non-zero 时仍能拿 partial 尝试 parse
      - 每行 emit on_progress(kind=stdout_line/stderr_line),给前端 SSE 用
    """

    def __init__(self, *, openclaw_bin=None, default_model=None, thinking=None):
        self._bin           = openclaw_bin or os.environ.get("OPENCLAW_BIN") or "openclaw"
        self._default_model = default_model or "deepseek/deepseek-v4-flash"
        self._thinking      = thinking or "high"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name="openclaw-subprocess",
            streams_progress=False,            # openclaw 自己 buffer 输出
            supports_warmup=False,
            supports_pool=False,
            supports_partial_on_timeout=True,  # buffer 留住,但通常为空
        )
```

### read-loop（替代 communicate()）

```python
async def _drain(stream, kind: str, buf: list[bytes]) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        buf.append(line)
        try:
            s = line.decode("utf-8", errors="replace").rstrip()
            if s:
                await _emit(kind, text=s)
        except Exception:
            pass

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
    ...
```

**好处**：

- timeout 时 `stdout_buf` 里已积累的行不丢，能丢给 `_try_parse_partial` 救回部分输出。
- 每行 emit `stdout_line` / `stderr_line` 事件给 `on_progress`，前端 SSE 可以实时展示 agent 在干啥。

**OpenClaw 局限**：streams_progress=False 是诚实的 — OpenClaw CLI 自己 buffer 输出，实际能 emit 的事件稀疏（几乎只有最终大 JSON 一次性出来）。架构就绪，但需要 backend 端配合（比如未来 DirectLLMBackend 用 Anthropic SSE 真 stream token）。

### Partial parse

```python
def _try_parse_partial(stdout_text: str) -> TurnResult | None:
    """timeout 时尝试从已累积 stdout 抠出可用结果."""
    if not stdout_text.strip():
        return None
    try:
        data = _parse_top_object(stdout_text)
        visible = _walk_find(data, "finalAssistantVisibleText") \
               or _walk_find(data, "finalAssistantRawText") or ""
        return TurnResult(
            text=visible, provider=None, model=None,
            prompt_tokens=0, completion_tokens=0, cache_read_tokens=0, total_tokens=0,
            raw={"_partial": True, **data},
        )
    except Exception:
        return None
```

caller 可以检查 `result.raw.get("_partial") == True` 判断是 partial 数据。pipeline 当前没用这个分支，但接口就绪。

---

## 五、单例

```python
_default: AgentBackend | None = None

def get_default_backend() -> AgentBackend:
    global _default
    if _default is None:
        _default = OpenClawSubprocessBackend()
    return _default

def set_default_backend(backend: AgentBackend) -> None:
    """测试时注入 mock backend."""
    global _default
    _default = backend
```

单测里把全局换掉再调 `run_agent_turn`：

```python
from app.orchestrator.agent_backend import set_default_backend, AgentBackend, ...

class MockBackend(AgentBackend):
    @property
    def capabilities(self):
        return BackendCapabilities(name="mock", streams_progress=False,
                                   supports_warmup=False, supports_pool=False,
                                   supports_partial_on_timeout=False)

    async def run_turn(self, *, agent_id, message, **kw) -> TurnResult:
        return TurnResult(
            text='```json\n{"slides": [...]}\n```',
            provider="mock", model="mock-v1",
            prompt_tokens=10, completion_tokens=20,
            cache_read_tokens=0, total_tokens=30,
            raw={},
        )

set_default_backend(MockBackend())
# 现在 pipeline 调 run_agent_turn 全走 mock
```

---

## 六、未来 backend 蓝图

### OpenClawGatewayBackend（次优先级）

走 ws://127.0.0.1:18789（用户机器上 OpenClaw Gateway 已起，参考 [[openclaw-local-gateway]]）：

- 复用单进程 / 单 model warm-up，避免每 turn fork。
- 真 streaming：Gateway 是 WebSocket，能逐 token 推 `stdout_line` / `assistant_token` 事件。
- capabilities 翻成 `supports_pool=True / streams_progress=True / supports_warmup=True`。
- 接入工作量：写一个 ws 客户端，按 Gateway 协议帧映射到 AgentProgress；pipeline 不改。

### DirectLLMBackend（远期）

完全跳过 OpenClaw，直调 anthropic-sdk / deepseek-sdk：

- 优势：retry / streaming / token usage 全在 backend 内部控制；可以做真 partial recovery（中途断把已生成 token 拿回）。
- 代价：OpenClaw 的 SOUL.md / AGENTS.md / 跨 agent 协议 / 工具调用全得自己重写一遍。
- 适合的场景：CI / 离线评测，不需要完整 agent 工具链时。

### MockBackend（单测）

见上节，已经能跑。

---

## 七、迁移注意

写新代码可以直接：

```python
from app.orchestrator.agent_backend import get_default_backend
result = await get_default_backend().run_turn(agent_id=..., message=...)
```

不必走 `from app.openclaw.client import run_agent_turn` thin wrapper（保留它纯粹是为兼容历史调用点）。

> ⚠️ `TurnResult` 仍在 `app/openclaw/client.py` 里 — 是历史包袱，新 backend `import` 它会有循环依赖风险（`client.py` import `agent_backend`、`agent_backend.py` import `TurnResult`）。`client.py:92` 用 `from ..orchestrator.agent_backend import get_default_backend` **延迟 import** 绕开。后续可考虑把 `TurnResult` 挪到 `agent_backend.py`，client.py 只负责 JSON parse 辅助函数。

---

## 八、Smoke test

```bash
# 跑一次 backend 单测（不依赖 pipeline）
cd apps/web-backend
uv run python -c "
import asyncio
from app.orchestrator.agent_backend import get_default_backend

async def main():
    backend = get_default_backend()
    print('caps:', backend.capabilities)
    events = []
    async def on_progress(ev):
        events.append((ev.kind, ev.text[:80]))
    result = await backend.run_turn(
        agent_id='coordinator', message='你好,介绍下你自己',
        timeout_sec=120, on_progress=on_progress,
    )
    print('text head:', result.text[:200])
    print('events:', events[:5])

asyncio.run(main())
"
```

预期：
- `caps:` 输出 `BackendCapabilities(name='openclaw-subprocess', streams_progress=False, ...)`
- agent 真实返回 `你好,我是 coordinator...`
- events 至少有 `start` + `done` 两条；中间可能没有 stdout_line（OpenClaw buffer 行为决定的）

---

## 九、相关 memory

- [[openclaw-integration]]
- [[openclaw-client-parser-bug]]
- [[openclaw-local-gateway]]
- [[sse-long-connection]]（on_progress 事件流最终消费方）
- [[agent-bubble-ux]]（前端如何展示 progress 事件）
