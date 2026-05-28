# Phase 0 — Research & Technical Decisions

**Feature**: Worker 订阅化 + decide-to-speak 闸门（P2）

**Branch**: `002-worker-subscription` | **Date**: 2026-05-28

---

## 决策汇总

| # | 决策点 | 选择 |
|---|---|---|
| 0 | P2 subscription 的架构定位 | **chat overlay**（emit speak/silent，不重跑 _run_step） |
| 1 | interests 谓词表达 | **Python `Callable`** + 预制 helper（mention_includes / hint_agent_is / artifact_id_in） |
| 2 | SubscriptionRegistry 位置 | 新模块 **`subscription.py`**，实例挂在 `HarnessState.subscriptions` |
| 3 | per-agent lock 位置 | **`HarnessState.agent_locks: dict[str, asyncio.Lock]`** |
| 4 | worker 事件队列实现 | **`asyncio.Queue(maxsize=32)`** + 长生命周期 `_consume_loop()` 协程 |
| 5 | dispatch 入口点 | **`HarnessState.emit_v2()` 末尾**（is_v2=True 时） |
| 6 | v1 零开销策略 | **is_v2=False 时不构造** SubscriptionRegistry / agent_locks |
| 7 | 配置方式 | **module-level 常量 + env var override**（`V2_MENTION_LIMIT` / `V2_LOCK_WAIT_SEC` / `V2_INBOX_MAX`） |

---

## 0. P2 subscription 的架构定位（最重要的拍板）

**Decision**: 在 P2 阶段，**subscription 是 chat overlay（群聊呈现层），不重新触发 `_run_step`**。
Coordinator 的 v1 链路仍是 LLM 工作的唯一驱动；subscription 只 emit 业务化 AgentSpeak/AgentSilent
事件。

**Rationale（解决 spec 内的约束冲突）**:

spec 同时要求：
- FR-011 + FR-018：**Coordinator P2 不改**（现有规则路由 / 必经步骤保护 / 默认链都保持）
- FR-002 + FR-020：**subscription 驱动 worker**（被 @ 自动唤醒 + 跑完产物落盘）

如果让 subscription 真的触发 `_run_step`：
- 同步：Coordinator 派单 step → run_turn → output_json → handoff → Coordinator 路由下一棒
- 异步：subscription 收到 emit_v2 → 也分发 → 也调 run_turn → 同步两路 race
- 结果：同一 step 在同一任务里被跑 2 次（Coordinator 一次 + subscription 一次）
- 后果：浪费 LLM 调用 / `state.visited` 被双重 bump / output_json 被覆盖 / 产物落盘冲突

P2 的最小可行解（这版 plan 的核心拍板）：
1. subscription 不重跑 `_run_step` — 只做"群聊呈现"：emit `agent.speak` 或 `agent.silent`，
   text 由 worker 根据上下文（如 "已就绪，可以接力"、"等 ReportCore 先出"）合成
2. Coordinator 仍按 v1 完整跑链路；产物落盘 / handoff / 默认链全保留
3. v2 路径下，**额外多一份 chat overlay**：每个 step 完成后多一条 AgentSpeak（含 mentions）
   + 一条 artifact.update；下游 worker 通过订阅看到，但**只发 silent 表态**（依赖未就绪）
   **或 speak 表态**（确认看到），不主动跑活
4. 真正"subscription 驱动 work"由 **P3 拆 Coordinator 时**落地（届时 Coordinator 失去
   chain 派单职责，subscription 成为唯一 driver）

**这是对 spec FR-020 的范围微调**："等价于'被 Coordinator 派单'"在 P2 解读为
"等价于'在群聊里被点了名'"，"产物落盘 + emit 与 v1 完全一致" 解读为 "Coordinator 路径
的产物落盘 + v2 多一份 chat overlay event"。这是 minimum-conflict 实现。

**Alternatives considered**:
- **真双驱动 + dedup 逻辑**：subscription 触发前查 `_currently_running_steps` 锁 → 同 step
  Coordinator 在跑就 ignore。可行但加复杂度；P2 单 task 短，意义不大。否。
- **v2 模式下停掉 Coordinator**：彻底切到 subscription-only 驱动 = P3 的活。否（FR-018）。

**实现要点**：
- `AgentWorker.handle_v2_event(event)` 是订阅路径专用入口：调 decide-to-speak gate →
  emit speak/silent；**不调 `_run_step`**
