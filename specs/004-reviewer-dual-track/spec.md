# Feature Specification: Reviewer 双轨(质量 + 流程逻辑)+ verdict.fail 修复闭环（P4）

**Feature Branch**: `004-reviewer-dual-track`

**Created**: 2026-05-30

**Status**: Draft

**Input**: User description: "P4 — Reviewer 双轨(质量 + 流程逻辑)+ verdict.fail 修复闭环。完整设计见 docs/superpowers/specs/2026-05-30-p4-reviewer-dual-track-design.md。"

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 现有 v1 用户感受零变化（Priority: P1）🎯 MVP 红线

正在使用「会汇报」的现有用户,完全感知不到 Reviewer 双轨改造。任务流程、群聊气泡、生成的汇报包、所需时长、错误降级行为,都与 P3 之后的 main 一致。v1 的即时质量门(REVIEW_GATES 4 步后跑 `_quick_review`,fail 重做 1 次)在 `harness_version="v1"`(默认)时完全保留;P4 的双轨审校、verdict 事件、修复闭环这些 v2-only 行为在 v1 路径完全短路。

**Why this priority**: 与 P1/P2/P3 同样的最低底线 —— v1 永远不能因为 v2 推进而出现回归。这条不通过,整个改造要回滚。

**Independent Test**: 在 main(含 P1+P2+P3)跑 demo task → 在 P4 分支不传 `harness_version` 跑同 demo → 比对两边 `events.jsonl` / `script.md` / `task.json` 字段级一致;v1 路径不出现双轨审校、ReviewerVerdict、修复闭环的任何 log 或事件。

**Acceptance Scenarios**:

1. **Given** 用户提交常规 3 分钟项目进度汇报(不传 `harness_version`),**When** 后端运行在 P4 分支,**Then** 事件流、产物、状态码与 main 完全一致;v1 即时质量门(REVIEW_GATES + `_quick_review`)行为不变。
2. **Given** 同上场景,**When** 某 REVIEW_GATES step 质量门否决触发重做,**Then** 重做路径(1 次)与 main 完全一致。
3. **Given** 同上场景,**When** 用户用 refine chip 触发改写,**Then** refine 流程与 main 完全一致。

---

### User Story 2 - 质量轨:artifact 产出即时审校（Priority: P2）

开 v2 feature flag 的实验任务,每当一个核心 artifact(MaterialPool/ReportCore/Outline/Script)产出后,Reviewer 立即对该 artifact 做内容质量审校(套话扫描 / 自洽性 / supplement 落地),并在群里给出质量结论。用户能在过程中尽早看到"这一步质量过没过",而不必等到最后。

**Why this priority**: P4 的核心交付物之一 —— 把 v1"4 个固定 step 后的质量门"升级为"每个核心 artifact 产出即审"的事件驱动质量轨,早发现早反馈。

**Independent Test**: v2 任务里手工产出一个核心 artifact(emit artifact.update)→ 校验 Reviewer 即时 emit 一条 `reviewer.verdict(dimension=quality)`,带 pass/fail + 必要时 suggested_fix_agent;同一 artifact 版本不重复审。

**Acceptance Scenarios**:

1. **Given** v2 任务产出 MaterialPool 的某版本,**When** Reviewer 订阅到该 artifact 更新,**Then** Reviewer 即时对其做质量审校并 emit `reviewer.verdict(dimension=quality)`,结论为 pass 或 fail。
2. **Given** 质量审校判定 fail,**When** emit verdict,**Then** verdict 携带 `suggested_fix_agent`(指向该 artifact 的产出 agent)与可执行建议。
3. **Given** 同一 artifact 的同一版本已审过,**When** 再次收到该版本的更新通知,**Then** Reviewer 不重复审校(版本去重)。
4. **Given** Reviewer 在审校(不直接 @ 任何 agent),**When** 质量结论产生,**Then** verdict 事件不含 @ 语义(宪章 IV:Reviewer 不直接路由)。

