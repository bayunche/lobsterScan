---
description: "P2 任务清单 — Worker 订阅化 + decide-to-speak 闸门"
---

# Tasks: Worker 订阅化 + decide-to-speak 闸门（P2）

**Input**: Design documents from `/specs/002-worker-subscription/`

**Prerequisites**: plan.md ✅ · spec.md ✅ · research.md ✅ · data-model.md ✅ · quickstart.md ✅（contracts/ skipped — 纯内部架构演进）

**Tests**: 包含（FR-021/022 显式要求；MVP 验收依赖 SC-005）。

**Organization**: 按 spec.md 3 个 user story 组织（US1 P1 v1 零回归 / US2 P2 mention 自动响应 / US3 P2 并发串行）。

## Format

`- [ ] [TaskID] [P?] [Story?] Description with file path`

- **[P]**: 不同文件、无前置依赖 → 可并行
- **[US1]/[US2]/[US3]**: 归属 user story（Setup / Foundational / Polish 阶段不带 Story label）

## Path Conventions

monorepo 后端单一改动点：`apps/web-backend/`。前端零改动。

```text
apps/web-backend/
├── app/orchestrator/
│   ├── subscription.py        # 新建（~250 行）
│   ├── harness.py             # 扩 ~150 行
│   ├── pipeline.py            # 扩 ~80 行
│   ├── events_v2.py           # 不动（P1 已落地）
│   └── artifacts_v2.py        # 不动（P1 已落地）
└── tests/orchestrator/
    ├── conftest.py            # 扩
    ├── test_v1_regression.py  # 扩
    ├── test_subscription.py            # 新建
    ├── test_per_agent_lock.py          # 新建
    └── test_v2_subscription_e2e.py     # 新建
```

---

## Phase 1: Setup（共享基础）

**Purpose**: 工作分支与开发环境就位（无新依赖、无新工具，本期复用 P1 的 pytest + pytest-asyncio）。

- [ ] T001 确认工作分支 `002-worker-subscription` 已 check out 且基于含 P1 实现的 main；`uv pip install -e apps/web-backend -e apps/admin-backend` 已执行
- [ ] T002 [P] 在 `apps/web-backend/tests/orchestrator/conftest.py` 增补 `stub_state_v2`（is_v2=True）与 `stub_state_v1`（is_v2=False）两个 fixture，复用 P1 已有的 `tmp_outputs_dir` / `mock_backend`

---

## Phase 2: Foundational（阻塞所有 user story 的前置）

**Purpose**: subscription.py 模块骨架 + HarnessState 字段扩展。3 个 user story 都依赖这层；本阶段不构造任何运行时副作用（v1 路径仍然零开销）。

**⚠️ CRITICAL**: 本阶段未完成前，US1 / US2 / US3 都不能开工。

