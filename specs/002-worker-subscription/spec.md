# Feature Specification: Worker 订阅化 + decide-to-speak 闸门（P2）

**Feature Branch**: `002-worker-subscription`

**Created**: 2026-05-28

**Status**: Draft

**Input**: User description: "P2 — Worker 订阅化 + decide-to-speak 闸门 + per-agent 串行锁。把现有「Coordinator 规则路由 → AgentWorker 被点名才跑」的模型，改成「AgentWorker 订阅事件 → 自己决定要不要说话」的群聊雏形。基于 P1（specs/001-v2-chat-protocol-state/，已合并）。"

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 现有 v1 用户感受零变化（Priority: P1）🎯 MVP 红线

正在使用「会汇报」生成汇报材料的现有用户，**完全感知不到**后端正在进行第二阶段架构演进 —— 任务流程、所见的群聊气泡、生成的汇报包、所需时长、错误降级行为，都与 P1 之后的 main 一致。订阅机制、闸门判定、串行锁这些 v2-only 行为在 `harness_version="v1"`（默认）时完全短路。

**Why this priority**: 与 P1 同样的最低底线 —— v1 永远不能因为 v2 推进而出现回归。这条不通过，整个改造就要回滚。

**Independent Test**: 在 main（含 P1）跑一个 demo task → 在 P2 分支不传 `harness_version` 跑同 demo → 比对两边的 `events.jsonl` 顺序与字段一致、`script.md` 一致、`task.json` 状态一致。

**Acceptance Scenarios**:

1. **Given** 用户提交一个常规 3 分钟项目进度汇报任务（不传 `harness_version`），**When** 后端运行在 P2 分支，**Then** 任务的事件流、产物、状态码与 main 完全一致；不出现订阅触发、闸门判定、锁等待的任何 log 或事件。
2. **Given** 同上场景，**When** 任务中某 agent 失败触发现有 Coordinator 兜底，**Then** 兜底路径与 main 行为完全一致（同样的 retry 次数、同样的降级链）。
3. **Given** 同上场景，**When** 用户用 5 个 refine chip 之一触发改写，**Then** refine 流程与 main 完全一致。

---

### User Story 2 - 被 @ 的 agent 自动被唤醒（Priority: P2）

开 v2 feature flag 的实验任务，当某 agent 在发言里 `mentions` 另一个 agent 时，被点名的 agent **自动响应**（不需要 Coordinator 路由派单）。这是 v2 自由群聊的第一个可见行为差异。

**Why this priority**: 这是 P2 的核心交付物 —— worker 真正变成"听总线 + 自决"模型；没有这条，后续 P3-P5 没有协作基础。

**Independent Test**: v2 任务里手工发一条 `agent.speak.mentions=["point-extractor"]` → 校验 events.jsonl 出现 point-extractor 的响应（speak 或 silent）；该响应来自订阅触发，不来自 Coordinator 的 handoff。

**Acceptance Scenarios**:

1. **Given** 任务运行在 `harness_version="v2"`，**When** material agent emit `agent.speak.mentions=["point-extractor"]`，**Then** point-extractor worker 在 5 秒内被订阅机制唤醒；events.jsonl 中能看到 point-extractor 发的 `agent.speak` 或 `agent.silent` 事件（带 `reply_to` 指向原 speak 的 message_id）。
2. **Given** 同上场景，**When** point-extractor 依赖的 `MaterialPool` artifact 已经存在最新版本，**Then** 它响应方式为 `agent.speak`（带 intent）。
3. **Given** 同上场景，**When** point-extractor 依赖的 artifact **缺失**，**Then** 它响应方式为 `agent.silent`，`reason` 包含等待的 artifact 信息（如「等 MaterialPool 先出」）。

---

### User Story 3 - 同 agent 并发触发被串行（Priority: P2）

v2 路径下，如果两条事件几乎同时点名同一个 agent（如 mention 在两条 speak 里），该 agent 不会被并发跑两次（OpenClaw `agentDir` 不能共享，是宪章 V 红线）。系统串行处理，并保留可观察性。

