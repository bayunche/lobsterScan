# Phase 1 — Data Model

**Feature**: Worker 订阅化 + decide-to-speak 闸门（P2）

**Module**: `apps/web-backend/app/orchestrator/subscription.py`（新增） + `harness.py` 字段扩展

---

## 总览

```text
subscription.py
├── Predicate           # type alias
├── WorkerProfile       # frozen dataclass：每个 agent 的 interests + requires 声明
├── WORKER_PROFILE      # 9 agent 的静态注册表（const）
├── DecisionResult      # enum：SPEAK / SILENT / IGNORE
├── MentionCounter      # 任务级：agent_id → @ 计数
├── ReplyToRegistry     # 任务级：(agent_id, reply_to) → seen 集合
└── SubscriptionRegistry # 任务级容器：worker registry + dispatch

harness.py 字段扩展
├── HarnessState.subscriptions: SubscriptionRegistry | None
├── HarnessState.agent_locks: dict[str, asyncio.Lock]
└── HarnessState.get_agent_lock(agent_id) -> asyncio.Lock
```

所有新类型：stdlib + Pydantic v2 重复使用（与 P1 风格一致）。

---

## 1. Predicate（类型别名）

```python
from typing import Callable
from .events_v2 import V2EventBase

Predicate = Callable[[V2EventBase, str], bool]
# 第一参：事件本身；第二参：worker 自己的 agent_id
# 返回 True 表示"这个 worker 对该事件感兴趣"
```

3 个预制 helper 函数：

| 函数 | 签名 | 用途 |
|---|---|---|
| `mention_includes(self_id)` | `(str) -> Predicate` | 闭包：返回判断 `AgentSpeak.mentions` 是否含 self_id 的谓词 |
| `hint_agent_is(self_id)` | `(str) -> Predicate` | 闭包：返回判断 `CoordinatorIntervene.hint_agent == self_id` |
| `artifact_id_in(ids)` | `(set[str]) -> Predicate` | 闭包：返回判断 `ArtifactUpdate.id ∈ ids` |

谓词内部用 `isinstance` 检查 event 类型；非匹配 event 一律返回 False（不抛错）。

---

## 2. WorkerProfile（frozen dataclass）

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class WorkerProfile:
    interests: tuple[Predicate, ...]      # 元组化保证不可变；any-of 语义（任一谓词 True 即感兴趣）
    requires: tuple[str, ...]              # 4 核心 artifact 子集；空表示"无依赖,总能 speak"
```

**任务内不可变**；启动 run_harness 时根据 WORKER_PROFILE 注册。

---

## 3. WORKER_PROFILE 静态表（subscription.py 顶部）

```python
WORKER_PROFILE: dict[str, WorkerProfile] = {
    "material":         WorkerProfile(
        interests=(mention_includes("material"), hint_agent_is("material")),
        requires=(),
    ),
    "point-extractor":  WorkerProfile(
        interests=(mention_includes("point-extractor"),
                   hint_agent_is("point-extractor"),
                   artifact_id_in({"MaterialPool"})),
        requires=("MaterialPool",),
    ),
    "structure":        WorkerProfile(
        interests=(mention_includes("structure"),
                   artifact_id_in({"ReportCore"})),
        requires=("ReportCore",),
    ),
    "upward-opt":       WorkerProfile(
        interests=(mention_includes("upward-opt"),
                   artifact_id_in({"ReportCore"})),
        requires=("ReportCore",),
    ),
    "copywriter":       WorkerProfile(
        interests=(mention_includes("copywriter"),
                   artifact_id_in({"ReportCore", "Outline"})),
        requires=("ReportCore", "Outline"),
    ),
    "html-designer":    WorkerProfile(
        interests=(mention_includes("html-designer"),
                   artifact_id_in({"Script"})),
        requires=("Script",),
    ),
    "video-producer":   WorkerProfile(
        interests=(mention_includes("video-producer"),
                   artifact_id_in({"Script"})),
        requires=("Script",),
    ),
    "reviewer":         WorkerProfile(
        interests=(mention_includes("reviewer"),
                   artifact_id_in({"MaterialPool", "ReportCore", "Outline", "Script"})),
        requires=(),  # reviewer 不强制依赖：任何 artifact 出来都能发声
    ),
    "coordinator":      WorkerProfile(
        interests=(),
        requires=(),
    ),  # P2 不订阅 — P3 才赋予 observer 行为
}
```

---

## 4. DecisionResult（enum）

```python
from enum import Enum

class DecisionResult(Enum):
    SPEAK  = "speak"    # emit AgentSpeak，更新 ReplyToRegistry 与 MentionCounter
    SILENT = "silent"   # emit AgentSilent，更新 ReplyToRegistry 与 MentionCounter
    IGNORE = "ignore"   # 仅 log；不 emit，不更新计数器