- Coordinator 路径的 `AgentWorker.run()` 不动
- Per-agent lock 仍守住：即便 subscription 不跑 LLM，emit/serialize 也要 lock 防 race

---

## 1. interests 谓词表达

**Decision**: Python `Callable[[V2EventBase, str], bool]` 形式，预制 helper 工厂函数。

**Rationale**:
- 类型安全、零依赖、IDE 跳转 / debug 友好
- 9 个 agent 的谓词总共不超过 20 条；不需要 DSL 表达能力
- 单测里直接 `predicate(event, agent_id) is True/False`，无 mock 复杂度

**Alternatives considered**:
- **JSON DSL**（`{"msg_type":"agent.speak","mentions_contains":"<self>"}`）：需要 parser + evaluator + 反序列化，零收益。否。
- **dict 谓词表**（`{"agent.speak": lambda e, self: self in e.mentions, ...}`）：每条 worker 的多种谓词不易表达；按 msg_type 索引虽快但限制表达力。否。

**实现要点**：

```python
# subscription.py 预制 helpers
from .events_v2 import V2EventBase, AgentSpeak, CoordinatorIntervene, ArtifactUpdate

Predicate = Callable[[V2EventBase, str], bool]

def mention_includes(self_id: str) -> Predicate:
    def _p(event: V2EventBase, _: str) -> bool:
        return isinstance(event, AgentSpeak) and self_id in (event.mentions or [])
    return _p

def hint_agent_is(self_id: str) -> Predicate:
    def _p(event: V2EventBase, _: str) -> bool:
        return isinstance(event, CoordinatorIntervene) and event.hint_agent == self_id
    return _p

def artifact_id_in(artifact_ids: set[str]) -> Predicate:
    def _p(event: V2EventBase, _: str) -> bool:
        return isinstance(event, ArtifactUpdate) and event.id in artifact_ids
    return _p
```

**`WORKER_PROFILE` 静态表**（subscription.py 顶部）：

```python
@dataclass(frozen=True)
class WorkerProfile:
    interests: list[Predicate]
    requires: list[str]  # 4 核心 artifact 子集

WORKER_PROFILE: dict[str, WorkerProfile] = {
    "material":         WorkerProfile([mention_includes("material"), hint_agent_is("material")], []),
    "point-extractor":  WorkerProfile([mention_includes("point-extractor"), hint_agent_is("point-extractor"),
                                       artifact_id_in({"MaterialPool"})], ["MaterialPool"]),
    "structure":        WorkerProfile([mention_includes("structure"), artifact_id_in({"ReportCore"})],
                                       ["ReportCore"]),
    "upward-opt":       WorkerProfile([mention_includes("upward-opt"), artifact_id_in({"ReportCore"})],
                                       ["ReportCore"]),
    "copywriter":       WorkerProfile([mention_includes("copywriter"),
                                       artifact_id_in({"ReportCore", "Outline"})],
                                       ["ReportCore", "Outline"]),
    "html-designer":    WorkerProfile([mention_includes("html-designer"), artifact_id_in({"Script"})],
                                       ["Script"]),
    "video-producer":   WorkerProfile([mention_includes("video-producer"), artifact_id_in({"Script"})],
                                       ["Script"]),
    "reviewer":         WorkerProfile([mention_includes("reviewer"),
                                       artifact_id_in({"MaterialPool", "ReportCore", "Outline", "Script"})],
                                       []),  # reviewer 不强制依赖，可总能发声
    "coordinator":      WorkerProfile([], []),  # P2 不订阅 — P3 才赋予 observer 行为
}
```

---

## 2. SubscriptionRegistry 位置

**Decision**: 新模块 `apps/web-backend/app/orchestrator/subscription.py`，实例挂在 `HarnessState.subscriptions: SubscriptionRegistry | None`。

**Rationale**:
- `harness.py` 已 ~500 行，subscription 加进去会让单文件超过 800 行难审阅
- 单独 module 便于 P3 阶段如要重构 / 扩展（如换 dict-based predicate 或换 routing 策略）
- 与 P1 的 `events_v2.py` / `artifacts_v2.py` 风格一致（每个 v2 子系统独立模块）

**Alternatives considered**:
- 内嵌 `harness.py`：聚拢但 file 过长。否。
- 内嵌 `events_v2.py`：events 模块只负责 schema，加 dispatch 逻辑职责混乱。否。

