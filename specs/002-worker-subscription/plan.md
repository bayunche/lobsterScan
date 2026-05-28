# Implementation Plan: Worker 订阅化 + decide-to-speak 闸门（P2）

**Branch**: `002-worker-subscription` | **Date**: 2026-05-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-worker-subscription/spec.md`

---

## Summary

为 P1 已落地的 v2 事件协议（agent.speak / silent / coordinator.intervene / reviewer.verdict / artifact.update）添加**订阅分发层**：每个 AgentWorker 声明 `interests` 谓词与 `requires` artifact 依赖；`HarnessState.emit_v2()` 在 v2 路径下额外按谓词分发事件给感兴趣的 worker；每个 worker 用 `asyncio.Queue` inbox + 长生命周期消费协程承接；decide-to-speak 闸门按 4 条确定性规则决策 speak/silent/ignore；per-agent `asyncio.Lock` 串行守住 OpenClaw agentDir 不共享的红线。

**重要架构定位（详 research.md §0）**：P2 阶段 subscription 是 **chat overlay**（群聊呈现层），不重复触发 `_run_step`。Coordinator 的 v1 链路仍是 LLM 工作的唯一驱动；subscription 只 emit 业务化 AgentSpeak/AgentSilent 事件，把"被 @"语义在群聊层完整呈现。这是 FR-011/FR-018（Coordinator P2 不改）与 FR-002/FR-020（subscription 驱动 worker）两个看似冲突约束的最小冲突实现。**P3 拆解 Coordinator 后**，subscription 才会被升级为真实的 work-driver。

技术路线：
- **零新外部依赖**：复用 stdlib `asyncio.Queue` + `asyncio.Lock` + `asyncio.wait_for`
- **v1 路径零开销**：`is_v2=False` 时 SubscriptionRegistry 根本不构造；`emit_v2` 第一行 short-circuit return
- **Coordinator 行为完全不变**：subscription 在 v2 路径下额外挂在 emit_v2 后面，与 Coordinator 的 v1 路由互不感知
- **Worker 拆两个入口**：`AgentWorker.run()`（Coordinator 派单，跑 `_run_step`，保持 v1 行为）+ `AgentWorker.handle_v2_event()`（订阅触发，跑 decide-to-speak gate，emit AgentSpeak/AgentSilent，不调 `_run_step`）

---

## Technical Context

| 项 | 选择 |
|---|---|
| **Language/Version** | Python 3.11+ |
| **Primary Dependencies** | stdlib `asyncio` only；复用 P1 落地的 `events_v2.py` / `ids.py` / `artifacts_v2.py` |
| **Storage** | 无新存储；订阅状态在 `HarnessState`（任务级内存）|
| **Testing** | pytest + pytest-asyncio（P1 已配）；复用 `ScriptedBackend` / `mock_backend` / `stub_state` fixtures |
| **Target Platform** | Linux server（含 WSL2 dev）；Python 进程内 asyncio |
| **Project Type** | web-service（`apps/web-backend`，monorepo 单后端） |
| **Performance Goals** | v1 路径 0 性能回归（baseline = P1 之后 main）；v2 路径 subscription 分发开销 < 1ms / 事件 / worker（in-memory 谓词调用 + queue.put_nowait） |
| **Constraints** | 100% 向后兼容（v1 + P1 v2 现有行为全保留）；100% 无外部新依赖；100% 不改 agent prompt / Coordinator 内部逻辑 / Reviewer 行为 |
| **Scale/Scope** | 新增 `subscription.py` 约 250 行；扩展 `harness.py` ~150 行；扩展 `pipeline.py` ~80 行（per-step v2 emit hook）；新增单测 ~250 行 |

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 是否通过 | 证据 |
|---|---|---|
| **I. 用户可见层脱敏** | ✅ PASS | FR-014/15 明确禁止 `interests / requires / decide_to_speak / lock_wait_timeout` 出现在 chat 气泡 / SSE / 错误提示 / 导出文件名；前端 P7 才动 |
| **II. 中文为产品语言** | ✅ PASS | P2 不动任何 UI / 前端文案；订阅化 emit 的 AgentSpeak/Silent text 用业务化中文 |
| **III. 降级而非崩溃** | ✅ PASS | FR-016/17 明确：订阅分发 / 闸门判定 / 锁等待 / 队列溢出任何异常都不可让任务 failed |
| **IV. Coordinator / Reviewer 边界** | ✅ PASS | FR-011/18/19 显式：P2 不改 Coordinator / Reviewer 内部行为；worker 自决是否 speak（不路由别人） |
| **V. Agent 自治与隔离** | ✅ PASS | per-agent `asyncio.Lock`（FR-008/12）正是为了守住 agentDir 不共享原则 |

**Gate 结论：通过 0 violation，可进 Phase 0。**

Complexity Tracking：N/A（无需 justify 复杂度违规）。

---

## Project Structure

### Documentation (this feature)

```text
specs/002-worker-subscription/
├── plan.md              # 本文件（/speckit-plan 输出）
├── spec.md              # /speckit-specify 已就位
├── research.md          # Phase 0 输出（本次产出，8 项决策）
├── data-model.md        # Phase 1 输出（本次产出）
├── quickstart.md        # Phase 1 输出（本次产出）
├── checklists/requirements.md  # /speckit-specify 已就位
└── tasks.md             # Phase 2 输出（由 /speckit-tasks 生成，本命令不产）
```

**contracts/ 目录跳过** — P2 是内部架构演进（无新对外 API / 事件 schema；订阅是后端实现细节），按 skill 指引"Skip if project is purely internal"。

### Source Code (repository root)

只动 `apps/web-backend/`，前端零改动。

```text
apps/web-backend/
├── app/
│   ├── orchestrator/
│   │   ├── subscription.py     # 【新增】订阅注册表 + 谓词函数 + 闸门 + 锁 (~250 行)
│   │   ├── harness.py          # 扩 ~150 行：
│   │   │                       #   - HarnessState 新增 subscriptions / agent_locks 字段
│   │   │                       #   - emit_v2() 末尾 dispatch
│   │   │                       #   - AgentWorker 新增 inbox + _consume_loop + handle_v2_event
│   │   │                       #   - run_harness() 在 is_v2=True 时启动 consume_loop
│   │   ├── pipeline.py         # 扩 ~80 行：
│   │   │                       #   - 在 v2 路径下，每个 step 完成后 emit per-step
│   │   │                       #     AgentSpeak（基于 output_json）+ artifact.update
│   │   ├── events_v2.py        # 不动（P1 落地）
│   │   ├── artifacts_v2.py     # 不动（P1 落地）
│   │   ├── ids.py              # 不动（P1 落地）
│   │   └── replay_check.py     # 不动（P1 落地）
│   └── api/                    # 不动
└── tests/
    └── orchestrator/
        ├── test_subscription.py            # 【新增】interests 谓词 + 闸门 4 分支 (~120 行)
        ├── test_per_agent_lock.py          # 【新增】串行锁 + 超时降级 (~50 行)
        ├── test_v2_subscription_e2e.py     # 【新增】端到端：mention → 自动响应 (~80 行)
        ├── test_v1_regression.py           # 扩 ~30 行：v2-flag-off 路径下 SubscriptionRegistry 不构造
        └── conftest.py                     # 扩 ~30 行：subscription_state fixture
