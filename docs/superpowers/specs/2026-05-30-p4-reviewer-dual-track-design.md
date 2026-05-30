# P4 设计 — Reviewer 双轨(质量 + 流程逻辑)+ verdict.fail 修复闭环

**日期**: 2026-05-30 · **阶段**: P4(v2 群聊化路线图,见 `docs/开发文档.md` §9.4.5)
**前序**: P1(协议+状态)· P2(订阅化)· P3(Coordinator 转型 + work-driver),均已合 main
**产出去向**: 本 design doc → `/speckit-specify` 产出 `specs/004-reviewer-dual-track/spec.md`

---

## 1. 背景与目标

现状(P3 之后):
- v1 即时质量门 `_gate_review`(REVIEW_GATES = material/point/upward-opt/copywriting 4 step 后跑)
  → `_quick_review`(report-reviewer LLM)→ fail 重做 1 次。
- `ReviewerVerdict` schema P1 已定义;P3 `_emit_v2_finalization` 有**示例** emit(标注"P4 才落真双轨")。
- P3 gatekeeper 已做 artifact **完整性**校验(齐不齐)。

P4 落地真正的 **Reviewer 双轨** + verdict 真实 emit + verdict.fail 触发修复:

> Reviewer 从"v1 即时质量门 + P3 链式终点 work-driver"重构为**全程订阅的双轨审校者**:
> **质量轨**(订阅 artifact.update → 即时 report-reviewer LLM → ReviewerVerdict(quality))
> + **流程逻辑轨**(observer 收尾触发 3 项纯规则 → ReviewerVerdict(process_logic));
> verdict.fail → Coordinator 转写 → 重置 step + 重新激活修复(有限次);
> gatekeeper 收尾综合 [完整性 + verdict] 决定 task.end。

里程碑(路线图原文):*Reviewer 能拒绝 task.end 并触发修复*。

### 验收基线(brainstorm 决策)

**ScriptedBackend 测试级闭环**(延续 P3)。流程逻辑轨纯规则可完整真测试;质量轨真 LLM
(report-reviewer subprocess)受 Windows issue 阻塞(`docs/issues/windows-real-pipeline-runnability.md`),
测试通过 ScriptedBackend 脚本化 reviewer turn 覆盖编排,真 LLM 质量审验收挂 issue(同 P3 T051)。

---

## 2. Brainstorm 决策记录(5 项)

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 双轨落地深度 | **B — 质量轨直接接 report-reviewer / `_quick_review`(不加抽象);流程逻辑轨纯规则** |
| 2 | verdict.fail 修复闭环 | **A — 重置 step status `needs_fix` + 重新激活 + 有限次修复上限** |
| 3 | 两轨审校时机 | **A — 质量轨即时(订阅 artifact.update)+ 流程逻辑轨收尾(全局审)** |
| 4 | 与 gatekeeper 协作 | **A — reviewer 审(校验)→ gatekeeper 据 [完整性 + verdict] 决策** |
| 5 | 流程逻辑检查项 | **A — 版本一致 + 依赖图 + 参与度(3 项纯规则);跨引用一致归质量轨/P5** |

---

## 3. 架构 — 4 核心组件

```
① 质量轨(即时,per-artifact)
   reviewer 订阅 artifact.update(P2 WORKER_PROFILE 已订阅 4 核心 artifact)
     → handle_v2_event reviewer 特化:跑 _quick_review(report-reviewer LLM,复用 v1)
     → emit ReviewerVerdict(dimension=quality, pass/fail, suggested_fix_agent)
     → **不**跑 review step(reviewer 不再是链式 work-driver 一环)
     → 同一 artifact 版本不重审(version 去重)

② 流程逻辑轨(收尾,全局)
   observer quiescence → ProcessReviewer(3 纯规则):
     · 版本一致:4 核心 artifact base_version 链一致
     · 依赖图:产出顺序符合 MaterialPool→ReportCore→Outline→Script
     · 参与度:该参与的 agent 都在 events 留痕
     → emit ReviewerVerdict(dimension=process_logic, pass/fail, findings)

③ verdict.fail 修复闭环(Coordinator 转写,宪章 IV)
   observer 监听 ReviewerVerdict(fail)
     → 读 suggested_fix_agent → emit CoordinatorIntervene 点名(转写,reviewer 不直接 @)
     → 重置该 agent 的 step status = "needs_fix"(解除 P3 work-driver 去重)
     → force_run_v2 重新激活该 agent 修复
     → REVIEW_FIX_MAX_RETRY 上限(防 reviewer 反复 fail 无限修)

④ gatekeeper 纳入 verdict(改造 P3 _on_quiescence)
   quiescence → 先触发流程逻辑审(②)→ 综合决策:
     · artifact 完整 + 所有 verdict pass → gate_pass + done
     · 有 verdict.fail 且可修(未达上限)→ 触发修复(③),不收尾
     · 不可修 / artifact 缺 → gate_reject + partial
```

---

## 4. Reviewer 角色转变(关键)

- **P3**:reviewer 被链式 mention(video→review)→ work-driver 跑 review step(链式最后一棒)。
- **P4**:reviewer **不再是链式 work-driver 一环** —— 全程订阅 `artifact.update` 即时质量审
  + 收尾流程逻辑审。`DEFAULT_NEXT_STEP` 链到 reviewer 的那一棒在 v2 路径改为"不产 artifact、
  只出 verdict";流程逻辑审由 observer 在 quiescence 触发(不依赖 reviewer 被 mention)。

