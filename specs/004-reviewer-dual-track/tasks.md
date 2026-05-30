---
description: "P4 任务清单 — Reviewer 双轨 + verdict.fail 修复闭环"
---

# Tasks: Reviewer 双轨(质量 + 流程逻辑)+ verdict.fail 修复闭环（P4）

**Input**: Design documents from `/specs/004-reviewer-dual-track/`

**Prerequisites**: plan.md ✅ · spec.md ✅ · research.md ✅ · data-model.md ✅ · quickstart.md ✅（contracts/ skipped — 复用 P1 ReviewerVerdict）

**Tests**: 包含(FR-021/022;SC-006 验收依赖)。

**Organization**: 按 spec.md 5 个 user story 优先级排(US1 P1 / US2 P2 / US3 P2 / US4 P2 / US5 P3)。**无需宪章修订**(P4 不阻塞)。

## Format

`- [ ] [TaskID] [P?] [Story?] Description with file path`

## Path Conventions

monorepo 后端单一改动点:`apps/web-backend/`。前端零改动。

```text
apps/web-backend/
├── app/orchestrator/
│   ├── process_review.py        # 新建(~130 行)
│   ├── harness.py               # 扩 ~90 行
│   ├── coordinator_observer.py  # 扩 ~70 行
│   ├── pipeline.py              # 小调
│   ├── events_v2.py / artifacts_v2.py  # 不动(P1)
└── tests/orchestrator/
    ├── test_reviewer_quality.py / test_process_review.py / test_fix_cycle.py /
    ├── test_reviewer_e2e.py     # 新建
    ├── test_v1_regression.py    # 扩
    └── conftest.py              # 复用 mock_backend
```

---

## Phase 1: Setup（共享基础）

- [X] T001 确认工作分支 `004-reviewer-dual-track` 基于含 P1+P2+P3 的 main;`pytest apps/web-backend/tests` 现有 81 case 全绿(P4 起点 baseline)
- [X] T002 [P] 在 `apps/web-backend/tests/orchestrator/conftest.py` 加 `_reviewer_turn(accept, comment, reason)` helper(构造 reviewer `_quick_review` 的 ScriptedBackend 脚本 dict),供质量轨测试复用

---

## Phase 2: Foundational（阻塞所有 user story）

**⚠️ CRITICAL**: 本阶段未完成前,US1~US5 都不能开工。

- [X] T003 创建 `apps/web-backend/app/orchestrator/process_review.py`:module 头 + `log` + `ProcessReviewResult` frozen dataclass(passed / findings / fix_targets)+ `ProcessReviewer` 类骨架(`check(task_id, events)` 框架,3 规则方法留占位)
- [X] T004 [P] 在 `process_review.py` 加 `_process_verdict(result, task_id) -> ReviewerVerdict`(ProcessReviewResult → ReviewerVerdict(dimension=process_logic);suggestions 凑 ≥3)
- [X] T005 在 `apps/web-backend/app/orchestrator/harness.py` 加 `_pad_suggestions(seed)`(凑 ≥3,满足 P1 schema)+ `_to_reviewer_verdict(qr, dimension, fix_agent, task_id)`(_quick_review dict → ReviewerVerdict 适配)
- [X] T006 在 `harness.py` `AgentWorker.__init__` 加 `self._reviewed: set[tuple[str,int]] = set()`(质量轨版本去重,仅 reviewer 用)
- [X] T007 在 `harness.py` `AgentWorker.handle_v2_event` 加 reviewer 特化早期分支骨架:`if self.agent_id=="reviewer" and self.state.is_v2: await self._reviewer_handle(event); return`;`_reviewer_handle` 暂分流(ArtifactUpdate→占位 / 否则→silent),`_reviewer_quality_review` 占位(US2 补)
- [X] T008 在 `apps/web-backend/app/orchestrator/coordinator_observer.py` 加 `REVIEW_FIX_MAX_RETRY` env 常量(默认 2)+ `CoordinatorObserver._fix_retries: dict[str,int]` / `_process_reviewed: bool` 字段 + `start()` 里 `bus.on("reviewer.verdict", self._on_verdict)`;`_on_verdict` 暂留 `pass`(US4 补)

**Checkpoint**: process_review.py 类型 + harness reviewer 特化骨架 + observer 字段/注册就位;v1 零开销。可启动 US1~US5。

---

## Phase 3: User Story 1 — 现有 v1 用户感受零变化（Priority: P1）🎯 MVP 红线