- [ ] T003 创建 `apps/web-backend/app/orchestrator/subscription.py`，加入 module 头部：导入、`log` logger、3 个 module-level 常量 `V2_MENTION_LIMIT` / `V2_LOCK_WAIT_SEC` / `V2_INBOX_MAX`（均支持 env var override，默认 2 / 60 / 32）
- [ ] T004 [P] 在 `apps/web-backend/app/orchestrator/subscription.py` 中定义 `Predicate` 类型别名 + 3 个工厂 helper：`mention_includes(self_id)` / `hint_agent_is(self_id)` / `artifact_id_in(ids)`（全部 isinstance 守护，非匹配 event 返回 False，不抛错）
- [ ] T005 [P] 在 `apps/web-backend/app/orchestrator/subscription.py` 中定义 `WorkerProfile` frozen dataclass（slots=True；interests: tuple[Predicate, ...]；requires: tuple[str, ...]）
- [ ] T006 [P] 在 `apps/web-backend/app/orchestrator/subscription.py` 中定义 `WORKER_PROFILE: dict[str, WorkerProfile]` 静态注册表，覆盖 9 个 agent（material/point-extractor/structure/upward-opt/copywriter/html-designer/video-producer/reviewer/coordinator），interests 与 requires 按 data-model.md §3 表格逐项配齐
- [ ] T007 [P] 在 `apps/web-backend/app/orchestrator/subscription.py` 中定义 `DecisionResult` enum（SPEAK / SILENT / IGNORE）
- [ ] T008 [P] 在 `apps/web-backend/app/orchestrator/subscription.py` 中定义 `MentionCounter` 与 `ReplyToRegistry` dataclass（任务级状态容器；接口按 data-model.md §5/§6）
- [ ] T009 在 `apps/web-backend/app/orchestrator/subscription.py` 中定义 `SubscriptionRegistry` dataclass 骨架（workers / profiles / mention_counter / reply_to_registry 四字段 + `register()` 方法；`dispatch()` 暂留 `pass` 占位，US2 阶段补完）
- [ ] T010 在 `apps/web-backend/app/orchestrator/harness.py` 中给 `HarnessState` 增加两个字段：`subscriptions: SubscriptionRegistry | None = None` 与 `agent_locks: dict[str, asyncio.Lock] = field(default_factory=dict)`；增加 `get_agent_lock(agent_id) -> asyncio.Lock` 方法（lazy-init；仅在运行循环内调用）
- [ ] T011 在 `apps/web-backend/app/orchestrator/harness.py` 中给 `AgentWorker.__init__` 增加 P2 字段占位：`self.inbox: asyncio.Queue[V2EventBase] | None = None` 与 `self._consume_task: asyncio.Task | None = None`（默认 None，v1 路径下永不构造）

**Checkpoint**: subscription.py 类型 + HarnessState 字段就位。v1 路径仍零开销（is_v2=False 时 subscriptions/inbox 永远 None）。可启动 US1/US2/US3 并行。

---

## Phase 3: User Story 1 — 现有 v1 用户感受零变化（Priority: P1）🎯 MVP 红线

**Goal**: 跑现有 demo task 不传 `harness_version`，`events.jsonl` / `script.md` / `task.json` 与含 P1 的 main 逐字段相同；订阅 / 闸门 / 锁的任何 log / 事件都不出现。

**Independent Test**: 同 demo 在 main 与 P2 分支跑 → diff `events.jsonl`、`script.md`、`task.json`、状态码全相同；grep events.jsonl 0 命中 `msg_type` 字段。

### Tests for US1（必写 — SC-001 验收依赖）

- [ ] T012 [P] [US1] 在 `apps/web-backend/tests/orchestrator/test_v1_regression.py` 扩展 case：v1 `run_harness` 跑完，断言 `state.subscriptions is None` 且 `state.agent_locks == {}`
- [ ] T013 [P] [US1] 在 `apps/web-backend/tests/orchestrator/test_v1_regression.py` 扩展 case：v1 路径下任意 `worker.inbox is None` 且 `worker._consume_task is None`
- [ ] T014 [P] [US1] 在 `apps/web-backend/tests/orchestrator/test_v1_regression.py` 扩展 case：v1 路径下 `events.jsonl` 任意行 grep `"msg_type"` 命中数 == 0（沿用 P1 已有的 replay_check 风格断言）
- [ ] T015 [P] [US1] 在 `apps/web-backend/tests/orchestrator/test_v1_regression.py` 扩展 case：`state.emit_v2(any_event)` 在 is_v2=False 时立即 return；事件未写盘、未 bus.emit、未触发 dispatch

### Implementation for US1

- [ ] T016 [US1] 在 `apps/web-backend/app/orchestrator/harness.py` `run_harness()` 入口确认 v1 分支不构造 `SubscriptionRegistry`、不调 `start_v2_consumer`、不注册任何 worker；通过 T012-T015 断言验证
- [ ] T017 [US1] 在 `apps/web-backend/app/orchestrator/harness.py` `HarnessState.emit_v2()` 第一行确认 `if not self.is_v2: return`（P1 已有，此处只校验不被 P2 改动破坏）；通过 T015 断言验证

**Checkpoint**: v1 任务运行行为与含 P1 的 main 字段级相同，可独立验证发布。MVP 红线达成。