```

---

## 5. MentionCounter（任务级）

```python
@dataclass
class MentionCounter:
    """任务内 agent_id → 被 @ 次数；用于 FR-006 防 flap（默认 N=2）。"""
    _counts: dict[str, int] = field(default_factory=dict)

    def bump(self, agent_id: str) -> int:
        self._counts[agent_id] = self._counts.get(agent_id, 0) + 1
        return self._counts[agent_id]

    def get(self, agent_id: str) -> int:
        return self._counts.get(agent_id, 0)
```

---

## 6. ReplyToRegistry（任务级）

```python
@dataclass
class ReplyToRegistry:
    """任务内 (agent_id, reply_to_message_id) → 已响应过；用于 FR-006 去重。"""
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def has_responded(self, agent_id: str, reply_to: str | None) -> bool:
        if reply_to is None:
            return False
        return (agent_id, reply_to) in self._seen

    def mark(self, agent_id: str, reply_to: str | None) -> None:
        if reply_to is not None:
            self._seen.add((agent_id, reply_to))
```

---

## 7. SubscriptionRegistry（任务级，HarnessState 持有）

```python
@dataclass
class SubscriptionRegistry:
    """任务级订阅注册表 + dispatch。is_v2=True 时由 run_harness 构造一次。"""
    workers: dict[str, "AgentWorker"] = field(default_factory=dict)
    profiles: dict[str, WorkerProfile] = field(default_factory=dict)
    mention_counter: MentionCounter = field(default_factory=MentionCounter)
    reply_to_registry: ReplyToRegistry = field(default_factory=ReplyToRegistry)

    def register(self, agent_id: str, worker: "AgentWorker", profile: WorkerProfile) -> None:
        if not profile.interests:  # 空 interests 跳过（如 coordinator）
            return
        self.workers[agent_id] = worker
        self.profiles[agent_id] = profile

    def dispatch(self, event: V2EventBase) -> int:
        """同步分发；返回入队的 worker 数。put_nowait 失败 → 丢最老（worker.enqueue_v2 内处理）。"""
        delivered = 0
        for agent_id, profile in self.profiles.items():
            try:
                if any(p(event, agent_id) for p in profile.interests):
                    worker = self.workers.get(agent_id)
                    if worker and worker.enqueue_v2(event):
                        delivered += 1
            except Exception as e:  # noqa: BLE001 — FR-016
                log.warning("subscription predicate eval crash for %s: %s", agent_id, e)
        return delivered
```

---

## 8. decide_to_speak（纯函数）

```python
def decide_to_speak(
    *,
    event: V2EventBase,
    agent_id: str,
    profile: WorkerProfile,
    mention_counter: MentionCounter,
    reply_to_registry: ReplyToRegistry,
    available_artifacts: dict[str, int],  # artifact_id → latest version；P1 read_versioned 可得
    mention_limit: int = V2_MENTION_LIMIT,
) -> tuple[DecisionResult, str]:
    """纯函数；返回 (decision, reason_or_speak_text_seed)。

    规则（按优先级）：
    1) 若 worker 已对同一 reply_to 响应过 → IGNORE
    2) 若 mention_counter[agent_id] >= mention_limit → IGNORE（log warn）
    3) 若 profile.requires 全部满足（每个 artifact_id 都 in available_artifacts）→ SPEAK
    4) 否则（有 require 缺失）→ SILENT（reason 含缺失 artifact 列表）
    """
    reply_to = getattr(event, "reply_to", None)
    if reply_to_registry.has_responded(agent_id, reply_to):
        return DecisionResult.IGNORE, "已响应同一 reply_to"

    if mention_counter.get(agent_id) >= mention_limit:
        return DecisionResult.IGNORE, f"被 @ 次数已达 {mention_limit}"

    missing = [a for a in profile.requires if a not in available_artifacts]
    if missing:
        return DecisionResult.SILENT, f"等 {' / '.join(missing)} 先就绪"

    return DecisionResult.SPEAK, "依赖齐全，准备发言"
```

**纯函数特性**：所有依赖都从参数传入，便于单测直接断言（不需要 mock state / async / IO）。

---

## 9. HarnessState 扩展字段

```python
# harness.py
from .subscription import SubscriptionRegistry
import asyncio

@dataclass
class HarnessState:
    # 现有 P1 字段...
    is_v2: bool = False
    message_id_registry: MessageIdRegistry = field(default_factory=MessageIdRegistry)

    # P2 新增（仅 is_v2=True 时构造 / 使用；v1 路径全部为 None / 空）
    subscriptions: "SubscriptionRegistry | None" = None
    agent_locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def get_agent_lock(self, agent_id: str) -> asyncio.Lock:
        """lazy-init per-agent lock；must be called inside running event loop."""
        if agent_id not in self.agent_locks:
            self.agent_locks[agent_id] = asyncio.Lock()
        return self.agent_locks[agent_id]
