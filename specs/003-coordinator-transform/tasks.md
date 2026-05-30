---
description: "P3 任务清单 — Coordinator 转型 + subscription work-driver"
---

# Tasks: Coordinator 转型(observer + gatekeeper)+ subscription work-driver（P3）

**Input**: Design documents from `/specs/003-coordinator-transform/`

**Prerequisites**: plan.md ✅ · spec.md ✅ · research.md ✅ · data-model.md ✅ · quickstart.md ✅（contracts/ skipped — 内部架构,复用 P1 InterveneKind）

**Tests**: 包含(FR-025/026 显式要求;SC-007 验收依赖)。

**Organization**: 按 spec.md 5 个 user story 组织。**排序调整**:US5(gatekeeper,P3)不依赖宪章,排在 US4 之前;**US4(drift,P3)被 Phase 0 宪章修订阻塞**,排最后。

## Format

`- [ ] [TaskID] [P?] [Story?] Description with file path`

- **[P]**: 不同文件、无前置依赖 → 可并行
- **[US1]~[US5]**: 归属 user story;Setup / Foundational / Polish 阶段不带 Story label

## Path Conventions

monorepo 后端单一改动点:`apps/web-backend/`。前端零改动。

```text
apps/web-backend/
├── app/orchestrator/
│   ├── coordinator_observer.py   # 新建(~280 行)
│   ├── harness.py                # 扩 ~120 行
│   ├── pipeline.py               # 调 ~30 行
│   ├── subscription.py           # 不动(P2)
│   ├── events_v2.py              # 不动(P1)
│   └── artifacts_v2.py           # 不动(P1)
└── tests/orchestrator/
    ├── conftest.py               # 扩(mock_drift fixture)
    ├── test_v1_regression.py     # 扩
    ├── test_v2_workdriver.py     # 新建
    ├── test_observer.py          # 新建
    └── test_drift.py             # 新建
```

---

## Phase 1: Setup（共享基础）

**Purpose**: 工作分支与开发环境就位(无新依赖,复用 P1/P2 的 pytest + pytest-asyncio)。

- [X] T001 确认工作分支 `003-coordinator-transform` 基于含 P1+P2 的 main;`uv sync --project apps/web-backend --extra test` 已执行;`pytest apps/web-backend/tests` 现有 61 case 全绿(P3 起点 baseline)
- [X] T002 [P] 在 `apps/web-backend/tests/orchestrator/conftest.py` 增补 `mock_drift` fixture(包装 `set_default_drift_judge` / `get_default_drift_judge`,yield 一个 `_make(drifted, text)` 工厂,teardown 还原),供 US4 drift 测试注入

---

## Phase 2: Foundational（阻塞所有 user story 的前置）

**Purpose**: `coordinator_observer.py` 模块骨架(不含 drift 调用逻辑)+ `HarnessState` 字段扩展。本阶段不构造任何 v2 运行时副作用(v1 路径仍零开销)。

**⚠️ CRITICAL**: 本阶段未完成前,US1~US5 都不能开工。