---

## Phase 4: User Story 2 — 被 @ 的 agent 自动被唤醒（Priority: P2）

**Goal**: v2 任务下，`agent.speak.mentions=[X]` → X 在 ≤ 5 秒内出现 `agent.speak` 或 `agent.silent` 响应事件，`reply_to` 指向原 speak 的 message_id。

**Independent Test**: v2 demo task 跑完，`events.jsonl` 至少 1 对 mention → 响应链路（SC-002）；handle_v2_event 不调 `_run_step`（chat overlay 路径）。

### Tests for US2（必写 — FR-021/022 + SC-002/003/005 验收依赖）

- [ ] T018 [P] [US2] 创建 `apps/web-backend/tests/orchestrator/test_subscription.py`，加入 5 条 predicate 单测：`mention_includes` 命中 / 不命中、`hint_agent_is` 命中 / 不命中、`artifact_id_in` 命中
- [ ] T019 [P] [US2] 在 `apps/web-backend/tests/orchestrator/test_subscription.py` 加入 `decide_to_speak` 4 分支单测：SPEAK（requires 满足）、SILENT（requires 缺失 + reason 含 artifact 名）、IGNORE（重复 reply_to）、IGNORE（mention 计数超阈）
- [ ] T020 [P] [US2] 在 `apps/web-backend/tests/orchestrator/test_subscription.py` 加入 `SubscriptionRegistry.dispatch` 路由单测：相同 event 同时命中多个 worker → 全部入队；不命中的 worker inbox 长度不变
- [ ] T021 [P] [US2] 在 `apps/web-backend/tests/orchestrator/test_subscription.py` 加入 inbox 满测试：连续 `enqueue_v2` 超 `V2_INBOX_MAX` → 丢最老 + 新事件成功入队 + log warn 命中（FR-017）
- [ ] T022 [US2] 创建 `apps/web-backend/tests/orchestrator/test_v2_subscription_e2e.py`，加入端到端 case：is_v2=True 状态下手工 `emit_v2(AgentSpeak(mentions=["point-extractor"]))` → 校验 `point-extractor` 在 events.jsonl 出现响应事件（speak 或 silent，带 `reply_to` 指向原 speak.message_id）

### Implementation for US2

- [ ] T023 [P] [US2] 在 `apps/web-backend/app/orchestrator/subscription.py` 实现 `decide_to_speak()` 纯函数（按 data-model.md §8 4 条规则顺序：reply_to dedup → mention 阈值 → requires 检查 → SPEAK；返回 `(DecisionResult, reason)` 元组）
- [ ] T024 [US2] 在 `apps/web-backend/app/orchestrator/subscription.py` 实现 `SubscriptionRegistry.dispatch(event)`：遍历 profiles，对每个 worker 跑 `any(p(event, agent_id) for p in profile.interests)`，命中即 `worker.enqueue_v2(event)`；预测异常用 try/except + log.warning（FR-016）
- [ ] T025 [US2] 在 `apps/web-backend/app/orchestrator/harness.py` 给 `AgentWorker` 加 `start_v2_consumer()` / `enqueue_v2(event)` / `_consume_loop()` 三个方法（按 data-model.md §10）：start_v2_consumer 构造 `asyncio.Queue(maxsize=V2_INBOX_MAX)` + 起 `_consume_loop` task；enqueue_v2 处理 `QueueFull` → 丢最老再放新（FR-017）；_consume_loop 死循环 + 异常仅 log.warning（FR-016）
- [ ] T026 [US2] 在 `apps/web-backend/app/orchestrator/harness.py` 给 `AgentWorker` 加 `handle_v2_event(event)`（按 data-model.md §10 + research.md §0 chat overlay 实现）：读 profile → 计算 `available_artifacts`（基于 `artifacts_v2.next_version()` - 1）→ 调 `decide_to_speak` → IGNORE log.debug 直接返回；SPEAK emit `AgentSpeak(text=f"收到 — {reason}", reply_to=event.message_id, intent="confirm")`；SILENT emit `AgentSilent(reason=reason[:30])`；**不调 `_run_step`**
- [ ] T027 [US2] 在 `apps/web-backend/app/orchestrator/harness.py` `HarnessState.emit_v2()` 末尾（写 events.jsonl + bus.emit 之后）调 `self.subscriptions.dispatch(event)`（守红线：`if self.subscriptions is not None`，外层 try/except + log.warning，FR-016）
- [ ] T028 [US2] 在 `apps/web-backend/app/orchestrator/harness.py` `run_harness()` 增加 is_v2=True 分支：从 `subscription.WORKER_PROFILE` 取 profile → 调 `state.subscriptions.register(agent_id, worker, profile)` → 调 `worker.start_v2_consumer()`；末尾任务结束时 cancel + await 所有 `worker._consume_task`（research.md §F6）
- [ ] T029 [US2] 在 `apps/web-backend/app/orchestrator/pipeline.py` 给每个 step 完成处增加 v2-only emit hook（守 `if state.is_v2`）：emit 1 条 `AgentSpeak(text=本步骤摘要, mentions=[下一棒 agent_id 按 DEFAULT_NEXT_STEP], intent="propose")` + 若该 step 对应 4 核心 artifact 之一则 emit `ArtifactUpdate`；text 摘要可从 output_json 取（占位 `_summarize_output()`，P5 升级）