```

---

## 10. AgentWorker 扩展字段与方法

```python
# harness.py
class AgentWorker:
    def __init__(self, *, agent_id, step_key, state, run_step_fn, gate_review_fn) -> None:
        # 现有 P1 字段...
        self.agent_id = agent_id
        # ...

        # P2 新增（仅 is_v2 时 start_v2_consumer 后才有值）
        self.inbox: asyncio.Queue[V2EventBase] | None = None
        self._consume_task: asyncio.Task | None = None

    def start_v2_consumer(self) -> None:
        """run_harness 在 is_v2 时为每个有 interests 的 worker 调一次。"""
        if self.inbox is not None:
            return
        self.inbox = asyncio.Queue(maxsize=V2_INBOX_MAX)
        self._consume_task = asyncio.create_task(self._consume_loop())

    def enqueue_v2(self, event: V2EventBase) -> bool:
        """SubscriptionRegistry.dispatch 调；满了丢最老。"""
        if self.inbox is None:
            return False
        try:
            self.inbox.put_nowait(event)
            return True
        except asyncio.QueueFull:
            try:
                self.inbox.get_nowait()
                self.inbox.put_nowait(event)
                log.warning("worker %s inbox 满,丢弃最老事件", self.agent_id)
                return True
            except Exception:
                return False

    async def _consume_loop(self) -> None:
        """死循环消费 inbox；任务结束时 cancel。"""
        try:
            while True:
                event = await self.inbox.get()
                try:
                    await self.handle_v2_event(event)
                except Exception as e:  # noqa: BLE001 — FR-016
                    log.warning("worker %s · handle_v2_event 失败: %s", self.agent_id, e)
        except asyncio.CancelledError:
            return

    async def handle_v2_event(self, event: V2EventBase) -> None:
        """订阅触发的"chat overlay"路径：decide-to-speak → emit speak/silent。
        
        **不调 _run_step**（详 research.md §0）；Coordinator 路径仍是 LLM 工作的唯一驱动。
        """
        from .subscription import (
            DecisionResult, decide_to_speak, V2_LOCK_WAIT_SEC,
        )
        from .events_v2 import AgentSpeak, AgentSilent

        if self.state.subscriptions is None or self.state.run is None:
            return

        profile = self.state.subscriptions.profiles.get(self.agent_id)
        if profile is None:
            return

        # 获取当前 task 已有 artifact 版本（用 artifacts_v2 + state.run.task_id 算）
        from .artifacts_v2 import next_version, CORE_ARTIFACTS
        available: dict[str, int] = {}
        for art_id in CORE_ARTIFACTS:
            v = next_version(self.state.run.task_id, art_id) - 1
            if v >= 1:
                available[art_id] = v

        decision, reason = decide_to_speak(
            event=event, agent_id=self.agent_id, profile=profile,
            mention_counter=self.state.subscriptions.mention_counter,
            reply_to_registry=self.state.subscriptions.reply_to_registry,
            available_artifacts=available,
        )

        if decision == DecisionResult.IGNORE:
            log.debug("subscription IGNORE %s on %s: %s",
                      self.agent_id, event.message_id, reason)
            return

        # 串行锁（FR-008/9/12）
        lock = self.state.get_agent_lock(self.agent_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=V2_LOCK_WAIT_SEC)
        except asyncio.TimeoutError:
            # FR-009 降级：锁超时改为 silent
            await self.state.emit_v2(AgentSilent(
                task_id=self.state.run.task_id, **{"from": self.agent_id},
                reply_to=getattr(event, "message_id", None),
                reason="锁等待超时",
            ))
            return

        try:
            self.state.subscriptions.mention_counter.bump(self.agent_id)
            self.state.subscriptions.reply_to_registry.mark(
                self.agent_id, getattr(event, "message_id", None),
            )
            if decision == DecisionResult.SPEAK:
                await self.state.emit_v2(AgentSpeak(
                    task_id=self.state.run.task_id, **{"from": self.agent_id},
                    text=f"收到 — {reason}",
                    reply_to=event.message_id,
                    intent="confirm",
                    mentions=[], cc=[], artifact_updates=[],
                ))
            else:  # SILENT
                await self.state.emit_v2(AgentSilent(
                    task_id=self.state.run.task_id, **{"from": self.agent_id},
                    reply_to=event.message_id, reason=reason[:30],
                ))
        finally:
            lock.release()