- [X] T003 创建 `apps/web-backend/app/orchestrator/coordinator_observer.py`,加 module 头 + `log` logger + 4 个 env-var override 常量 `OBSERVER_TICK_SEC`(0.5)/ `DRIFT_EVERY_N_TICK`(10)/ `DRIFT_RECENT_K`(5)/ `STAGNATION_MAX_RETRY`(3)
- [X] T004 [P] 在 `coordinator_observer.py` 定义 `DriftVerdict` frozen dataclass(`drifted: bool` / `restate_text: str`)+ `DriftJudge` ABC(`async judge(goal, recent_speaks) -> DriftVerdict`)+ `NoDriftJudge` 默认实现(永远 `drifted=False`,不调 LLM)+ `get_default_drift_judge` / `set_default_drift_judge` 注入点(同 `agent_backend` 模式)
- [X] T005 [P] 在 `coordinator_observer.py` 定义 `GateResult` dataclass + `ArtifactGate` 类(`CORE` 4 元组 + `check(task_id) -> GateResult`,复用 `artifacts_v2.next_version`,latest = next_version-1;异常算缺失,FR-024 降级)
- [X] T006 在 `coordinator_observer.py` 定义 `CoordinatorObserver` dataclass 骨架:字段(state/workers/goal/gate/drift_judge/_task/_tick/_stagnation_retries)+ `start()` / `stop()`(asyncio task 起停,cancel+await 清理)+ `_loop()`(周期 sleep + tick + 调 `_is_quiescent`/`_on_quiescence`/`_check_drift` 占位)+ `_is_quiescent()`(inflight==0 + inbox 全空 + bootstrapped + 未完成);`_on_quiescence`/`_check_drift` 暂留 `pass`(US3/US5/US4 补)
- [X] T007 [P] 在 `coordinator_observer.py` 加 `_emit_intervene(kind, text)` helper(emit `CoordinatorIntervene(kind, text)`,复用 P1 events_v2;try/except 降级)+ `_recent_speaks(k)` / `_display(agent_id)` helper
- [X] T008 在 `apps/web-backend/app/orchestrator/harness.py` 给 `HarnessState` 加字段 `inflight_steps: int = 0` / `bootstrapped: bool = False` / `observer: "CoordinatorObserver | None" = None` + `start_observer(workers, goal)` / `stop_observer()` 方法(仅 is_v2 构造/启停,详 data-model §5)
- [X] T009 在 `harness.py` 加 `_derive_goal(run)` helper(从 `TaskRun` report_type/audience/raw_text 摘要拼原始目标字符串,drift + gate_reject 文案用)+ `_bootstrap_first_step(state, workers, first_agent)`(emit task.start + emit_v2 bootstrap AgentSpeak(mentions=[first_agent]);`bootstrapped` flag 去重,FR-005/006)
- [X] T010 在 `harness.py` `run_harness()` 加 is_v2 分支骨架:`is_v2` 时调 `state.start_observer(workers, _derive_goal(run))` + `_bootstrap_first_step(...)`(替代 line 764 chain 起点);末尾 `await state.stop_observer()`;**else 分支(v1)保留 line 764 chain 起点原样**。observer 的 `_on_quiescence` 仍是占位,本 task 只接线

**Checkpoint**: `coordinator_observer.py` 类型 + watchdog 框架 + HarnessState 字段就位;v2 任务能 bootstrap 起点 + observer 空转;v1 路径零开销。可启动 US1~US5。

---

## Phase 3: User Story 1 — 现有 v1 用户感受零变化（Priority: P1）🎯 MVP 红线

**Goal**: v1 任务的 Coordinator chain 路由、起点派单、必经步骤保护、失败兜底与含 P1+P2 的 main 字段级一致;P3 的 work-driver / observer / gatekeeper 在 v1 路径完全短路。

**Independent Test**: main vs P3 分支跑同 demo(不传 harness_version)→ diff `events.jsonl`/`script.md`/`task.json` 字段级相同;grep 无 P3 新事件。

### Tests for US1（必写 — SC-001 验收依赖）

- [X] T011 [P] [US1] 在 `apps/web-backend/tests/orchestrator/test_v1_regression.py` 扩 case:v1 `run_harness` 跑完,断言 `state.observer is None` 且 `state.inflight_steps == 0` 且 `state.bootstrapped == False`
- [X] T012 [P] [US1] 在 `test_v1_regression.py` 扩 case:v1 路径 `Coordinator.on_handoff` **不** short-circuit(仍 chain 路由)—— 断言 v1 任务的 chain 推进与 P2 baseline 一致(用 spy 或断言 on_handoff 实际 dispatch 了 worker.run)
- [X] T013 [P] [US1] 在 `test_v1_regression.py` 扩 case:v1 路径 events.jsonl 无 P3 新行为痕迹(grep 无 `kind=stagnation/drift/gate_pass/gate_reject` 的 coordinator.intervene;且无 bootstrap AgentSpeak)

### Implementation for US1