**Goal**: v1 即时质量门(REVIEW_GATES + `_quick_review` + fail 重做 1 次)与含 P1+P2+P3 的 main 字段级一致;P4 双轨/verdict/修复在 v1 完全短路。

**Independent Test**: main vs P4 分支跑同 demo(不传 harness_version)→ diff 字段级相同;v1 events 无 reviewer.verdict/needs_fix。

### Tests for US1

- [X] T009 [P] [US1] 在 `test_v1_regression.py` 扩 case:v1 路径 `state.observer is None` 后,reviewer worker(若构造)`_reviewed` 为空集;v1 任务 events.jsonl grep 无 `reviewer.verdict` / `needs_fix`
- [X] T010 [P] [US1] 在 `test_v1_regression.py` 扩 case:v1 路径 reviewer 不走 handle_v2_event 特化分支(handle_v2_event 仅 v2 调用;v1 reviewer 走 `_gate_review`)—— 断言 v1 `_gate_review`/REVIEW_GATES 行为与 main 一致(用现有 v1 baseline 断言)

### Implementation for US1

- [X] T011 [US1] 在 `harness.py` 确认 reviewer 特化分支守 `self.state.is_v2`(T007 已加),v1 路径不进;通过 T009/T010 验证
- [X] T012 [US1] 在 `pipeline.py` 确认 `_gate_review`/REVIEW_GATES/`_quick_review` v1 路径不被 P4 改动影响(质量轨复用 `_quick_review` 是 v2 新调用点,不改函数本身);通过 v1 baseline 验证
- [X] T013 [US1] 运行 `pytest test_v1_regression.py` + 全量 81 case,确认 v1 字段级零回归(SC-001 测试级守护)

**Checkpoint**: v1 行为与含 P1+P2+P3 的 main 字段级相同。MVP 红线达成。

---

## Phase 4: User Story 2 — 质量轨:artifact 产出即时审校（Priority: P2）

**Goal**: reviewer 订阅 artifact.update → 即时 `_quick_review` → emit `ReviewerVerdict(quality)`;版本去重;被 mention silent。

**Independent Test**: emit artifact.update → reviewer 即时 emit verdict(quality) pass/fail;同版本 0 重审。

### Tests for US2（FR-021 + SC-002）

- [X] T014 [P] [US2] 创建 `test_reviewer_quality.py`,加 case:ScriptedBackend 脚本化 reviewer `_quick_review` turn(accept=True)→ artifact.update 触发 → emit `ReviewerVerdict(dimension=quality, verdict=pass)`
- [X] T015 [P] [US2] 在 `test_reviewer_quality.py` 加 case:reviewer turn(accept=False)→ emit verdict(fail)带 `suggested_fix_agent=producer` + suggestions ≥3
- [X] T016 [P] [US2] 在 `test_reviewer_quality.py` 加 case:同一 (artifact_id, version) 二次 artifact.update → 不重审(_reviewed 去重,SC-002)
- [X] T017 [P] [US2] 在 `test_reviewer_quality.py` 加 case:reviewer 被 mention(非 artifact)→ emit AgentSilent,不跑 step(决策 3)

### Implementation for US2

- [X] T018 [US2] 在 `harness.py` 实现 `_reviewer_quality_review(event)`:`_reviewed` 版本去重 → `AGENT_TO_STEP[event.producer]` → `by_key[step]` → `_quick_review(step, run)` → `_to_reviewer_verdict(..., dimension="quality", fix_agent=event.producer)` → emit;lazy import pipeline(F1)
- [X] T019 [US2] 在 `harness.py` 完善 `_reviewer_handle`:ArtifactUpdate → `_reviewer_quality_review`;否则 → emit AgentSilent("持续审校中,收尾给结论")
- [X] T020 [US2] 运行 `pytest test_reviewer_quality.py`,确认质量轨即时审 + 去重 + silent 全绿(SC-002)

**Checkpoint**: 质量轨即时审校可用。US2 可独立验证。

---

## Phase 5: User Story 3 — 流程逻辑轨:收尾全局校验（Priority: P2）

**Goal**: observer quiescence 触发 ProcessReviewer 3 纯规则 → emit `ReviewerVerdict(process_logic)`。

**Independent Test**: 构造收尾态(版本不一致/依赖错/缺席)→ verdict(process_logic, fail) + findings;全正常 → pass。

### Tests for US3（FR-021 + SC-003）