**Why this priority**: P6 才上 EventBus fan-out + 真并发，但 P2 已经需要这个串行保证 —— 因为订阅机制让"同一事件触发多个 worker"或"两条事件触发同一 worker"在真实任务里完全可能。没这条，可能在 demo 第一天就出问题。

**Independent Test**: 单测里构造两条同步 emit 的事件（都 mention reviewer），观察 reviewer 的两次 run_turn **串行执行**（结束时间间隔 ≥ 第一次的耗时），且产物互不踩坏。

**Acceptance Scenarios**:

1. **Given** v2 任务，**When** 在 ≤ 100ms 内连续发两条 `mentions=["reviewer"]`，**Then** reviewer 两次响应**先后**发生，且第二次的 `ts` ≥ 第一次的 `ts + 0.001`（不会同时跑）。
2. **Given** 同上场景，**When** 第一次响应正在跑（持有 lock），**Then** 第二次的事件被 enqueue（默认等 ≤ 60s）；如果第一次 60s 内不释放锁，第二次被记录为 `agent.silent("锁等待超时")` 并继续，整任务不挂。

---

### Edge Cases

- **未知 mention 名**：`mentions=["unknown-agent"]` 时，订阅机制找不到 worker → log warn 跳过，不影响任务。
- **同一 reply_to 被同 agent 重复响应**：第二次响应被 ignore（不发 speak 也不发 silent），log debug。
- **同任务被 @ 超过阈值（默认 N=2）**：第 3 次开始 ignore，避免 @ 来回 flap；事件层 log warn 但任务继续。
- **依赖 artifact 列表为空**：所有 agent 都可响应（不阻塞）。
- **被 @ 但 task.status 已 done/failed**：订阅机制不再触发 worker，避免任务结束后还产生事件。
- **v1 任务的 events.jsonl 写入 v2 事件**（不应发生，FR 守护）：日志报警 + 测试断言 0 命中。

---

## Requirements *(mandatory)*

### Functional Requirements

**订阅机制**

- **FR-001**: 系统 MUST 为每个 AgentWorker 提供 `interests` 字段，声明该 worker 关心哪些 v2 事件（按 `msg_type` + 谓词组合，例：「`agent.speak` 且 `mentions` 包含自己 agent_id」、「`coordinator.intervene` 且 `hint_agent == 自己`」、「`artifact.update` 且 `id ∈ {ReportCore, Outline}`」）。
- **FR-002**: 系统 MUST 在 v2 事件被 emit 时，按各 worker 的 `interests` 谓词分发：匹配则将事件加入该 worker 的事件队列（不直接调 run_turn）。
- **FR-003**: v1 路径（`is_v2=False`）下，订阅机制完全不工作 —— 不计算谓词、不分发、不入队（零开销）。

**decide-to-speak 闸门**

- **FR-004**: 系统 MUST 为每个 worker 提供 `requires` 字段，声明运行依赖哪些 4 核心 artifact（例：reviewer 依赖 `[Script]`、upward-opt 依赖 `[ReportCore]`）。
- **FR-005**: 当 worker 从事件队列拿到一个事件时，系统 MUST 调用 decide-to-speak 闸门，闸门必须返回 3 种结果之一：`speak` / `silent` / `ignore`。
- **FR-006**: decide-to-speak 闸门的规则（P2 起步是确定性，不上 LLM）：
  - 若 worker 已对**同一 reply_to** 响应过 → `ignore`
  - 若 worker 在当前任务被 @ 次数 ≥ 阈值（默认 2）→ `ignore` + log warn
  - 若 worker 的 `requires` artifact 在当前任务里**全部存在**（≥ v1 版本即可）→ `speak`（触发 run_turn）
  - 若 worker 的 `requires` 至少 1 个**缺失** → `silent`（emit AgentSilent，reason 含缺失 artifact 列表）
- **FR-007**: `ignore` 决策 MUST NOT 写入 events.jsonl（仅 log）；`silent` / `speak` MUST 各 emit 对应 v2 事件。

**per-agent 串行锁**

- **FR-008**: 系统 MUST 为每个 agent_id 维护一个 asyncio.Lock；订阅触发 worker 跑 turn 前必须先 acquire；run_turn 完成后必须 release。
- **FR-009**: 锁等待 MUST 有超时（默认 60s）；超时时 worker 不跑 turn，事件被处理为 `agent.silent("锁等待超时")` 而不是抛错。
- **FR-010**: 任意 agent 的 lock 锁定不允许阻塞总线分发（即：bus.emit 立即返回，worker 自己异步等 lock）。