- [X] T014 [US1] 在 `harness.py` `Coordinator.on_handoff` / `on_failed` / `on_needs_help` / `on_needs_retry` 各加第一行 `if self.state.is_v2: return`(short-circuit;v1 路径完全不动,详 data-model §7);通过 T012 验证
- [X] T015 [US1] 在 `harness.py` 确认 `run_harness` 的 v1 分支(is_v2=False)不构造 observer、不 bootstrap、走 line 764 chain 起点;通过 T011 验证
- [X] T016 [US1] 运行 `pytest test_v1_regression.py` + P1/P2 全量 61 case,确认 v1 字段级零回归(SC-001 测试级守护)

**Checkpoint**: v1 任务行为与含 P1+P2 的 main 字段级相同。MVP 红线达成。

---

## Phase 4: User Story 2 — v2 任务由 subscription 链式驱动闭环（Priority: P2）

**Goal**: v2 任务起点 bootstrap 触发资料员,此后被点名且依赖就绪的 agent 真跑 step 产出产物,链式推进到 8 step 闭环;全程 Coordinator 不 chain 路由。

**Independent Test**: ScriptedBackend 喂 8 step 输出 → 校验 8 step 全由 subscription 触发产出、task done、`_resolve_target` 0 次调用。

### Tests for US2（必写 — FR-025/026 + SC-002/003 验收依赖）

- [X] T017 [P] [US2] 创建 `apps/web-backend/tests/orchestrator/test_v2_workdriver.py`,加 case:`handle_v2_event` SPEAK 分支真跑 `_run_unlocked`(断言 step 产出 artifact + `inflight_steps` 在跑时 +1 跑完归 0),而非 emit confirm 气泡
- [X] T018 [P] [US2] 在 `test_v2_workdriver.py` 加 case:`_bootstrap_first_step` 触发 material 第一棒(material 被 enqueue + SPEAK + 跑 step),不经 Coordinator chain handoff
- [X] T019 [P] [US2] 在 `test_v2_workdriver.py` 加 case:v2 路径 `Coordinator.on_handoff` short-circuit(被 emit 的 agent.handoff 不 dispatch worker.run,用 spy 断言 0 次 chain dispatch)
- [X] T020 [US2] 在 `test_v2_workdriver.py` 加端到端 case:`ScriptedBackend` 喂 8 step 脚本 → `run_harness(is_v2=True)` → 断言 8 step 全由 subscription 链式驱动产出、`result["reason"]=="done"`、backend.i==8(SC-002)

### Implementation for US2

- [X] T021 [US2] 在 `harness.py` `AgentWorker.handle_v2_event` 改 SPEAK 分支(line ~367):从 emit confirm AgentSpeak 改为 `inflight_steps += 1` → `await self._run_unlocked()` → `finally inflight_steps -= 1`(lock 已持有,详 data-model §6);SILENT/IGNORE/锁超时分支保留 P2 行为
- [X] T022 [US2] 在 `harness.py` 确认 `_run_unlocked` 末尾的 `_emit_v2_step_overlay`(P2 已落地)在 work-driver 路径正常 emit `AgentSpeak(mentions=[下一棒])`,驱动下游订阅唤醒(链式闭环,research §3);如需小调适配 v2 驱动则就地改
- [X] T023 [US2] 在 `harness.py` 落实 `_bootstrap_first_step`(T009 已建骨架)真正 emit_v2 bootstrap AgentSpeak 并经 dispatch 唤醒 material;通过 T018 验证
- [X] T024 [US2] 在 `harness.py` 落实 `Coordinator.on_handoff` 的 is_v2 short-circuit(T014 已加),确认 v2 路径 worker 跑完 emit 的 agent.handoff 不触发 chain dispatch;通过 T019 验证
- [X] T025 [US2] 在 `apps/web-backend/app/orchestrator/pipeline.py` 调整 v2 收尾:`_emit_v2_finalization` 在 v2 路径收窄(gatekeeper 接管收尾后避免与 gate_pass/reject 重复 emit,research F3);保留 v1 行为
- [X] T026 [US2] 运行 `pytest test_v2_workdriver.py`,确认 work-driver 转换 + bootstrap + 链式闭环全绿(SC-002/003)