```

**Structure Decision**: 新增独立 module `subscription.py` 把订阅逻辑（谓词 / 注册表 / 闸门 / 锁 / 计数器）聚拢一处，避免再撑大 `harness.py`。`subscription.py` 是 self-contained 模块（只依赖 stdlib + events_v2.py + ids.py），便于 P3 阶段如果需要替换为真 work-driver 也好改。

---

## Complexity Tracking

无 violations，本段不填。

---

## Phase 0 Output

详见 [research.md](./research.md) — **8 项关键决策已拍板**：

| # | 决策 | 选择 |
|---|---|---|
| 0 | P2 subscription 是 chat overlay 还是真 work-driver？ | **chat overlay**（emit speak/silent，不重跑 _run_step；解决 FR-011 与 FR-002/20 的冲突约束） |
| 1 | interests 谓词表达 | **Python `Callable[[V2EventBase, str], bool]`** + 预制 helper（mention_includes / hint_agent_is / artifact_id_in） |
| 2 | SubscriptionRegistry 位置 | **新模块 `subscription.py`**，实例挂在 `HarnessState.subscriptions` |
| 3 | per-agent lock 位置 | **`HarnessState.agent_locks: dict[str, asyncio.Lock]`**（任务级隔离） |
| 4 | worker 事件队列 | **`asyncio.Queue(maxsize=32)` per worker + 长生命周期 `_consume_loop()` 协程** |
| 5 | dispatch 入口点 | **在 `HarnessState.emit_v2()` 末尾** 调 `subscriptions.dispatch(event)`（is_v2=True 时） |
| 6 | v1 零开销策略 | **is_v2=False 时根本不构造 SubscriptionRegistry / agent_locks**；emit_v2 已有 short-circuit return |
| 7 | mention 阈值 + lock 超时配置 | **module-level 常量 + env var override**（`V2_MENTION_LIMIT` / `V2_LOCK_WAIT_SEC` / `V2_INBOX_MAX`），不进 settings |

## Phase 1 Output

- **数据模型**：[data-model.md](./data-model.md) — 7 个新类型 + 状态转移
- **快速上手**：[quickstart.md](./quickstart.md) — 开发者怎么给一个 agent 配 interests / requires，怎么单测

## Post-Design Constitution Re-Check

Phase 1 设计完成后回头再核 5 大原则：

| 原则 | Post-Design 结论 |
|---|---|
| I 用户可见层脱敏 | ✅ 所有新字段（interests / requires / decision / lock_holder）都在 HarnessState 内部，不在任何 to_dict() / SSE payload 路径 |
| II 中文为产品语言 | ✅ 无 UI 改动；subscription emit 的 AgentSpeak/Silent text 是业务化中文 |
| III 降级而非崩溃 | ✅ `subscription.dispatch` / `decide_to_speak` / lock acquire 全部 try/except，异常仅 log warn |
| IV Coordinator / Reviewer 边界 | ✅ Coordinator 不动；Reviewer 也不动；subscription 作为独立模块挂在 emit_v2 后面，不调 Coordinator |
| V Agent 自治与隔离 | ✅ per-agent lock 确保 OpenClaw agentDir 同 agent 永不并发；与 v1 Coordinator 派单也用同一把 lock |

**Re-check 结论：通过。可进 `/speckit-tasks`。**