**与 v1 Coordinator 并存**

- **FR-011**: v2 路径下，Coordinator 的现有规则路由（`_resolve_target` / 必经步骤保护 / 默认链）**保持不变**（这是 P3 的事）；订阅机制与 Coordinator 派单**并存**，但不互相干涉。
- **FR-012**: 同一 worker 被 Coordinator 派单（v1 路径）和被订阅触发（v2 路径）使用同一把 lock —— 任何路径都不允许并发跑。
- **FR-013**: v1 任务（默认）的 Coordinator 派单流程与 main 完全一致（无任何 P2 介入痕迹）。

**用户可见层（宪章原则 I 红线）**

- **FR-014**: 任何用户可见层（chat 气泡、SSE 推送、API 错误、导出文件名）MUST NOT 出现 `interests` / `requires` / `decide-to-speak` / `lock_wait_timeout` 这些技术词；它们只允许进 `events.jsonl` / `data/.logs/` / admin 控制台。
- **FR-015**: SSE 推送给前端的 `chat.message` 视觉 / 字段不需为 P2 变更（agent.speak / agent.silent 经过现有翻译层渲染为正常聊天气泡；前端零改动留到 P7）。

**降级（宪章原则 III）**

- **FR-016**: 订阅谓词计算异常 / 闸门规则异常 / 锁获取异常 / 事件队列异常 —— **任何 P2 内部错误**都 MUST NOT 导致整任务 `failed`；按 v1 兜底路径继续。
- **FR-017**: 若某 worker 的订阅队列堆积超过阈值（默认 32）→ 丢弃最老事件 + log warn（防 OOM 但不挂任务）。

**Coordinator / Reviewer 行为边界（宪章原则 IV）**

- **FR-018**: P2 **不改** Coordinator 内部行为（`Coordinator.on_handoff` / `on_failed` / `on_needs_help` / `on_needs_retry` 现有逻辑保持不变）。
- **FR-019**: P2 **不改** Reviewer 现有审校行为（即时质量门 / 流程逻辑校验是 P4 的事）。
- **FR-020**: 在 P2 阶段，allowed_via_subscription 是新增的"触发来源"，但所有触发的 worker 仍按现有 step 代码跑 —— 即"被订阅唤醒"等价于"被 Coordinator 派单"，跑完的产物落盘 + emit 与 v1 完全一致（除多出 v2 事件）。

**测试**

- **FR-021**: 提供单测覆盖：interests 谓词匹配（5 种典型谓词）、decide-to-speak 4 个分支（speak / silent / ignore-by-replyto / ignore-by-count）、串行锁（acquire/release/wait-timeout）、订阅触发与现有 Coordinator 派单互不冲突。
- **FR-022**: 提供集成测试：mock 一个 v2 demo task，校验「发一条 mentions=[X] → X 在 events.jsonl 出现响应事件」端到端跑通。

### Key Entities