**Checkpoint**: v2 任务由 subscription 链式驱动闭环,Coordinator 不 chain 派单。US2 可独立验证(配合 US5 gatekeeper 才能真收尾 done;本阶段可先用"8 step 跑完 + 手动断言 artifact 齐"验证驱动闭环)。

---

## Phase 5: User Story 3 — 链卡住时 Coordinator 守住 liveness（Priority: P2）

**Goal**: v2 链式推进卡住(全员 silent / 链断)时,observer 检测 quiescence,emit `intervene(kind=stagnation)` 激活"依赖就绪却静默"的 worker;无解则有限次兜底进入收尾,不死锁。

**Independent Test**: 构造死锁 → observer 检测 → 激活就绪静默 worker → recover;无解 → 转收尾。

### Tests for US3（必写 — FR-025 + SC-004 验收依赖）

- [X] T027 [P] [US3] 创建 `apps/web-backend/tests/orchestrator/test_observer.py`,加 case:`_is_quiescent` 双条件(inbox 全空 + inflight==0 + bootstrapped + 未完成)真值表 —— 有 inflight 或 inbox 非空时返回 False
- [X] T028 [P] [US3] 在 `test_observer.py` 加 case:死锁场景(worker 全 silent,某 artifact 缺但有"依赖就绪却没产出"的 worker)→ observer `_on_quiescence` emit `intervene(kind=stagnation)` 并重新 enqueue 激活该 worker
- [X] T029 [P] [US3] 在 `test_observer.py` 加 case:stagnation 激活**不指定 next-speaker**(只激活依赖满足却静默者;断言激活集合 = 满足 requires 且未产出自己 artifact 的 worker,FR-011)
- [X] T030 [P] [US3] 在 `test_observer.py` 加 case:stagnation 反复无解(无可激活 worker)达 `STAGNATION_MAX_RETRY` → 进入收尾(set done partial),不无限 intervene(FR-012/SC-004)

### Implementation for US3

- [X] T031 [US3] 在 `coordinator_observer.py` 实现 `_on_quiescence` 的 stagnation 部分:`_activate_ready_silent_workers()`(找 requires 全满足但本任务未产出自己 artifact 的 worker → 给它 enqueue 一条 stagnation 触发事件 + emit `intervene(kind=stagnation)`)+ `_stagnation_retries` 累计逻辑(详 data-model §4)
- [X] T032 [US3] 在 `coordinator_observer.py` 实现 `_is_quiescent`(T006 骨架)的完整双条件判定;接通 `_loop` 周期调用
- [X] T033 [US3] 运行 `pytest test_observer.py`(stagnation 部分),确认死锁检测 + 激活 + 无解兜底全绿(SC-004)

**Checkpoint**: v2 链卡住时 observer 守住 liveness,任务不死锁。US3 可独立验证。

---

## Phase 6: User Story 5 — Coordinator 收尾把关产物完整性（gatekeeper）（Priority: P3,不依赖宪章,先于 US4）

**Goal**: v2 链式自然终止或 stagnation 无解时,gatekeeper 校验 4 核心 artifact 完整性:齐 → gate_pass + done;缺 → gate_reject + partial + 点名缺失上游。

**Independent Test**: artifact 齐 → gate_pass+done;缺 → gate_reject+partial+点名。

### Tests for US5（必写 — FR-025 + SC-006 验收依赖）

- [X] T034 [P] [US5] 在 `test_observer.py` 加 case:`ArtifactGate.check` —— 4 核心 artifact 都有 latest≥1 → passed;缺某个 → `missing` 含该 id
- [X] T035 [P] [US5] 在 `test_observer.py` 加 case:quiescence + artifact 齐 → observer emit `intervene(kind=gate_pass)` + `state.done` set "done"
- [X] T036 [P] [US5] 在 `test_observer.py` 加 case:quiescence + 缺失 + 无可激活 worker(无解)→ emit `intervene(kind=gate_reject)` 点名缺失上游(业务化中文)+ `state.done` set "partial"
- [X] T037 [P] [US5] 在 `test_observer.py` 加 case:任意收尾路径都给出确定终止状态码(done/partial),`state.done` 必被 set,无挂起(FR-020/SC-006)