---

### User Story 3 - 流程逻辑轨:收尾全局校验（Priority: P2）

v2 任务链式推进自然终止时,Reviewer 对整个任务做一次流程逻辑层面的全局校验:核心 artifact 的版本链是否一致、产出顺序是否符合依赖关系、该参与的 agent 是否都参与了。校验结论以 verdict 形式给出,供收尾决策使用。

**Why this priority**: P4 核心交付物之二 —— Reviewer 的"流程逻辑验证"职责(宪章 IV)落地。它替代了 v1 由 Coordinator 硬拦的"必经步骤保护"的收尾验证版本(P3 已把硬拦下沉到 worker 依赖,这里收尾补一刀全局一致性)。

**Independent Test**: 构造一个 v2 收尾态(部分 artifact 版本不一致 / 依赖顺序错 / 某 agent 缺席)→ 校验 Reviewer emit `reviewer.verdict(dimension=process_logic)` 且 findings 指出具体问题;全部正常 → verdict pass。

**Acceptance Scenarios**:

1. **Given** v2 任务链式终止(quiescence),**When** 收尾触发流程逻辑审,**Then** Reviewer 对 [版本一致 / 依赖图 / 参与度] 三项做规则校验并 emit `reviewer.verdict(dimension=process_logic, pass/fail)`。
2. **Given** 某核心 artifact 的版本链不一致(如下游引用了不存在的上游版本),**When** 流程逻辑审,**Then** verdict 为 fail,findings 指出版本不一致项。
3. **Given** 产出顺序违反依赖(如 Outline 在 ReportCore 之前出现),**When** 流程逻辑审,**Then** verdict 为 fail,findings 指出依赖违例。
4. **Given** 该参与的 agent 有缺席(某核心 artifact 始终没产出),**When** 流程逻辑审,**Then** verdict 为 fail,findings 指出参与度缺口。

---

### User Story 4 - verdict.fail 触发修复闭环（Priority: P2）

v2 任务下,当 Reviewer 给出 fail 结论(质量轨即时 fail 或流程逻辑轨收尾 fail),系统不是简单记录,而是由 Coordinator 把这条 fail 转写为对责任 agent 的点名,让该 agent 重做修复;修复有次数上限,避免反复打回导致死循环。修复完成后重新进入审校,直到通过或达上限。

**Why this priority**: P4 里程碑"Reviewer 能拒绝 task.end 并**触发修复**"。没有这条,双轨只是"只读体检",不能真正改善产物。

**Independent Test**: v2 任务里 Reviewer emit 一条 verdict(fail, suggested_fix_agent=X)→ 校验 Coordinator 转写为点名 X 的发声 + X 被重新激活跑修复;连续 fail 达上限 → 停止重做,转入收尾。

**Acceptance Scenarios**:

1. **Given** Reviewer emit `reviewer.verdict(fail, suggested_fix_agent=X)`,**When** 系统处理该 verdict,**Then** Coordinator 转写为点名 X 的群里发声(Reviewer 不直接 @,宪章 IV),且 X 的对应步骤被标记为需修复并重新激活执行。
2. **Given** X 修复后重新产出 artifact,**When** 新版本产出,**Then** 质量轨重新审校该新版本(闭环继续)。
3. **Given** 同一责任点连续被打回达修复次数上限,**When** 再次 fail,**Then** 系统停止重做,把该问题留待收尾决策(不无限修复)。
4. **Given** 修复触发重做,**When** 重做执行,**Then** 修复执行与同 agent 的其它执行串行(不并发,沿用隔离红线)。

---

### User Story 5 - 收尾决策纳入 verdict（Priority: P3）

v2 任务收尾时,任务的最终状态(完成 / 部分完成)不再只看"核心产物齐不齐",还要纳入 Reviewer 的双轨 verdict:产物齐全且 Reviewer 全部通过才算完成;有未解决的 fail 或产物缺失则按部分完成交付并说明原因。