- [X] T021 [P] [US3] 创建 `test_process_review.py`,加 case:版本一致规则 —— 某 artifact base_version 指向不存在上游版本 → fail + finding
- [X] T022 [P] [US3] 在 `test_process_review.py` 加 case:依赖图规则 —— events 里 Outline 早于 ReportCore → fail
- [X] T023 [P] [US3] 在 `test_process_review.py` 加 case:参与度规则 —— 缺某核心 artifact → fail + fix_targets 含缺失 producer
- [X] T024 [P] [US3] 在 `test_process_review.py` 加 case:4 artifact 齐 + 版本链一致 + 顺序对 → passed

### Implementation for US3

- [X] T025 [US3] 在 `process_review.py` 实现 `ProcessReviewer` 3 规则:`_check_version_consistency`(读 4 核心 artifact `__meta__.base_version`)/ `_check_dependency_order`(events artifact.update 时序)/ `_check_participation`(ArtifactGate + events);各 finding 带违例 artifact_id;fix_targets = `ARTIFACT_PRODUCER[id]`;降级 try/except(FR-019)
- [X] T026 [US3] 在 `coordinator_observer.py` `_on_quiescence` 加流程逻辑审触发(收尾一次,`_process_reviewed` flag):`ProcessReviewer().check(...)` → emit `_process_verdict(...)`(US5 再接综合决策)
- [X] T027 [US3] 运行 `pytest test_process_review.py`,确认 3 规则齐/缺全绿(SC-003)

**Checkpoint**: 流程逻辑轨收尾全局审可用。US3 可独立验证。

---

## Phase 6: User Story 4 — verdict.fail 触发修复闭环（Priority: P2）

**Goal**: observer 监听 reviewer.verdict(fail) → 转写 intervene 点名 + 重置 step needs_fix + force_run_v2 修复 + 上限。

**Independent Test**: emit verdict(fail, suggested_fix_agent=X)→ X 重置 needs_fix + force_run_v2;达上限停。

### Tests for US4（FR-021 + SC-004）

- [X] T028 [P] [US4] 创建 `test_fix_cycle.py`,加 case:emit ReviewerVerdict(fail, suggested_fix_agent=material)→ observer `_on_verdict` → material step.status=="needs_fix" + force_run_v2 被调度 + `_fix_retries[material]==1`
- [X] T029 [P] [US4] 在 `test_fix_cycle.py` 加 case:连续 fail 达 `REVIEW_FIX_MAX_RETRY`(monkeypatch=2)→ 第三次不再触发修复(FR-011)
- [X] T030 [P] [US4] 在 `test_fix_cycle.py` 加 case:verdict(fail, suggested_fix_agent=None/无效)→ 不触发修复(FR-013)
- [X] T031 [P] [US4] 在 `test_fix_cycle.py` 加 case:verdict(pass)→ `_on_verdict` 不触发任何修复

### Implementation for US4

- [X] T032 [US4] 在 `coordinator_observer.py` 实现 `_on_verdict(event)`(T008 占位):读 payload verdict/suggested_fix_agent;fail + fix_agent 有效 + 未达上限 → bump `_fix_retries` + `_emit_intervene("gate_reject", 转写点名)` + 重置 `by_key[step].status="needs_fix"` + `inflight_steps += 1` + `create_task(force_run_v2)`;try/except 降级(FR-019)
- [X] T033 [US4] 在 `harness.py` 确认 force_run_v2 修复后重产 artifact → 质量轨重审(闭环);work-driver step-success 去重被 needs_fix 重置解除
- [X] T034 [US4] 运行 `pytest test_fix_cycle.py`,确认转写/重置/重激活/上限/缺失不修全绿(SC-004)

**Checkpoint**: verdict.fail 修复闭环可用。US4 可独立验证。

---

## Phase 7: User Story 5 — 收尾决策纳入 verdict（Priority: P3）

**Goal**: observer `_on_quiescence` 双因子(artifact 完整性 + verdict)决定 done/partial。

**Independent Test**: 齐+全pass→done;有未解决 fail/缺产物→partial。

### Tests for US5（FR-021 + SC-005）

- [X] T035 [P] [US5] 创建 `test_reviewer_e2e.py`,加 case:产物齐 + 流程逻辑 pass + 无未解决 quality fail → `_on_quiescence` gate_pass + done
- [X] T036 [P] [US5] 在 `test_reviewer_e2e.py` 加 case:产物齐但有达上限的 quality fail → partial + 业务化说明
- [X] T037 [P] [US5] 在 `test_reviewer_e2e.py` 加端到端 case:ScriptedBackend 跑"artifact 产出 → 质量审 fail → 修复 → 重审 pass → 收尾 done"(SC-006 e2e)

### Implementation for US5