### Implementation for US5

- [X] T038 [US5] 在 `coordinator_observer.py` 实现 `_on_quiescence` 的 gatekeeper 部分:调 `ArtifactGate.check` → 齐 emit `gate_pass` + `state.done.set_result("done")`;无解 emit `gate_reject` 点名缺失(`_display` 翻中文)+ `state.done.set_result("partial")`(详 data-model §4/§7);与 T031 stagnation 部分合成完整 `_on_quiescence`
- [X] T039 [US5] 运行 `pytest test_observer.py`(gatekeeper 部分)+ 回头补跑 T020 端到端(此时 gatekeeper 接管收尾,8 step 闭环应真 done),确认 gate 齐/缺两路 + 端到端 done 全绿(SC-006)

**Checkpoint**: gatekeeper 收尾把关,v2 任务有确定终止状态。US2+US3+US5 合起来 = v2 路径完整闭环(work-driver + liveness + 收尾),**不依赖宪章修订即可交付**。

---

## Phase 7: User Story 4 — Coordinator 跑题纠偏（drift）（Priority: P3）⚠️ Phase 0 宪章修订阻塞

**Goal**: observer 周期判断讨论是否偏离原始目标,跑题则 emit `intervene(kind=drift)` 复诵目标。只发声、不路由、不审质量、不改产物。

**⚠️ BLOCKING 前置**: T040(宪章修订)**必须先完成**;在宪章放宽"Coordinator 纯规则引擎"之前,drift 真实现(LLMDriftJudge)+ `_check_drift` 接通不得落地。注入抽象(T004 的 DriftJudge/NoDriftJudge)已在 Foundational 落地且不违宪。

**Independent Test**: 注入 mock DriftJudge → drifted→intervene / not→silent / crash→降级 / 不路由。

### Phase 0 前置（宪章修订,阻塞本 user story）

- [X] T040 [US4] 走 `/speckit-constitution` 把 `.specify/memory/constitution.md` 原则 IV / 关联 §9.4.7 决策 1 的"Coordinator 是**纯**规则引擎"放宽为"observer 的 drift 判断允许一次受限 LLM 调用(只发声/不路由/不审质量/不改产物/minimal context)";版本 1.0.0 → 1.1.0,commit 标 `constitution: 1.0.0 → 1.1.0` + 理由。**此 task 不完成,T041-T046 全部阻塞**

### Tests for US4（必写 — FR-025 + SC-005 验收依赖;依赖 T040）

- [X] T041 [P] [US4] 创建 `apps/web-backend/tests/orchestrator/test_drift.py`,用 `mock_drift` fixture 加 case:drifted=True → observer `_check_drift` emit `intervene(kind=drift)` 复诵文案
- [X] T042 [P] [US4] 在 `test_drift.py` 加 case:drifted=False → 不 emit drift 事件(链式推进不受打扰)
- [X] T043 [P] [US4] 在 `test_drift.py` 加 case:注入一个 `judge` 抛异常的 stub → `_check_drift` 仅 log warn,任务不挂、不 failed(FR-017 降级)
- [X] T044 [P] [US4] 在 `test_drift.py` 加 case:drift intervene **不路由 next-speaker、不改产物**(断言 emit 的 CoordinatorIntervene 无 mentions 语义、不触发 worker.run、artifact 版本不变,FR-016)

### Implementation for US4（依赖 T040）

- [X] T045 [US4] 在 `coordinator_observer.py` 接通 `_check_drift`(T006 占位):取 `_recent_speaks(DRIFT_RECENT_K)` + `goal` → `await self.drift_judge.judge(...)` → drifted 则 `_emit_intervene("drift", restate_text)`;try/except 降级(FR-017);`_loop` 中每 `DRIFT_EVERY_N_TICK` 拍调一次
- [X] T046 [US4] 运行 `pytest test_drift.py`,确认 drift 4 分支(drifted/not/crash/不路由)全绿(SC-005);确认默认 `NoDriftJudge` 下 drift 永不触发(不破坏 US2/US3/US5 测试)