**Checkpoint**: v2 任务下被 @ 的 agent 通过订阅自动响应（speak/silent）；chat overlay 完整呈现在 events.jsonl；Coordinator 路径仍按 v1 完整跑 LLM 工作。US2 可独立验证。

---

## Phase 5: User Story 3 — 同 agent 并发触发被串行（Priority: P2）

**Goal**: ≤ 100 ms 内两条 mention 同一 agent 的事件 → 两次响应串行（ts 间隔 ≥ 第一次耗时）；锁等待超过 `V2_LOCK_WAIT_SEC`（60s）→ 降级为 `agent.silent("锁等待超时")` 不挂任务。

**Independent Test**: 单测构造两条同时 emit 的 mention 同 agent → 两次 run_turn 时间区间不重叠（SC-004）；超时分支 emit silent 事件占比 ≤ 1%。

### Tests for US3（必写 — FR-021 + SC-004 验收依赖）

- [ ] T030 [P] [US3] 创建 `apps/web-backend/tests/orchestrator/test_per_agent_lock.py`，加入 `test_same_agent_serial`：同 agent_id 两次 `async with lock` 任务，断言执行顺序 `[A-acq, A-rel, B-acq, B-rel]`（不重叠）
- [ ] T031 [P] [US3] 在 `apps/web-backend/tests/orchestrator/test_per_agent_lock.py` 加入 `test_different_agents_parallel`：不同 agent_id 拿到不同 Lock 实例（`is not` 断言）
- [ ] T032 [P] [US3] 在 `apps/web-backend/tests/orchestrator/test_per_agent_lock.py` 加入 `test_lock_wait_timeout_silent`：monkeypatch `V2_LOCK_WAIT_SEC=0.05` + 让 holder 持锁 0.2s → 第二个调用 emit `AgentSilent(reason="锁等待超时")` 而非抛错
- [ ] T033 [P] [US3] 在 `apps/web-backend/tests/orchestrator/test_subscription.py` 加入 `test_v1_subscription_coexist`：Coordinator 派单（v1 路径）与 subscription 同 agent 触发使用同一把 lock，串行执行不双跑（FR-012）

### Implementation for US3

- [ ] T034 [US3] 在 `apps/web-backend/app/orchestrator/harness.py` `AgentWorker.handle_v2_event()` SPEAK/SILENT emit 前后包裹 per-agent lock：`await asyncio.wait_for(lock.acquire(), timeout=V2_LOCK_WAIT_SEC)`；TimeoutError 分支 emit `AgentSilent(reason="锁等待超时")` 后 return（FR-009）；finally `lock.release()`（FR-008/010）
- [ ] T035 [US3] 在 `apps/web-backend/app/orchestrator/harness.py` `AgentWorker.run()`（Coordinator 派单路径）增加 is_v2=True 时也走同一把 lock（research.md §F4 模板）：超时分支降级为 log.warning 后跳过 lock 继续跑（v1 兜底）；v1 路径完全短路保持原行为