---

## 5. 模块结构(只动 `apps/web-backend/`)

| 文件 | 改动 |
|---|---|
| **`process_review.py`**【新增 ~130 行】 | 流程逻辑轨 3 纯规则:`ProcessReviewer.check(task_id, events) -> ProcessReviewResult(passed, findings)`;版本一致 / 依赖图 / 参与度 各一规则函数。独立可测(像 `ArtifactGate`) |
| `harness.py`【扩】 | `AgentWorker.handle_v2_event` reviewer 特化分支(`agent_id=="reviewer"` 且 event 是 `ArtifactUpdate` → 质量审,不走 work-driver);step `status="needs_fix"` 重置解除去重 |
| `coordinator_observer.py`【扩】 | `_on_quiescence` 纳入流程逻辑审 + verdict 综合决策;监听 `ReviewerVerdict(fail)` → 转写 + 重置 + `force_run_v2` 修复 + `REVIEW_FIX_MAX_RETRY` 上限 |
| `pipeline.py`【小调】 | v2 reviewer 不产 artifact;`_quick_review` 复用为 v2 质量轨;移除 `_emit_v2_finalization` 的 ReviewerVerdict 示例 |

**Structure Decision**:流程逻辑轨独立 `process_review.py`(纯规则聚拢,像 P3 ArtifactGate);质量轨
复用既有 `_quick_review`(决策 B,不加抽象);修复闭环并入 observer(已是 Coordinator 转写载体)。

---

## 6. v1 路径(零回归红线)

完全不动。v1 `_gate_review`(REVIEW_GATES 4 step 即时质量门)+ `_quick_review` 保留;P4 双轨改动
全守 `is_v2`。`_quick_review` 被 v2 质量轨复用(同一 LLM 审,触发源 + emit 不同)。沿用 P1-P3 的
US1 红线测试模式(字段级断言 v1 行为不变)。

---

## 7. 测试策略(ScriptedBackend 测试级)

| 测试面 | 怎么测 |
|---|---|
| 质量轨即时审 | ScriptedBackend 脚本化 reviewer `_quick_review` turn → artifact.update 触发 → emit verdict(quality);pass/fail 两路 |
| 流程逻辑轨 3 规则 | `ProcessReviewer` 单测:版本一致/依赖图/参与度 各齐/缺(纯规则真测试) |
| verdict.fail 修复闭环 | fail → 转写 intervene + 重置 step status + 重新激活 fix_agent + 达上限停 |
| gatekeeper 纳入 verdict | 完整+pass→done;fail 可修→修复;不可修/缺→partial |
| v1 零回归 | v1 _gate_review/REVIEW_GATES 不变;P4 守 is_v2 |
| 质量轨真 LLM | ⏸️ 挂 Windows issue(同 P3 T051) |

---

## 8. 范围边界(Out of Scope)

- **跨引用一致**(artifact 内容间引用)→ 归质量轨 report-reviewer LLM 或 P5(不进流程逻辑轨纯规则)
- **质量轨真 LLM 验收** → 挂 Windows issue(ScriptedBackend 测试级覆盖编排)
- **humanizer 深度集成 / report-reviewer skill 内部改造** → 复用现状,不深化
- **P5 prompt 重写 / P6 并发 / P7 UX / P8** → 后续阶段

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| reviewer 特化分支与 P3 work-driver 路径分叉 | `handle_v2_event` 内 `agent_id=="reviewer"` 清晰隔离 + 注释 |
| 质量轨即时审每 artifact 一次 LLM(4 次成本) | 同一 artifact 版本不重审(version 去重);即时反馈价值 > 成本 |
| 修复重跑与 P3 step-success 去重交互 | `needs_fix` 重置 status 解除去重;`REVIEW_FIX_MAX_RETRY` 上限;修复后重新 success |
| 两个 fail 来源(质量即时 + 流程逻辑收尾)统一 | observer 统一监听 `ReviewerVerdict(fail)`,按 `suggested_fix_agent` 转写,一套修复机制 |

---

## 10. 宪章合规

- **IV 边界**:Reviewer 出 verdict(质量轨 LLM + 流程逻辑轨规则),**不直接 @**(verdict.fail 由
  observer/Coordinator 转写),**不重写产物**(只出 verdict + suggestions,修复由 fix_agent 自跑)。
  流程逻辑轨纯规则不引 LLM;质量轨 LLM 是 **Reviewer 本职**(report-reviewer skill),非 Coordinator。
  **无需新宪章修订**(P3 的 1.1.0 已够;宪章原则 IV 本就允许 Reviewer 用 LLM 做质量验证)。
- I 脱敏(verdict/intervene 文案业务化中文)/ III 降级(审/修复异常不挂任务)/ V 隔离(修复走
  per-agent lock)均守。

---

## 11. 下一步

本 design 经用户确认 → `/speckit-specify` 产出 `specs/004-reviewer-dual-track/spec.md`
(会触发 `before_specify` git hook 从 main 创建 `004-reviewer-dual-track` 分支)。
按 P3 模式:产出 spec 后视用户意愿逐步推进 plan/tasks/implement。