**Checkpoint**: drift 纠偏可用(mock 注入测试级);真 LLM 判断(LLMDriftJudge)随 Windows issue 解决后补。US4 在 T040 宪章修订后可独立验证。

---

## Phase 8: Polish & Cross-Cutting（横切验收）

**Purpose**: 跨 user story 的红线自检 + 文档收尾。建议在 US1~US5 都 checkpoint 通过后串行执行。

- [X] T047 [P] grep 红线审计:`grep -rn 'stagnation\|drift\|gatekeeper\|bootstrap\|quiescence\|inflight\|_resolve_target' apps/web-backend/app/api/ apps/web-frontend/ apps/admin-frontend/`(src)应 0 命中(FR-023 + SC-008)
- [X] T048 [P] `pnpm --filter web-frontend build` + `pnpm --filter admin-frontend build` 全绿(P3 不动 UI,防御性 — SC-009)
- [X] T049 [P] 运行 `apps/web-backend` 全量 pytest,确认 P1(32)+ P2(29)+ P3(新增 US1~US5)全绿,0 回归
- [ ] T050 v1 baseline diff(SC-001,需真 LLM 管线):main vs 003 分支跑 5 个 v1 demo → diff events.jsonl/script.md/task.json 字段级相同。**挂 Windows issue;环境可跑时人工执行**(同 P2 T038 性质)
- [ ] T051 [P] v2 真 LLM 闭环验收(SC-002 真任务版):harness_version=v2 demo 跑通 8 agent 链式闭环 + observer 收尾。**挂 Windows issue;环境可跑时人工执行**(同 P2 T040 性质;ScriptedBackend 测试级已由 T020/T039 覆盖)
- [X] T052 在 `CLAUDE.md` SPECKIT 块把 P3 状态从 (planning) 改为 ✅ Implemented;补 `coordinator_observer.py` 代码摘要 + 测试统计;`docs/开发文档.md` §9.4.5 P3 行标已落地

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无前置
- **Foundational (Phase 2)**: 依赖 Setup — **阻塞** US1~US5
- **US1 (Phase 3)**: 依赖 Foundational;与其他 US 独立(纯 v1 守护)
- **US2 (Phase 4)**: 依赖 Foundational;T025 pipeline 收窄需 US5 gatekeeper 落地后才能真闭环(T020 端到端在 T039 回补)
- **US3 (Phase 5)**: 依赖 Foundational;T031 stagnation 与 US5 gatekeeper 同在 `_on_quiescence`(T038 合成)
- **US5 (Phase 6)**: 依赖 Foundational + US3(共享 `_on_quiescence`,T038 合成 T031+gate);**不依赖宪章**
- **US4 (Phase 7)**: 依赖 Foundational + **T040 宪章修订(BLOCKING)**;US4 与 US1/2/3/5 独立
- **Polish (Phase 8)**: 依赖 US1~US5 全 checkpoint

### User Story Dependencies

- **US1 (P1)**: 独立可验证(v1 守护,不依赖任何 v2 实现)
- **US2 (P2)**: work-driver 闭环;真 done 收尾需 US5 gatekeeper(T039 回补端到端)
- **US3 (P2)** + **US5 (P3)**: 在 `_on_quiescence` 是协同关系(stagnation 激活 + gate 收尾合一个方法);实现顺序 US3 → US5(T031 先,T038 合成)
- **US4 (P3)**: 被 T040 宪章修订阻塞;其余 US 不阻塞

### 关键路径

```
Setup → Foundational → US1(MVP 红线)
                     → US2(work-driver)→ US3(stagnation)→ US5(gatekeeper)→ [v2 完整闭环,不依赖宪章]
                     → T040 宪章修订 → US4(drift)
                     → Polish
```

### Parallel Opportunities

