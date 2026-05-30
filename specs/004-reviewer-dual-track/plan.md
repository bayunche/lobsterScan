# Implementation Plan: Reviewer 双轨(质量 + 流程逻辑)+ verdict.fail 修复闭环（P4）

**Branch**: `004-reviewer-dual-track` | **Date**: 2026-05-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-reviewer-dual-track/spec.md`

---

## Summary

把 Reviewer 从"v1 即时质量门 + P3 链式终点 work-driver"重构为**全程订阅的双轨审校者**:
**质量轨**(reviewer 被 `artifact.update` 触发 → `handle_v2_event` 特化分支 → 复用 `_quick_review`
报告评审 → emit `ReviewerVerdict(quality)`;版本去重)+ **流程逻辑轨**(observer quiescence 触发新模块
`process_review.py` 的 3 纯规则:版本一致/依赖图/参与度 → emit `ReviewerVerdict(process_logic)`)。
`verdict.fail` → observer `bus.on("reviewer.verdict")` 监听 → Coordinator 转写 `intervene` 点名 +
重置 step `needs_fix`(解除 P3 work-driver 去重)+ `force_run_v2` 修复(`REVIEW_FIX_MAX_RETRY` 上限);
gatekeeper `_on_quiescence` 改造为双因子(artifact 完整性 + verdict)决定 `task.end`。v1 路径字段级零回归。

技术路线:
- **零新外部依赖**:复用 stdlib + P1 `ReviewerVerdict` schema(无新事件)+ P3 `force_run_v2`/observer
- **质量轨复用 `_quick_review`**(brainstorm 决策 B,不加抽象);ScriptedBackend 脚本化 reviewer turn 测试
- **流程逻辑轨纯规则**(决策 A,新模块 `process_review.py`,无 LLM,真测试)
- **无需新宪章修订**:Reviewer 用 LLM 做质量验证是原则 IV 本职(P3 的 1.1.0 已够)
- **验收基线 = ScriptedBackend 测试级**;质量轨真 LLM 挂 Windows issue(同 P3 T051)

---

## Technical Context

| 项 | 选择 |
|---|---|
| **Language/Version** | Python 3.11+ |
| **Primary Dependencies** | stdlib;复用 P1 `events_v2`(ReviewerVerdict/Finding)/`artifacts_v2` + P2 subscription + P3 `coordinator_observer`/`force_run_v2` + pipeline `_quick_review`/`AGENT_TO_STEP` |
| **Storage** | 无新存储;reviewer `_reviewed` / observer `_fix_retries`/`_process_reviewed` 在任务级内存 |
| **Testing** | pytest + pytest-asyncio;复用 `ScriptedBackend`/`mock_backend`;新增 `process_review` 纯规则单测 |
| **Target Platform** | Linux server(含 WSL2 dev)。**质量轨真 LLM(report-reviewer subprocess)在 Windows dev 受阻**(同 P3) |
| **Project Type** | web-service(`apps/web-backend`,monorepo 单后端) |
| **Performance Goals** | v1 路径 0 性能回归(baseline = P3 之后 main);质量轨每核心 artifact 一次 LLM(4 次,版本去重避免重复);流程逻辑轨纯规则 < 5ms |
| **Constraints** | 100% 向后兼容(v1 + P1-P3 v2 现有行为保留);100% 无外部新依赖;Reviewer 不直接 @/不重写产物(宪章 IV);无需新宪章修订 |
| **Scale/Scope** | 新增 `process_review.py` ~130 行;扩展 `harness.py` ~90 行(reviewer 特化 + 适配);扩展 `coordinator_observer.py` ~70 行(_on_verdict + _on_quiescence 改造);新增单测 ~350 行 |

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 是否通过 | 证据 |
|---|---|---|
| **I. 用户可见层脱敏** | ✅ PASS | FR-018 禁 `process_logic/verdict/suggested_fix_agent/needs_fix/quiescence` 出现在用户可见层;verdict/intervene 经翻译层渲染中文;SC-007 grep 兜底 |
| **II. 中文为产品语言** | ✅ PASS | P4 不动 UI;verdict 转写的 intervene 文案业务化中文("X 那块再打磨一下") |
| **III. 降级而非崩溃** | ✅ PASS | FR-019:质量审/流程审/修复/收尾全 try/except 降级;`_quick_review` 本就异常放行;收尾必给确定状态码 |
| **IV. Coordinator / Reviewer 边界** | ✅ PASS | Reviewer 只出 verdict + 建议(FR-020),**不直接 @**(verdict.fail 由 observer/Coordinator 转写 intervene,FR-009),**不重写产物**(修复由 producer 自跑 force_run_v2)。Reviewer 用 LLM 做**质量验证**是原则 IV 明列的本职 —— **无需新宪章修订**(P3 的 1.1.0 已够)。流程逻辑轨纯规则不引 LLM |
| **V. Agent 自治与隔离** | ✅ PASS | 修复走 `force_run_v2` → per-agent lock(P3);agentDir 不共享不变 |

**Gate 结论**:**通过 0 violation**。与 P3 不同,P4 **无需宪章修订** —— Reviewer 的质量 LLM 审是其固有职责,原则 IV 本就允许(P3 放宽的是 *Coordinator* 用 LLM,与 Reviewer 无关)。

Complexity Tracking:N/A(无 violation)。

---

## Project Structure

### Documentation (this feature)

```text
specs/004-reviewer-dual-track/
├── plan.md              # 本文件
├── spec.md              # /speckit-specify 已就位
├── research.md          # Phase 0(本次,9 决策 + 4 派生发现)
├── data-model.md        # Phase 1(本次)
├── quickstart.md        # Phase 1(本次)
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks 生成(本命令不产)
```

**contracts/ 跳过** —— P4 内部架构,复用 P1 `ReviewerVerdict` schema,无新对外 API/事件。

### Source Code (repository root)

只动 `apps/web-backend/`,前端零改动。

```text
apps/web-backend/
├── app/orchestrator/
│   ├── process_review.py        # 【新增】流程逻辑轨 3 纯规则 + ProcessReviewer + _process_verdict (~130 行)
│   ├── harness.py               # 扩 ~90 行：
│   │                            #   - AgentWorker._reviewed(质量轨版本去重)
│   │                            #   - handle_v2_event reviewer 特化早期分支
│   │                            #   - _reviewer_handle / _reviewer_quality_review
│   │                            #   - _to_reviewer_verdict / _pad_suggestions 适配
│   ├── coordinator_observer.py  # 扩 ~70 行：
│   │                            #   - REVIEW_FIX_MAX_RETRY + _fix_retries + _process_reviewed
│   │                            #   - _on_verdict(监听 reviewer.verdict(fail)→ 修复闭环)
│   │                            #   - _on_quiescence 改造(流程逻辑审 + 双因子决策)
│   ├── pipeline.py              # 小调：_quick_review/AGENT_TO_STEP 被质量轨复用;_emit_v2_finalization 示例清理
│   ├── events_v2.py             # 不动(P1;复用 ReviewerVerdict/Finding)
│   └── artifacts_v2.py          # 不动(P1;ProcessReviewer 读 __meta__)
└── tests/orchestrator/
    ├── test_reviewer_quality.py # 【新增】质量轨即时审 (~80 行)
    ├── test_process_review.py   # 【新增】流程逻辑轨 3 规则 (~90 行)
    ├── test_fix_cycle.py        # 【新增】verdict.fail 修复闭环 (~80 行)
    ├── test_reviewer_e2e.py     # 【新增】收尾双因子决策 e2e (~70 行)
    ├── test_v1_regression.py    # 扩 ~30 行：v1 无 reviewer.verdict/needs_fix
    └── conftest.py              # 复用(mock_backend 脚本化 reviewer turn)