**Checkpoint**: 同 agent 并发触发严格串行；lock 等待超时降级为 silent；OpenClaw agentDir 不共享红线被守住。US3 可独立验证。

---

## Phase 6: Polish & Cross-Cutting（横切验收）

**Purpose**: 跨 user story 的红线自检 + 文档收尾。建议在 US1/2/3 都 checkpoint 通过后串行执行。

- [ ] T036 [P] 运行 `uv run python -m app.orchestrator.replay_check data/outputs/<v2-task-id>/events.jsonl` 校验 v2 e2e events.jsonl 全部 schema valid（沿用 P1 工具）
- [ ] T037 [P] grep 红线审计：`grep -rn 'interests\|requires\|decide_to_speak\|lock_wait_timeout\|_consume_loop\|enqueue_v2' apps/web-backend/app/api/ apps/web-frontend/ apps/admin-frontend/` 应 0 命中（FR-014 + SC-006）
- [ ] T038 在 main 与 002-worker-subscription 分支分别跑 5 个 v1 demo task（覆盖 4 种 report_type 任 5 组合）→ diff 两边 `events.jsonl` / `script.md` / `task.json` 字段级相同（ts 浮点宽容，video/audio binary 排除）—— SC-001 验收
- [ ] T039 [P] `pnpm --filter web-frontend build` + `pnpm --filter admin-frontend build` 全绿（P2 不动 UI，防御性 check —— SC-007）
- [ ] T040 [P] 跑 quickstart.md §4 单测三件套 + §3 v2 demo curl 流程，确认 mention → 响应链路与 quickstart 描述一致
- [ ] T041 在 `CLAUDE.md` 的「Active spec-driven feature」段落把 P2 的「planning」改为「Implementation in progress / Implemented」（视落地状态），并在「Architecture」段落新增对 `subscription.py` 模块的一句说明

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无前置 — 可立即开始
- **Foundational (Phase 2)**: 依赖 Setup — **阻塞** US1/US2/US3
- **US1 (Phase 3)**: 依赖 Foundational；与 US2/US3 互不依赖（可并行）
- **US2 (Phase 4)**: 依赖 Foundational；T029（pipeline 改造）需要 T025-T028 已落地
- **US3 (Phase 5)**: 依赖 Foundational；T034 依赖 T026（handle_v2_event 已存在）
- **Polish (Phase 6)**: 依赖 US1 + US2 + US3 全部 checkpoint 通过

### User Story Dependencies

- **US1 (P1)**: 独立可验证（v1 路径不依赖任何 v2 实现，仅靠 emit_v2 短路 + 字段默认 None）
- **US2 (P2)**: 独立可验证（chat overlay 不调 _run_step，pipeline.py per-step hook 落地即可端到端跑）
- **US3 (P2)**: 独立可验证（lock 行为通过 test_per_agent_lock 即可断言；不要求 US2 完成）
- **US2 与 US3**: 在 handle_v2_event 中是协同关系（lock 包裹 emit），实现顺序建议 US2 → US3（T034 编辑同一函数）

### Within Each User Story

- 必须先写测试再写实现（TDD — 测试初始应失败）
- subscription.py 内部：常量 → 类型 → enum → 数据类 → 注册表 → 纯函数（按 Foundational T003-T009 顺序）
- harness.py 内部：HarnessState 字段（T010） → AgentWorker 字段（T011） → consumer 三件套（T025） → handle_v2_event（T026） → emit_v2 dispatch（T027） → run_harness 集成（T028）

### Parallel Opportunities