```

---

## 11. emit_v2 末尾 dispatch 调用

```python
# harness.py HarnessState.emit_v2()
async def emit_v2(self, event: V2EventBase) -> None:
    if not self.is_v2:
        return
    # ... P1 现有：去重、写 events.jsonl、bus.emit
    # P2 新增：尾端 dispatch
    if self.subscriptions is not None:
        try:
            self.subscriptions.dispatch(event)
        except Exception as e:  # noqa: BLE001 — FR-016
            log.warning("v2 subscription dispatch failed: %s", e)
```

---

## 12. run_harness 集成

```python
# harness.py run_harness() 末尾构造区域附近
async def run_harness(*, ..., is_v2: bool = False) -> dict:
    # ... 现有
    state = HarnessState(..., is_v2=is_v2)

    # P2 新增：仅 v2 路径下构造 SubscriptionRegistry 与启动 consume_loops
    if is_v2:
        from .subscription import SubscriptionRegistry, WORKER_PROFILE
        state.subscriptions = SubscriptionRegistry()
        for agent_id, worker in workers.items():
            profile = WORKER_PROFILE.get(agent_id)
            if profile and profile.interests:
                state.subscriptions.register(agent_id, worker, profile)
                worker.start_v2_consumer()

    # ... 现有 emit task.start + 起点 handoff + await state.done

    # P2 新增：任务结束清理 consume tasks
    if is_v2:
        for worker in workers.values():
            if worker._consume_task is not None and not worker._consume_task.done():
                worker._consume_task.cancel()
                try:
                    await worker._consume_task
                except asyncio.CancelledError:
                    pass

    return {...}
```

---

## 状态转移

### Worker 视角

```
[初始：inbox=None, consume_task=None]
       │
       ├── is_v2=False → 永远停在这里（v1 路径）
       │
       └── is_v2=True + has interests
           │
           v
[start_v2_consumer]
   inbox = Queue(maxsize=32)
   consume_task = asyncio.create_task(_consume_loop)
       │
       ├── 任务结束 → cancel + await（清理）
       │
       └── 收到 enqueue_v2(event)
           │
           v
       [_consume_loop 取出 event]
           │
           v
       [handle_v2_event]
           │
           ├── decide_to_speak == IGNORE → log + return
           ├── decide_to_speak == SILENT → acquire lock → emit AgentSilent → release lock
           └── decide_to_speak == SPEAK  → acquire lock → emit AgentSpeak  → release lock
               │
               ├── lock 等超 V2_LOCK_WAIT_SEC → emit AgentSilent("锁等待超时") → return
               └── lock 获取成功 → emit + release → 继续循环 inbox.get()
```

### Event dispatch 视角

```
[Coordinator / 任意 v2 emit 源] → state.emit_v2(event)
   │
   ├── is_v2=False → return（零开销）
   │
   └── is_v2=True
       │
       v
   [现有：去重 + 写 events.jsonl + bus.emit]
       │
       v
   [subscriptions.dispatch(event)]
       │
       v
   [遍历 profiles]
       │
       v
   [对每个 profile 跑 any(predicate)]
       │
       ├── 不匹配 → 跳过
       └── 匹配 → worker.enqueue_v2(event)
                       │
                       ├── inbox 未满 → put_nowait → True
                       └── inbox 满 → 丢最老 + 放新的 → True（log warn）
```

---

## 验证规则汇总

| 来源 | 规则 |
|---|---|
| FR-001 | WorkerProfile.interests 必须是 Predicate 元组 |
| FR-002 | dispatch 必须按谓词分发到所有匹配 worker（多 worker 可重复触发） |
| FR-003 | is_v2=False 时 SubscriptionRegistry / agent_locks 不构造，inbox 为 None |
| FR-005/06 | decide_to_speak 必须按 4 条规则按顺序判断 |
| FR-007 | IGNORE 不写 events.jsonl；SPEAK/SILENT 必 emit 对应事件 |
| FR-008 | 同 agent_id 同时只能有一个 lock holder |
| FR-009 | wait_for(lock.acquire(), V2_LOCK_WAIT_SEC) 超时降级为 silent |
| FR-010 | dispatch 同步返回（不阻塞 emit_v2 调用方） |
| FR-012 | Coordinator 路径下的 worker.run() 也必须经过同一把 agent_lock（防 v1+v2 并发 race） |
| FR-016 | dispatch / predicate / decide_to_speak / lock 内部异常仅 log warn |
| FR-017 | inbox 满 → 丢最老 + put 新的 |

---

## 已知未决（留给 P3+）

- **真 work-driver subscription**：P3 拆 Coordinator 后，subscription 不再是 chat overlay
- **per-step v2 emit 的具体 text 模板**：本期占位（`f"收到 — {reason}"`），P5 prompt 重写时升级到 transcript-aware
- **`Coordinator` agent 的 observer 行为**：P3 给 coordinator 配 interests（订阅 stagnation / drift 信号）