- [X] T038 [US5] 在 `coordinator_observer.py` `_on_quiescence` 完成双因子综合决策(data-model §6):流程逻辑审(②已 T026)后 → gate 完整性 + `unresolved = any(_fix_retries >= MAX)` → 齐+无未解决→gate_pass+done;否则 stagnation 激活/修复/gate_reject+partial(沿用 P3 兜底 + 上限)
- [X] T039 [US5] 运行 `pytest test_reviewer_e2e.py`,确认双因子收尾 + e2e 闭环全绿(SC-005/006)

**Checkpoint**: 收尾决策双因子。US1~US5 合起来 = Reviewer 双轨完整闭环。

---

## Phase 8: Polish & Cross-Cutting

- [X] T040 [P] grep 红线审计:`grep -rn 'process_logic\|verdict\|suggested_fix_agent\|needs_fix\|quiescence' apps/web-backend/app/api/ apps/web-frontend/ apps/admin-frontend/`(src)应 0 命中(FR-018 + SC-007)
- [X] T041 [P] `pnpm --filter web-frontend build` + `pnpm --filter admin-frontend build` 全绿(P4 不动 UI,防御性 — SC-008)
- [X] T042 [P] 运行 `apps/web-backend` 全量 pytest,确认 P1-P3(81)+ P4 新增全绿,0 回归
- [X] T043 在 `pipeline.py` 清理 `_emit_v2_finalization` 的 ReviewerVerdict 示例(F4;真双轨已替代),确认 P1 test_v2_integration 不依赖该示例(若依赖则保留)
- [ ] T044 v1 baseline diff(SC-001,需真 LLM 管线)+ v2 真 LLM 质量审验收(SC-002 真任务版):**挂 Windows issue;环境可跑时人工**(同 P3 T050/T051 性质;ScriptedBackend 测试级已覆盖)
- [X] T045 在 `CLAUDE.md` SPECKIT 块把 P4 状态从 (planning) 改为 ✅ Implemented;补 `process_review.py` 代码摘要 + 测试统计

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2,阻塞 US1~US5)** → US1~US5 → **Polish (Phase 8)**
- **US1 (P1)**: 独立(v1 守护)
- **US2 (P2)**: 质量轨;产 verdict(quality)给 US4 消费
- **US3 (P2)**: 流程逻辑轨;产 verdict(process_logic)给 US4/US5;T026 部分接 US5
- **US4 (P2)**: 修复闭环;依赖 US2/US3 的 verdict 来源(_on_verdict 消费)
- **US5 (P3)**: 收尾双因子;依赖 US3(流程逻辑审)+ US4(未解决 fail 判定)
- 实施顺序:US1 → US2 → US3 → US4 → US5(verdict 生产先于消费)

### Parallel Opportunities

- Foundational T004(独立)可与 T003 并行
- 各 US 测试任务 [P] 可并行(不同文件/case)
- Polish T040/T041/T042 可并行

---

## Implementation Strategy

### MVP First（US1）

Setup + Foundational + US1 → v1 字段级零回归 → MVP 红线。

### Incremental Delivery

1. Setup + Foundational
2. US1 → v1 零回归
3. US2 → 质量轨即时审(可见的早反馈)
4. US3 → 流程逻辑轨收尾审
5. US4 → verdict.fail 修复闭环(双轨真正"能改善产物")
6. US5 → 收尾双因子(完整 Reviewer 双轨)
7. Polish

---

## Notes

- **[P]** = 不同文件/case 无依赖;**[US1]~[US5]** 仅 user story 阶段带
- 测试要求 FR-021/022;TDD 顺序
- 红线:用户可见层不得出现 `process_logic/verdict/suggested_fix_agent/needs_fix/quiescence`(FR-018 + T040)
- Reviewer 不直接 @(observer 转写)/不重写产物(producer 自跑);质量 LLM 是本职,**无需宪章修订**
- T044 真 LLM 验收挂 Windows issue;ScriptedBackend 测试级由各 US 测试覆盖
- **无 Phase 0 宪章前置**(与 P3 不同):US1~US5 按优先级正常排

---

## Validation Checklist

- ✅ 所有任务符合 `- [ ] [TaskID] [P?] [Story?] Description with file path` 格式
- ✅ 5 个 user story 各自独立可测试可验收(US1 SC-001 / US2 SC-002 / US3 SC-003 / US4 SC-004 / US5 SC-005/006)
- ✅ Foundational 完成前任何 US 不开工
- ✅ 测试任务先于实现任务
- ✅ 文件路径精确到模块级
- ✅ 无宪章前置(P4 无需);验收基线 ScriptedBackend,真 LLM 挂 issue