**module 内部结构**：

```text
subscription.py
├── Predicate type alias + 3 个 helper (mention_includes / hint_agent_is / artifact_id_in)
├── WorkerProfile dataclass + WORKER_PROFILE 静态表
├── DecisionResult enum (SPEAK / SILENT / IGNORE)
├── decide_to_speak(...) 函数（纯函数，便于单测）
├── SubscriptionRegistry class（register + dispatch + interest 查找）
├── MentionCounter + ReplyToRegistry（任务级状态，闸门用）
└── module-level 配置常量（V2_MENTION_LIMIT / V2_LOCK_WAIT_SEC / V2_INBOX_MAX）
```

---

## 3. per-agent lock 位置

**Decision**: `HarnessState.agent_locks: dict[str, asyncio.Lock] = field(default_factory=dict)` —— 任务级映射，按需 lazy-init lock。

**Rationale**:
- **任务级隔离**：不同任务的 lock 互不影响（即便后续多任务并发 run_harness）
- 不能用全局 `dict`（会跨任务串扰）
- 不能用 `contextvars`（async-task 边界跨越时不稳）
- `defaultdict(asyncio.Lock)` 不行 —— `asyncio.Lock()` 必须在事件循环内构造，`defaultdict` 触发的默认构造可能在不对的 loop 上

**Alternatives considered**:
- 全局 `_locks: dict[str, asyncio.Lock]`：跨任务串扰，否
- `contextvars.ContextVar`：async cancel / shield 时行为不可控，否
- `weakref.WeakValueDictionary`：lock 没人持有就被回收，下次又新建，失去锁义。否

**实现要点**：

```python
# HarnessState 新方法
def get_agent_lock(self, agent_id: str) -> asyncio.Lock:
    if agent_id not in self.agent_locks:
        self.agent_locks[agent_id] = asyncio.Lock()
    return self.agent_locks[agent_id]

# 调用：
lock = state.get_agent_lock(agent_id)
try:
    await asyncio.wait_for(lock.acquire(), timeout=V2_LOCK_WAIT_SEC)
except asyncio.TimeoutError:
    # FR-009：超时降级为 silent
    await state.emit_v2(AgentSilent(..., reason="锁等待超时"))
    return
try:
    # ... 跑活
finally:
    lock.release()
```

---

## 4. worker 事件队列实现

**Decision**: `asyncio.Queue(maxsize=V2_INBOX_MAX)` per worker，搭配长生命周期 `_consume_loop()` 协程。

**Rationale**:
- `asyncio.Queue` 是 stdlib、稳定、async 友好、有 maxsize 防 OOM
- 长 loop 协程比"每事件一个 task"更简单可控（事件来 fast burst 时不会瞬间起 N 个 task）
- worker 自身串行处理 inbox，与 per-agent lock 形成双层保护
- maxsize 满了 → `put_nowait` 失败 → 丢最老（FR-017），不阻塞 emit

**Alternatives considered**:
- **直接订阅 + 每事件 create_task**：fast burst 时起 task 数无上限，可能 OOM；难以控制顺序。否
- **bus.on(handler) 注册回调**：handler 调用是同步串行的，但 handler 内 await 会阻塞 bus dispatch；不适合 LLM 等长任务。否
- **`janus.Queue`**（同步 / 异步双端）：需要新依赖，且 P2 没有跨线程需求。否

**实现要点**：

```python
class AgentWorker:
    def __init__(self, *, agent_id, ..., state: HarnessState) -> None:
        # ... 已有字段
        # P2 新增（仅 is_v2 时启用）
        self.inbox: asyncio.Queue[V2EventBase] | None = None
        self._consume_task: asyncio.Task | None = None

    def start_v2_consumer(self) -> None:
        """run_harness 在 is_v2 时调用一次。"""
        if self.inbox is not None:
            return
        self.inbox = asyncio.Queue(maxsize=V2_INBOX_MAX)
        self._consume_task = asyncio.create_task(self._consume_loop())

    async def _consume_loop(self) -> None:
        """死循环消费 inbox；任务结束时 cancel。"""
        while True:
            event = await self.inbox.get()
            try:
                await self.handle_v2_event(event)
            except Exception as e:  # noqa: BLE001 — FR-016 降级
                log.warning("worker %s · handle_v2_event 失败: %s", self.agent_id, e)

    def enqueue_v2(self, event: V2EventBase) -> bool:
        """SubscriptionRegistry.dispatch 调；返回 False 表示丢弃。"""
        if self.inbox is None:
            return False
        try:
            self.inbox.put_nowait(event)
            return True
        except asyncio.QueueFull:
            # FR-017：满了丢最老，再 put 新的
            try:
                self.inbox.get_nowait()
                self.inbox.put_nowait(event)
                log.warning("worker %s inbox 满,丢弃最老事件", self.agent_id)
                return True
            except Exception:
                return False
```