**Why this priority**: 宪章原则 IV 明确收尾"校验 artifact 完整性 **+ Reviewer verdict**;决定 task.end 状态码"。这条让收尾决策真正双因子(完整性 + 质量/流程),而非只看完整性。

**Independent Test**: 构造两种收尾态:① 产物齐 + 所有 verdict pass → 任务完成;② 产物齐但有未解决的 verdict.fail → 部分完成 + 说明。校验最终状态码与说明文案符合预期。

**Acceptance Scenarios**:

1. **Given** v2 收尾时核心产物齐全且 Reviewer 双轨 verdict 全部 pass,**When** 收尾决策,**Then** 任务判为完成(done)。
2. **Given** 收尾时存在未解决(已达修复上限)的 verdict.fail,**When** 收尾决策,**Then** 任务判为部分完成(partial),并在群里业务化说明原因(不暴露技术细节)。
3. **Given** 收尾时核心产物缺失,**When** 收尾决策,**Then** 任务判为部分完成,点名缺失对应的责任 agent。

---

### Edge Cases

- **质量审校异常**(report-reviewer 调用失败/超时):降级跳过本次质量审,不阻塞任务、不让任务 failed。
- **流程逻辑审异常**(规则计算崩溃):降级为不出 process_logic verdict,收尾仍给出确定状态码。
- **suggested_fix_agent 缺失/无效**:verdict.fail 但没指明责任 agent → 不触发修复,留待收尾决策。
- **修复后仍 fail**:计入修复次数,达上限转部分完成。
- **同一 artifact 短时间多次更新**:按版本去重,只审最新版本,不重复审旧版本。
- **v1 任务出现 v2-only 事件**(不应发生):日志报警 + 测试断言 0 命中。

---

## Requirements *(mandatory)*

### Functional Requirements

**质量轨(US2)**

- **FR-001**: v2 路径下,Reviewer MUST 订阅核心 artifact 更新;每个核心 artifact 产出后即时对其做内容质量审校,并 emit `reviewer.verdict(dimension=quality, pass/fail)`。
- **FR-002**: 质量审校 MUST 复用现有 report-reviewer 质量评审能力(与 v1 即时质量门同一审校逻辑),触发源与产出事件不同。
- **FR-003**: 质量 verdict 为 fail 时 MUST 携带 `suggested_fix_agent`(该 artifact 的产出 agent)与可执行建议;Reviewer MUST NOT 直接 @ 该 agent(宪章 IV)。
- **FR-004**: 同一 artifact 的同一版本 MUST NOT 被重复质量审校(版本去重)。

**流程逻辑轨(US3)**

- **FR-005**: v2 任务链式终止(quiescence)时,系统 MUST 触发一次流程逻辑全局审,emit `reviewer.verdict(dimension=process_logic, pass/fail)`。
- **FR-006**: 流程逻辑审 MUST 用确定性规则覆盖三项:① 版本一致(核心 artifact 版本链一致)② 依赖图(产出顺序符合 MaterialPool→ReportCore→Outline→Script)③ 参与度(该参与的 agent 都留痕)。
- **FR-007**: 流程逻辑审 MUST NOT 调用 LLM(纯规则);跨引用一致性等需理解内容的检查不属于本轨(归质量轨或后续阶段)。
- **FR-008**: 流程逻辑 verdict 的 findings MUST 指出具体违例项(哪个 artifact / 哪条规则)。

**verdict.fail 修复闭环(US4)**

- **FR-009**: 系统 MUST 监听 Reviewer 的 fail verdict(质量轨或流程逻辑轨),由 Coordinator 把 fail 转写为对 `suggested_fix_agent` 的点名发声(Reviewer 不直接路由)。
- **FR-010**: 转写后 MUST 把该责任 agent 的对应步骤标记为需修复(解除 P3 work-driver 的"已成功跳过"去重),并重新激活该 agent 执行修复。
- **FR-011**: 修复 MUST 有次数上限(`REVIEW_FIX_MAX_RETRY`);同一责任点达上限后 MUST NOT 再触发修复,转入收尾决策。
- **FR-012**: 修复执行 MUST 与同 agent 的其它执行串行(沿用 per-agent 串行锁,宪章 V)。
- **FR-013**: `suggested_fix_agent` 缺失/无效时,verdict.fail MUST NOT 触发修复,留待收尾决策。