- **WorkerInterests**：worker 关心的事件集合声明。属性：`msg_types`（订阅哪些 v2 事件 kind 的集合）、`predicate`（详细谓词，例：mention 包含 self / hint_agent == self / artifact id 在某子集内）。
- **WorkerRequires**：worker 运行依赖的 artifact 列表（4 核心 artifact 子集）。
- **DecisionResult**：decide-to-speak 闸门的返回值（`speak` / `silent` / `ignore`） + 可选解释字符串。
- **PerAgentLock**：agent_id → asyncio.Lock 的映射，全 task 共享。属性：当前持有者 trace、获取等待队列、超时阈值。
- **SubscriptionRegistry**：HarnessState 持有的 worker_id → WorkerInterests 表，用于事件分发时查询。
- **MentionCounter**：任务内 agent_id → 被 @ 次数计数，用于防 flap 决策。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 现有 v1 用户跑 5 个 demo task（覆盖 4 种 report_type × 3 种 duration 之中任 5 组合），生成的 `events.jsonl` / `script.md` / `task.json` 关键字段，与 P1 之后 main 的 baseline **逐字段相同**（ts 浮点宽容；视频/音频 binary 排除）。
- **SC-002**: v2 demo task 跑完后，**至少 1 个 agent 是通过订阅机制唤醒并响应的**（其响应事件可通过 trace 关联到上游 speak 的 message_id）。
- **SC-003**: v2 路径下，依赖 artifact 不齐时被 @ 的 worker emit AgentSilent 的比例 **≥ 80%**（≤ 20% 因防 flap / 重复 reply_to 而 ignore；不允许任何阻塞情况）。
- **SC-004**: 并发触发同 agent 的测试场景下，**两次 run_turn 的执行时间区间不重叠**（验证锁生效）；锁等待超时降级为 silent 的事件占比 **≤ 1%**。
- **SC-005**: 单测覆盖率 100% pass：interests 谓词 + decide-to-speak 4 分支 + 串行锁 + 订阅 vs Coordinator 派单互不冲突；集成测试 1 case end-to-end pass。
- **SC-006**: 任意用户可见层 grep 不到 `interests` / `requires` / `decide_to_speak` / `lock_wait_timeout` 字面量。
- **SC-007**: `pnpm --filter web-frontend build` 与 `pnpm --filter admin-frontend build` 全绿（P2 不动 UI，纯防御性 check）。

---

## Assumptions

- **订阅触发由 EventBus.emit 链路同步发起**：`HarnessState.emit_v2` 内部除了写 events.jsonl 与 bus.emit，还调用一次"按 interests 谓词分发到 worker queue"；不引入新的 background dispatch 线程（asyncio task 即可）。
- **每个 worker 自己一个 asyncio.Queue 接收订阅来的事件**；worker 的 main loop 是 `while True: ev = await queue.get(); await self.handle(ev)`。
- **decide-to-speak 闸门是同步函数**（不调 LLM、不查外部 IO）；规则由 worker 的 `interests` + `requires` + 任务级 MentionCounter / ReplyToRegistry 决定。
- **per-agent lock 的 key 是 agent_id 字符串**；同一 worker 实例只服务一个 agent_id，所以 lock 也只服务一个 worker。
- **订阅触发的 worker 跑 turn 时**，复用 v1 的 `_run_step` 函数（同一份代码）；区别仅是"触发来源"和"跑完后是否额外 emit v2 事件"，产物落盘、Coordinator gate_review 等行为完全一致。
- **mention 阈值 N=2** 是合理默认（防止 A→B→A→B 这种死循环），可通过 config / env var 调整但不在 P2 范围内。
- **lock 超时 60s** 是合理默认（覆盖一个 LLM turn 的常见时长）；超时降级为 silent + log，不挂任务。
- **v1/v2 lock 共用**：v1 任务下也会用 lock（虽然 v1 路径不会触发并发 schedule，但 lock 存在不破坏行为）—— 这保证宪章 V 不会被绕过。

---

## Dependencies

- `specs/001-v2-chat-protocol-state/` — P1 已合并，本 spec 直接依赖：
  - `events_v2.py` 5 类事件 Pydantic 模型
  - `HarnessState.is_v2` / `emit_v2()` / `message_id_registry`
  - `TaskRun.harness_version`
  - `artifacts_v2.next_version()`（用于 requires 检查）
- `docs/开发文档.md` §9.4.5 P2 行 + §3.5 v2 事件协议
- `.specify/memory/constitution.md` 原则 I / III / IV / V

---

## Out of Scope（P2 明确不做）

- Coordinator 行为改造（`_resolve_target` / 必经步骤保护 / 默认链）→ P3
- Reviewer 即时质量门 + 流程逻辑校验 → P4
- 8 个 step prompt 改写为 transcript-aware → P5
- EventBus 真并发（fan-out / 多 worker 同事件并行）+ artifact 乐观并发 → P6
- 前端 @ 高亮 / silent 灰显 / artifact diff → P7
- LLM 驱动的 decide-to-speak（智能体自由判断） → P5 之后视情况升级
- agent 在没被 @ / 没 artifact.update 时主动发言（响应式群聊设计）→ P5 视 prompt 引导
- rolling summary / yes-man 防御 / 预算硬上限 → P8