```

**Structure Decision**: 流程逻辑轨独立 `process_review.py`(纯规则,像 P3 `ArtifactGate`,聚拢 3 检查便于测试);质量轨复用 `_quick_review`(决策 B,不加抽象,reviewer 特化分支在 harness);修复闭环并入 observer(已是 Coordinator 转写 + force_run_v2 载体)。

---

## Complexity Tracking

无 violation,本段不填。

---

## Phase 0 Output

详见 [research.md](./research.md) —— **9 项决策 + 4 派生发现**:

| # | 决策 | 选择 |
|---|---|---|
| 0 | reviewer 全程审校载体 | handle_v2_event reviewer 特化分支(不走 work-driver) |
| 1 | 质量轨审 artifact | ArtifactUpdate.producer → AGENT_TO_STEP → _quick_review |
| 2 | _quick_review → verdict 映射 | accept→verdict;suggestions 凑 ≥3(满足 schema) |
| 3 | reviewer 被 mention | silent(不跑 step) |
| 4 | 质量轨版本去重 | _reviewed: set[(id,version)] |
| 5 | 流程逻辑轨 | process_review.py ProcessReviewer 3 纯规则 |
| 6 | verdict.fail 修复 | observer bus.on(reviewer.verdict)→ 转写 + 重置 needs_fix + force_run_v2 |
| 7 | _on_quiescence 改造 | 流程逻辑审 + 双因子(完整性 + verdict)决策 |
| 8 | v1 零回归 | 全 is_v2 守卫 |

## Phase 1 Output

- **数据模型**:[data-model.md](./data-model.md) —— ProcessReviewer/ProcessReviewResult + reviewer 特化 + observer 扩展 + 状态转移
- **快速上手**:[quickstart.md](./quickstart.md) —— 双轨流程图 / ScriptedBackend 质量轨 / 流程逻辑轨单测 / 修复闭环 / 调试 / 红线

## Post-Design Constitution Re-Check

| 原则 | Post-Design 结论 |
|---|---|
| I 脱敏 | ✅ 新字段(_reviewed/_fix_retries/_process_reviewed)+ ProcessReviewer 全内部;verdict/intervene 文案中文 |
| II 中文 | ✅ 转写 intervene + verdict suggestions 业务化中文 |
| III 降级 | ✅ reviewer 审/流程审/修复/收尾全 try/except;_quick_review 异常放行 |
| IV 边界 | ✅ Reviewer 只出 verdict + 建议;不直接 @(observer 转写);不重写产物(producer 自跑);质量 LLM 是本职;**无需宪章修订** |
| V 隔离 | ✅ 修复走 force_run_v2 → per-agent lock |

**Re-check 结论**:通过 0 violation。可进 `/speckit-tasks`。

**提示**:`/speckit-tasks` 拆解时,US1-US5 均不依赖宪章修订(P4 无需),可按优先级正常排;质量轨真 LLM 验收 + v1 baseline diff 挂 Windows issue(同 P3)。