- Foundational T004-T008（subscription.py 内独立小节）可并行（同文件不同段；建议一人提交避免合并冲突）
- US1 全部测试任务（T012-T015）独立可并行
- US2 测试任务（T018-T021）独立可并行；T022 e2e 测试需要 T025-T028 已落地后跑
- US3 测试任务（T030-T033）独立可并行
- Polish 阶段 T036/T037/T039/T040 独立可并行；T038（v1 diff）需要 US1 全部完成
- US2 与 US3 可由两人并行（注意 handle_v2_event 是合并点）

---

## Parallel Example: User Story 2

```bash
# US2 测试三件套（不同文件 / 不同 fixture，可同时跑）：
Task: "predicate 5 case in apps/web-backend/tests/orchestrator/test_subscription.py"
Task: "decide_to_speak 4 branches in apps/web-backend/tests/orchestrator/test_subscription.py"
Task: "dispatch routing + inbox overflow in apps/web-backend/tests/orchestrator/test_subscription.py"

# US2 实现（顺序：纯函数 → 注册表 → worker 方法 → harness 集成）：
Task: "decide_to_speak in apps/web-backend/app/orchestrator/subscription.py"
Task: "SubscriptionRegistry.dispatch in apps/web-backend/app/orchestrator/subscription.py"
Task: "AgentWorker.start_v2_consumer/enqueue_v2/_consume_loop in apps/web-backend/app/orchestrator/harness.py"
Task: "AgentWorker.handle_v2_event in apps/web-backend/app/orchestrator/harness.py"
```

---

## Implementation Strategy

### MVP First（US1 优先 — 红线保护）

1. 完成 Phase 1: Setup（T001-T002）
2. 完成 Phase 2: Foundational（T003-T011）— **必须先于任何 user story**
3. 完成 Phase 3: US1（T012-T017）
4. **STOP & VALIDATE**：跑 v1 demo task baseline diff → 字段级相同 → MVP 红线达成
5. 此时如果需要 hold（如先发 P1.5 patch），整个 P2 可保留在分支上不发，main 0 风险

### Incremental Delivery（推荐）

1. Setup + Foundational → 基础就位
2. US1 → diff baseline 通过 → 可宣告"P2 不破坏 v1"（最低交付价值）
3. US2 → v2 demo 跑通 mention → 响应链路 → 可对内演示"群聊雏形"
4. US3 → 并发串行测试通过 → 可宣告"宪章 V 红线被守住"
5. Polish → grep 审计 / 前端 build / 文档更新 → 可发 PR

### Parallel Team Strategy（如有 2 人）

1. 共同完成 Setup + Foundational
2. Foundational checkpoint 后：
   - 开发者 A：US1（短、纯测试 + emit_v2 短路验证）+ US2（核心实现）
   - 开发者 B：US3（lock 实现 + 测试，与 US2 在 handle_v2_event 是合并点 — 约定 US2 先合）
3. Polish 串行一次

---

## Notes

- **[P] 任务** = 不同文件且无依赖
- **[US1]/[US2]/[US3] 标签**：仅 user story 阶段的任务带；Setup / Foundational / Polish 阶段任务不带
- **测试要求**：FR-021/022 明确要求；TDD 顺序（先写测试再写实现），断言初始失败
- **红线**：用户可见层不得出现 `interests / requires / decide_to_speak / lock_wait_timeout / _consume_loop / enqueue_v2`（FR-014 + SC-006）；Polish T037 兜底审计
- **Coordinator 不改**（FR-018）；Reviewer 不改（FR-019）；P2 subscription = chat overlay（research.md §0）
- 每完成一个 checkpoint 建议 commit，便于回滚

---

## Validation Checklist

- ✅ 所有任务符合 `- [ ] [TaskID] [P?] [Story?] Description with file path` 格式
- ✅ 3 个 user story 各自独立可测试可验收（US1 SC-001 / US2 SC-002+003 / US3 SC-004）
- ✅ Foundational 阶段完成前任何 user story 都不能开工
- ✅ 测试任务先于实现任务（每个 user story 内）
- ✅ 文件路径精确到模块级（apps/web-backend/app/orchestrator/{subscription,harness,pipeline}.py 与 tests/orchestrator/*.py）
- ✅ 横切 Polish 在所有 user story checkpoint 后执行