**收尾决策纳入 verdict(US5)**

- **FR-014**: v2 收尾决策 MUST 双因子:核心 artifact 完整性 + Reviewer 双轨 verdict。产物齐全且所有 verdict pass → 完成(done)。
- **FR-015**: 存在未解决(达修复上限)的 verdict.fail 或产物缺失 → 部分完成(partial),并业务化说明原因(脱敏)。

**v1 零回归(US1,宪章红线)**

- **FR-016**: 所有 P4 改动 MUST 守 `is_v2` 守卫;v1 路径(默认)的即时质量门(REVIEW_GATES + `_quick_review` + fail 重做 1 次)与 main(含 P1+P2+P3)**字段级一致**。
- **FR-017**: v1 路径 MUST NOT 触发双轨审校、ReviewerVerdict、修复闭环的任何 log 或事件。

**用户可见层脱敏(宪章原则 I)**

- **FR-018**: 任何用户可见层 MUST NOT 出现 `process_logic` / `verdict` / `suggested_fix_agent` / `needs_fix` / `quiescence` 等技术词;Reviewer 结论与修复点名经现有翻译层渲染为业务化中文。

**降级(宪章原则 III)**

- **FR-019**: 质量审校 / 流程逻辑审 / 修复触发 / 收尾决策的任何 P4 内部错误 MUST NOT 导致整任务异常崩溃;按降级原则给出确定终止状态码。

**Reviewer 边界(宪章原则 IV)**

- **FR-020**: Reviewer MUST 只出 verdict + 建议,MUST NOT 直接 @ 其它 agent、MUST NOT 重写任何 agent 的产物(修复由责任 agent 自己执行)。

**测试(ScriptedBackend 基线)**

- **FR-021**: 提供单测覆盖:质量轨即时审(pass/fail/版本去重)、流程逻辑轨 3 规则(各齐/缺)、verdict.fail 修复闭环(转写/重置/重激活/上限)、收尾决策双因子、v1 零回归。
- **FR-022**: 提供集成测试:ScriptedBackend 脚本化 reviewer turn,跑通"artifact 产出 → 质量审 → fail → 修复 → 重审 → 收尾"端到端。

### Key Entities

- **QualityVerdict**:质量轨即时审结论(dimension=quality),含 pass/fail、findings、suggested_fix_agent;复用 P1 `ReviewerVerdict` schema。
- **ProcessLogicVerdict**:流程逻辑轨收尾审结论(dimension=process_logic),含 pass/fail、findings;复用 P1 `ReviewerVerdict` schema。
- **ProcessReviewer**:流程逻辑轨的纯规则校验器,输入任务的 artifact 版本状态 + 参与记录,输出三项规则(版本一致/依赖图/参与度)的结论与 findings。
- **FixCycle**:verdict.fail 触发的修复周期,含责任 agent、修复次数、上限;达上限转收尾。
- **ReviewerRole(v2)**:Reviewer 在 v2 的新角色 —— 全程订阅审校者(质量轨即时 + 流程逻辑轨收尾),不再是链式 work-driver 一环。
- **VerdictDimension**:`quality` / `process_logic`(P1 已定义枚举,P4 真实使用)。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 现有 v1 用户跑 5 个 demo task,生成的 `events.jsonl` / `script.md` / `task.json` 关键字段与 P3 之后 main 的 baseline **逐字段相同**(ts 浮点宽容;视频/音频 binary 排除)。
- **SC-002**: v2 任务每产出一个核心 artifact,Reviewer **即时** emit 一条 `reviewer.verdict(dimension=quality)`;同一 artifact 版本**0** 次重复审。
- **SC-003**: v2 任务收尾**恰好** emit 一条 `reviewer.verdict(dimension=process_logic)`,且对版本一致/依赖图/参与度三项给出确定结论。
- **SC-004**: 注入一条 `verdict(fail, suggested_fix_agent=X)`,**100%** 触发 Coordinator 转写点名 + X 修复重做;连续 fail 达 `REVIEW_FIX_MAX_RETRY` 后**100%** 停止重做(无无限循环)。
- **SC-005**: 收尾决策双因子:产物齐 + 全 pass → done;有未解决 fail / 缺产物 → partial,两路单测全 pass。
- **SC-006**: 单测 + 集成测试 100% pass:质量轨 + 流程逻辑轨 + 修复闭环 + 收尾决策 + v1 零回归;ScriptedBackend 端到端 1 case。
- **SC-007**: 任意用户可见层 grep 不到 `process_logic` / `verdict` / `suggested_fix_agent` / `needs_fix` / `quiescence` 字面量。
- **SC-008**: `pnpm --filter web-frontend build` 与 `pnpm --filter admin-frontend build` 全绿(P4 不动 UI,防御性 check)。