- Foundational T004/T005/T007(coordinator_observer.py 不同小节)可并行
- US1 测试 T011-T013 [P] 可并行
- US2 测试 T017-T019 [P] 可并行(T020 端到端需实现后)
- US3 测试 T027-T030 [P]、US5 测试 T034-T037 [P] 可并行
- US4 测试 T041-T044 [P] 可并行(需 T040 宪章 + T045 实现后跑)
- Polish T047/T048/T049 可并行;T050/T051 挂 Windows issue 人工
- **US4 可与 US1/2/3/5 并行开发**(不同文件 + 独立),但 T040 宪章修订须先做

---

## Parallel Example: User Story 3 + User Story 5（共享 _on_quiescence）

```bash
# US3 + US5 测试(不同 case,同文件 test_observer.py,可同写):
Task: "quiescence 真值表 in test_observer.py"
Task: "stagnation 激活就绪静默 worker in test_observer.py"
Task: "ArtifactGate.check 齐/缺 in test_observer.py"
Task: "gate_pass→done / gate_reject→partial in test_observer.py"

# 实现(顺序:stagnation → gatekeeper 合成 _on_quiescence):
Task: "_activate_ready_silent_workers + stagnation in coordinator_observer.py"
Task: "ArtifactGate gatekeeper 合成 _on_quiescence in coordinator_observer.py"
```

---

## Implementation Strategy

### MVP First（US1 优先 — 红线保护）

1. Phase 1 Setup → Phase 2 Foundational → Phase 3 US1
2. **STOP & VALIDATE**:v1 demo 字段级零回归 → MVP 红线达成
3. 此时整个 P3 可 hold 在分支,main 0 风险

### Incremental Delivery（推荐）

1. Setup + Foundational → 基础就位
2. US1 → v1 零回归 → "P3 不破坏 v1"
3. US2 → work-driver 转换 → "subscription 真驱动 step"
4. US3 → stagnation → "卡住能被推"
5. US5 → gatekeeper → "v2 完整闭环 + 确定收尾"(**到此不依赖宪章即可交付一个可用的 v2 自驱闭环**)
6. T040 宪章修订 → US4 drift → "跑题纠偏"
7. Polish → grep / build / 文档

### 宪章解耦策略

US1~US3 + US5 构成"v2 subscription 自驱闭环"的完整最小集,**全程不碰宪章**。可先交付这一段(甚至先合 PR),drift(US4)作为宪章修订后的增量,不阻塞主体落地。

---

## Notes

- **[P] 任务** = 不同文件且无依赖
- **[US1]~[US5] 标签**:仅 user story 阶段带;Setup/Foundational/Polish 不带
- **测试要求**:FR-025/026 明确;TDD 顺序(测试先于实现,断言初始失败)
- **红线**:用户可见层不得出现 `stagnation/drift/gatekeeper/bootstrap/quiescence/inflight/_resolve_target`(FR-023 + Polish T047 兜底)
- **Coordinator class 仅加 4 行 short-circuit**(T014),内部逻辑不动;Reviewer 不动(P4)
- **drift 真实现(LLMDriftJudge)本期不交付**:Phase 0 宪章 + Windows issue 双前置;本期交付 NoDriftJudge + 接口 + mock 测试
- **T050/T051 真 LLM 验收挂 Windows issue**(同 P2 T038/T040 性质);ScriptedBackend 测试级闭环由 T020/T039 覆盖
- 每完成一个 checkpoint 建议 commit

---

## Validation Checklist

- ✅ 所有任务符合 `- [ ] [TaskID] [P?] [Story?] Description with file path` 格式
- ✅ 5 个 user story 各自独立可测试可验收(US1 SC-001 / US2 SC-002 / US3 SC-004 / US4 SC-005 / US5 SC-006)
- ✅ Foundational 完成前任何 US 不开工
- ✅ 测试任务先于实现任务(每个 US 内)
- ✅ 文件路径精确到模块级
- ✅ **US4(drift)的 Phase 0 宪章修订(T040)显式列为 BLOCKING 前置;US1/2/3/5 不阻塞,排在 US4 之前**
- ✅ 横切 Polish 在所有 US checkpoint 后