---

## 5. dispatch 入口点

**Decision**: 在 `HarnessState.emit_v2()` 末尾（写完 events.jsonl + bus.emit 之后）调用 `self.subscriptions.dispatch(event)`，仅当 `self.is_v2=True` 且 `self.subscriptions is not None` 时。

**Rationale**:
- emit_v2 已经是 v2 事件流唯一出口，dispatch 放在这里语义清晰
- v1 路径下 emit_v2 第一行 return；dispatch 自然短路
- 不需要额外 dispatcher 模块或回调注册

**Alternatives considered**:
- **bus 上挂一个 wildcard subscriber 做 dispatch**：bus 是 v1 协议（kind 路由），mix v2 dispatch 模糊职责。否
- **包一层 `V2Dispatcher` 类**：emit_v2 → dispatcher.emit → bus + subscriptions。多一层间接，无收益。否

**实现要点**（修改 `HarnessState.emit_v2`）：

```python
async def emit_v2(self, event: V2EventBase) -> None:
    if not self.is_v2:
        return
    # ... 现有：去重、写 events.jsonl、bus.emit
    # P2 新增：尾端 dispatch
    if self.subscriptions is not None:
        try:
            self.subscriptions.dispatch(event)
        except Exception as e:  # noqa: BLE001 — FR-016 降级
            log.warning("v2 subscription dispatch failed: %s", e)
```

---

## 6. v1 零开销策略

**Decision**: `is_v2=False` 时**根本不构造** `SubscriptionRegistry` / `agent_locks` / worker inbox。`run_harness` 入口分流：

```python
async def run_harness(*, ..., is_v2: bool = False) -> dict:
    # ... 现有
    state = HarnessState(..., is_v2=is_v2)
    if is_v2:
        state.subscriptions = SubscriptionRegistry()
        # 为每个 worker 注册 interests + 启动 consume loop
        for agent_id, worker in workers.items():
            profile = WORKER_PROFILE.get(agent_id)
            if profile is None or not profile.interests:
                continue
            state.subscriptions.register(agent_id, worker, profile)
            worker.start_v2_consumer()
    # else: 完全不动 state.subscriptions（保持 None）、不构造 inbox
```

**Rationale**:
- 100% 零开销：v1 路径下零个新对象、零次新方法调用、零分支
- 既有 v1 / P1 已有的回归测试自动覆盖这层（test_v1_regression.py 不需要改）

**Alternatives considered**:
- 始终构造，靠 `is_v2` 短路：构造开销极小（几个空 dict），但破坏"零变化"语义；测试需要修改才能验证 v1 不受影响。否

**测试守护**：
- T-002 测试：v1 run_harness 跑完，state.subscriptions is None；agent_locks == {}；任何 worker 的 inbox 都是 None
- T-003 测试：grep `events.jsonl` v1 行不出现任何 msg_type 字段（沿用 P1 test_v1_regression）

---

## 7. mention 阈值 + lock 超时配置

**Decision**: `subscription.py` 顶部 module-level 常量 + env var override：

```python
import os

V2_MENTION_LIMIT = int(os.environ.get("V2_MENTION_LIMIT", "2"))
V2_LOCK_WAIT_SEC = float(os.environ.get("V2_LOCK_WAIT_SEC", "60"))
V2_INBOX_MAX     = int(os.environ.get("V2_INBOX_MAX", "32"))
```

**Rationale**:
- 业务默认值是 spec 已给的（2 / 60 / 32），覆盖 80% 场景
- env var 给运维 / debug 一个临时调整入口（如 demo 时把 lock 超时调到 10s 加速触发降级）
- 不进 `settings.py` 避免 P2 给 settings module 引入大量 v2-specific 字段