---

## Assumptions

- **验收基线是 ScriptedBackend 测试级闭环**:质量轨真 LLM(report-reviewer subprocess)受 Windows 环境 issue(`docs/issues/windows-real-pipeline-runnability.md`)阻塞,真 LLM 质量审验收挂 issue 后人工补(同 P3 T051),不阻塞 P4 实施。
- **质量轨复用既有 `_quick_review` / report-reviewer**(brainstorm 决策 B,不引入新抽象层);测试通过 ScriptedBackend 脚本化 reviewer 的 agent turn 覆盖编排。
- **流程逻辑轨是纯规则**(决策 A 三项),不调 LLM;跨引用一致性不在本轨。
- **`ReviewerVerdict` 的 quality / process_logic dimension 在 P1 已定义**,P4 真实使用,不新增事件 schema。
- **修复复用 P3 的 step 重置 + 重新激活机制**(work-driver 去重靠 step status,需修复时重置 status 解除);`REVIEW_FIX_MAX_RETRY` 取合理默认。
- **per-agent 串行锁、decide-to-speak、work-driver、observer watchdog、gatekeeper 均为 P2/P3 已落地**,P4 直接复用/扩展。

---

## Dependencies

- **P1(已合 main)**:`ReviewerVerdict` schema(quality/process_logic dimension)+ artifact 版本化。
- **P2(已合 main)**:subscription(reviewer 已订阅核心 artifact)+ per-agent 锁 + decide-to-speak。
- **P3(已合 main)**:work-driver(SPEAK→跑 step + step-success 去重)+ `force_run_v2` 激活 + `coordinator_observer`(quiescence + gatekeeper + intervene 转写)。
- **宪章 v1.1.0(已落地)**:原则 IV —— Reviewer 用 LLM 做质量验证是其本职,**无需新宪章修订**。
- **`docs/开发文档.md` §9.4.5 P4 行 + §4.9 Reviewer 职责**。
- **设计来源**:`docs/superpowers/specs/2026-05-30-p4-reviewer-dual-track-design.md`。

---

## Out of Scope（P4 明确不做）

- **跨引用一致性校验**(artifact 内容间引用)→ 归质量轨 report-reviewer LLM 或 P5(不进流程逻辑轨纯规则)。
- **质量轨真 LLM 验收**(report-reviewer 真任务跑通)→ 随 Windows 环境 issue 解决后人工补。
- **humanizer 深度集成 / report-reviewer skill 内部改造** → 复用现状,不深化。
- **P5 prompt 重写(transcript-aware)/ P6 并发 / P7 UX / P8 运营兜底** → 路线图后续阶段。
- **修复策略的智能化**(如按 fail 类型选不同修复路径)→ 本期统一"点名责任 agent 重做"。