**Alternatives considered**:
- 写入 `app/config/settings.py`：耦合更紧，但 settings 已有较多字段，再加 v2 一组维护成本上升。否
- 通过 `TaskRun.v2_config: dict` 任务级配置：太灵活了 P2 用不上。否
- 写入 `.env.example`：可以，但 env var 没出现在 .env.example 也能用，最小变更。后续阶段再补 .env.example。否

**实现要点**：
- 单测里通过 `monkeypatch.setenv("V2_MENTION_LIMIT", "5")` + 重新 import subscription 来测不同值
- 或者通过函数参数 inject（更可测）；但保持简单优先选 const

---

## 派生发现（Derived Findings）

### F1 — Per-step v2 emit 需要在 pipeline 中添加挂钩

P1 只在任务收尾（`_emit_v2_finalization`）emit v2 事件。P2 要让 subscription 在任务**进行中**有事件可订阅，
需要在 v2 路径下**每个 step 完成后**也 emit 一份：

- `agent.speak` —— 由该 step 的 agent 发，text 简述本步成果（可从 output_json 摘取，如 `_summarize_output()`），
  mentions 默认 = [下一棒 agent_id]（按 DEFAULT_NEXT_STEP）
- `artifact.update` —— 若该 step 对应 4 核心 artifact，调 `write_versioned()` 触发

实现位置：`pipeline._gate_review_fn` 内 或 `AgentWorker.run()` 末尾，包裹 `if state.is_v2:` 守红线。

### F2 — `coordinator` agent 在 P2 不订阅任何事件

`coordinator` agent 是 v1 派单的规则引擎，P3 才赋予 observer 行为。P2 阶段：
- `WORKER_PROFILE["coordinator"] = WorkerProfile([], [])`（空 interests）
- subscription 不会触发它
- 现有 Coordinator class 行为完全不动

### F3 — `reviewer` agent 在 P2 可选订阅 artifact 更新（P4 雏形）

`reviewer` 的 interests 包含 `artifact_id_in({MaterialPool, ReportCore, Outline, Script})`，
任何核心 artifact 更新后会触发 reviewer 的 `handle_v2_event`。P2 实现为 emit AgentSilent（"待最终审校"）
或 AgentSpeak（"我看到 X 更新了，等齐了再做完整审校"），具体行为留给 P4 升级。

### F4 — Coordinator 派单与 subscription 触发使用同一把 lock

防御性设计（FR-012）：

```python
# AgentWorker.run() (Coordinator 派单路径) 现有 v1 逻辑外加：
async def run(self) -> None:
    if self.state.is_v2:
        lock = self.state.get_agent_lock(self.agent_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=V2_LOCK_WAIT_SEC)
        except asyncio.TimeoutError:
            # 罕见 — v1 路径下被 v2 lock 卡住；log warn 但继续(降级)
            log.warning("v1 worker %s 锁等待超时,跳过 lock 跑了", self.agent_id)
            return await self._run_unlocked()
        try:
            return await self._run_unlocked()  # 原 run() 主体
        finally:
            lock.release()
    return await self._run_unlocked()
```

这样无论 Coordinator 派单还是 subscription 触发，同一 agent 都串行。v1 路径下 `is_v2=False` 完全短路。

### F5 — Subscription dispatch 是同步函数

`SubscriptionRegistry.dispatch(event)` 内部对每个 interested worker 调 `worker.enqueue_v2(event)`（put_nowait），
是同步的、O(workers count) ≤ 9 次。不需要 await，emit_v2 调用方零阻塞。

worker 的 `_consume_loop` 是异步的，独立 task 跑；inbox.get() 是 await，自然异步。

### F6 — 任务结束需要 cancel consume_loop

`run_harness` 末尾 / state.done 设置后，需要把所有 worker._consume_task cancel 并 await，否则 task 泄漏：

```python
# run_harness 末尾
for worker in workers.values():
    if worker._consume_task is not None and not worker._consume_task.done():
        worker._consume_task.cancel()
        try:
            await worker._consume_task
        except asyncio.CancelledError:
            pass
```

---

## 阶段产出

完成本研究后进入 Phase 1（数据模型 + quickstart），见 [data-model.md](./data-model.md) 与
[quickstart.md](./quickstart.md)。**contracts/ 跳过 — P2 是内部架构演进，无新对外 API/schema。**

**所有 NEEDS CLARIFICATION 状态：✅ 0 项遗留。**
