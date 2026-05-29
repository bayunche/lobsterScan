# Feature Specification: Coordinator 转型(observer + gatekeeper)+ subscription 升级为 work-driver（P3）

**Feature Branch**: `003-coordinator-transform`

**Created**: 2026-05-30

**Status**: Draft

**Input**: User description: "P3 — Coordinator 转型(observer + gatekeeper)+ subscription 升级为 work-driver。完整设计见 docs/superpowers/specs/2026-05-29-p3-coordinator-transform-design.md。"

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 现有 v1 用户感受零变化（Priority: P1）🎯 MVP 红线

正在使用「会汇报」的现有用户,完全感知不到后端正在进行第三阶段架构演进。任务流程、群聊气泡、生成的汇报包、所需时长、错误降级行为,都与 P2 之后的 main 一致。Coordinator 的 chain 路由、起点派单、必经步骤保护、失败兜底在 `harness_version="v1"`(默认)时完全保留;P3 的 work-driver 转换、observer 监控、收尾 gatekeeper 这些 v2-only 行为在 v1 路径完全短路。

**Why this priority**: 与 P1/P2 同样的最低底线 —— v1 永远不能因为 v2 推进而出现回归。这条不通过,整个改造要回滚。

**Independent Test**: 在 main(含 P1+P2)跑 demo task → 在 P3 分支不传 `harness_version` 跑同 demo → 比对两边 `events.jsonl` 顺序与字段、`script.md`、`task.json` 状态一致;v1 路径不出现 work-driver 触发、stagnation/drift 判定、gatekeeper 校验的任何 log 或事件。

**Acceptance Scenarios**:

1. **Given** 用户提交常规 3 分钟项目进度汇报(不传 `harness_version`),**When** 后端运行在 P3 分支,**Then** 事件流、产物、状态码与 main 完全一致;Coordinator 仍按 `_resolve_target` 规则路由,不出现 P3 任何新行为。
2. **Given** 同上场景,**When** 某 agent 失败触发现有 Coordinator 兜底,**Then** 兜底路径(retry 次数、降级链、`agent.handoff` 兜底)与 main 完全一致。
3. **Given** 同上场景,**When** 用户用 refine chip 触发改写,**Then** refine 流程与 main 完全一致。

---

### User Story 2 - v2 任务由 subscription 链式驱动闭环（Priority: P2）

开 v2 feature flag 的实验任务,不再由 Coordinator chain 派单驱动。任务从起点 bootstrap 触发第一棒(资料员),此后每个 agent 跑完真实产物后在群里点名下一棒,被点名且依赖就绪的 agent **自动跑活**(真跑 step,产出真实产物),链式推进直到全部完成。这是"群聊化"的核心可见行为:worker 真正变成"听总线 + 自决 + 干活"的协作者,Coordinator 不再充当派单中枢。

**Why this priority**: P3 的核心交付物 —— subscription 从 P2 的"chat overlay(只表态不干活)"升级为真正的 work-driver。没有这条,P3 的全部价值不成立。

**Independent Test**: 用 `ScriptedBackend` 喂 8 个 step 的脚本化输出,跑一个 v2 任务 → 校验 8 个 step 全部由 subscription 触发并产出产物、task 状态闭环(done),全程 Coordinator 不调用 chain 路由(`_resolve_target`)。

**Acceptance Scenarios**:

1. **Given** v2 任务启动,**When** 起点 bootstrap 触发,**Then** 资料员(material)被唤醒并跑真实 step(产出 MaterialPool),不经 Coordinator chain handoff。
2. **Given** 某 agent 跑完产物并点名下一棒,**When** 下游 agent 的运行依赖(`requires`)已满足,**Then** 下游 agent 被订阅唤醒并**真跑 step**(产出对应核心 artifact),而非只发"收到"气泡。
3. **Given** 8 个 step 依次链式跑完,**When** 最后一棒(reviewer)完成,**Then** 任务自然终止,无需 Coordinator chain 派单介入。
4. **Given** 下游 agent 被点名但依赖缺失,**When** 进入决策闸门,**Then** 它表态等待(silent),不跑 step,等待依赖就绪后由后续事件再驱动。

---

### User Story 3 - 链卡住时 Coordinator 守住 liveness（Priority: P2）

v2 任务下,如果链式推进卡住(所有 agent 都在等依赖、或无人接力导致总线静默),Coordinator 作为 observer 检测到停滞,在群里发声把流程重新激活,让"依赖已就绪却还没动"的 agent 继续干活。任务不会因为去掉 chain 派单而死锁挂起。

**Why this priority**: A 驱动模型(链式自驱)的 liveness 命脉。去掉 Coordinator chain 兜底后,异步事件驱动天然存在"全员静默"死锁风险;没有 stagnation 守护,demo 第一天就可能挂死。

**Independent Test**: 单测构造死锁场景(worker 全部 silent / 链断)→ 观察 Coordinator emit `coordinator.intervene(kind=stagnation)` 重新激活"依赖满足却静默"的 worker → 任务 recover 继续推进。

**Acceptance Scenarios**:

1. **Given** v2 任务推进中所有 worker inbox 已空、无 in-flight step、task 未完成且仍有未产出的核心 artifact,**When** Coordinator observer 检测到此 quiescence 状态,**Then** emit `coordinator.intervene(kind=stagnation)`,重新激活依赖已就绪但尚未行动的 worker。
2. **Given** stagnation 被触发并激活了某 worker,**When** 该 worker 恢复干活,**Then** 链式推进继续,任务最终闭环。
3. **Given** stagnation 反复无解(激活后仍无 worker 能推进),**When** 达到兜底上限,**Then** Coordinator 进入收尾 gatekeeper 流程,按现有 artifact 给出 partial/failed,任务不无限挂起。

---

### User Story 4 - Coordinator 跑题纠偏（drift）（Priority: P3）

v2 任务下,Coordinator 作为 observer 周期性判断当前群聊讨论是否偏离了用户的原始汇报目标;若发现跑题,在群里复诵原始目标把讨论拉回。判断基于"原始目标 + 最近若干条发言"这一最小上下文,只发声提醒,不指定谁说话、不审内容质量、不改产物。

**Why this priority**: 群聊自由度提高后,agent 间自由 @ 可能逐渐偏离汇报主题。drift 纠偏是质量护栏。列为 P3 是因为它**依赖 Phase 0 宪章修订**(放宽 Coordinator 纯规则引擎约束),且价值低于 liveness(US3)与闭环(US2)。

**Why this priority(实施约束)**: 本 user story **被 Phase 0 宪章修订阻塞** —— 在宪章放宽"Coordinator 是纯规则引擎"之前不得实现(见 Dependencies)。

**Independent Test**: 注入 mock 的 drift 判断函数:返回"跑题"→ 校验 Coordinator emit `coordinator.intervene(kind=drift)` 复诵目标;返回"未跑题"→ 校验不 emit。判断函数走可注入抽象(同 backend mock 模式),测试不依赖真 LLM。

**Acceptance Scenarios**:

1. **Given** v2 任务推进到第 N 个 step(或触发 stagnation),**When** drift 判断认定讨论偏离原始目标,**Then** Coordinator emit `coordinator.intervene(kind=drift)`,文案复诵原始汇报目标(业务化中文,不含技术 ID)。
2. **Given** 同上时机,**When** drift 判断认定未跑题,**Then** Coordinator 不 emit drift 事件,链式推进不受打扰。
3. **Given** drift 判断调用失败(LLM 不可用 / 超时),**When** 异常发生,**Then** 跳过本次 drift 判断,仅 log warn,任务不挂、不 failed。

---

### User Story 5 - Coordinator 收尾把关产物完整性（gatekeeper）（Priority: P3）

v2 任务链式推进自然终止(或 stagnation 无解)时,Coordinator 作为 gatekeeper 校验 4 核心 artifact 依赖图的完整性:齐全则放行任务收尾(done),缺失则在群里点名缺失上游(gate_reject),并据此决定最终任务状态码。Coordinator 不再用 chain 的"必经步骤保护"硬拦跳步 —— 那一职责已下沉到各 worker 的运行依赖(P2 已落地的 `requires`)。

**Why this priority**: 输出管控是 Coordinator 转型后保留的两大职责之一(另一是 observer)。它替代了 v1 的"必经步骤保护"硬拦,把"不能跳步"从外部强拦变为"收尾校验 + worker 自己识相"。

**Independent Test**: 单测构造两种收尾态:① 4 核心 artifact 齐全 → 校验 emit `gate_pass` + task 状态 done;② 缺某核心 artifact → 校验 emit `gate_reject` 点名缺失上游 + task 状态 partial/failed。

**Acceptance Scenarios**:

1. **Given** v2 任务链式推进自然终止,**When** Coordinator gatekeeper 校验发现 4 核心 artifact(MaterialPool/ReportCore/Outline/Script)齐全,**Then** emit `coordinator.intervene(kind=gate_pass)` 并把 task 收尾为 done。
2. **Given** 同上时机,**When** 校验发现某核心 artifact 缺失,**Then** emit `coordinator.intervene(kind=gate_reject)` 点名缺失对应的上游 agent(业务化中文),task 收尾为 partial/failed(按降级原则,不崩)。
3. **Given** v2 路径下,**When** 任意 agent 想跳过中间步骤直接到 reviewer,**Then** 系统**不**靠 Coordinator chain 硬拦,而是该 agent 因 `requires` 依赖缺失自己 silent(P2 已落地);收尾时 gatekeeper 再兜底校验完整性。

---

### Edge Cases

- **起点 bootstrap 重复触发**:bootstrap 只应触发资料员一次;重复触发被去重,不产生双跑。
- **work-driver 与 v1 残留 race**:v2 路径下 Coordinator 的 chain 路由分支必须整段不挂载,避免同一 step 被 chain + subscription 双驱动。
- **stagnation 误判活跃为停滞**:有 in-flight step 正在跑时不得判停滞(双条件:inbox 空 + 无 in-flight)。
- **stagnation 激活后仍无人能推进**:有限次兜底后进入收尾,不无限循环 intervene。
- **drift 假阳性**:误报跑题时只复诵目标、不强制路由、不改产物,影响温和。
- **gatekeeper 在 stagnation 无解时被调用**:即便 artifact 不全也要给出确定的 partial/failed 状态码,任务必须终止。
- **v1 任务的 events.jsonl 写入 P3 v2-only 事件**(不应发生):日志报警 + 测试断言 0 命中。

---

## Requirements *(mandatory)*

### Functional Requirements

**work-driver 转换（US2）**

- **FR-001**: 系统 MUST 在 v2 路径下,当某 worker 被订阅唤醒且决策结果为"可发言/可行动"(依赖就绪)时,**真正运行该 worker 的 step**(产出真实产物 + 落盘 + 现有 emit),而非 P2 的仅 emit 确认气泡。
- **FR-002**: worker 运行 step 完成后 MUST emit 携带"下一棒"点名(mentions)与 artifact 更新的群聊事件,以驱动下游 worker 被订阅唤醒(链式推进)。
- **FR-003**: 依赖未就绪的被点名 worker MUST 表态等待(silent)且**不**运行 step,等待后续事件再驱动(沿用 P2 的 decide-to-speak 闸门)。
- **FR-004**: 同一 worker 被订阅唤醒运行 step MUST 走 per-agent 串行锁(P2 已落地),保证 OpenClaw agentDir 不并发占用。

**起点 bootstrap（US2）**

- **FR-005**: 系统 MUST 在 v2 任务启动时,通过 bootstrap 机制触发第一棒(资料员)运行 step,**不**经由 Coordinator 的 chain handoff。
- **FR-006**: bootstrap MUST 只触发一次;重复触发被去重。

**删 chain routing（v2 路径）（US2/US5）**

- **FR-007**: v2 路径下,Coordinator MUST NOT 执行 chain 路由(`_resolve_target` / 默认链 / 必经步骤保护)来驱动 step;这些 v1 chain 行为在 v2 路径整段不挂载。
- **FR-008**: 必经步骤保护 MUST 由各 worker 的运行依赖(`requires`,P2 已落地)在 worker 侧实现("依赖缺失则 silent"),不再由 Coordinator 在 chain 上硬拦。

**stagnation observer（US3）**

- **FR-009**: 系统 MUST 让 Coordinator 作为 observer 监听总线,以确定性规则检测停滞:所有 worker inbox 空 + 无 in-flight step + task 未完成 + 仍有未产出核心 artifact。
- **FR-010**: 检测到停滞时,Coordinator MUST emit `coordinator.intervene(kind=stagnation)`,重新激活"运行依赖已就绪却尚未行动"的 worker。
- **FR-011**: stagnation 激活 MUST NOT 指定 next-speaker(只激活依赖满足却静默者,不替其选下一棒);激活逻辑 MUST 为确定性规则,无 LLM。
- **FR-012**: stagnation 反复无解 MUST 有有限次兜底上限,超限进入收尾 gatekeeper,任务不无限挂起。

**drift observer（US4，依赖 Phase 0 宪章）**

- **FR-013**: 系统 MUST 让 Coordinator 周期性(每 N 个 step 或 stagnation 时)判断讨论是否偏离原始目标;判断输入限定为"原始目标 + 最近 K 条发言"的最小上下文,K/N 可配置。
- **FR-014**: drift 判定为偏离时,Coordinator MUST emit `coordinator.intervene(kind=drift)` 复诵原始目标;判定未偏离时 MUST NOT emit。
- **FR-015**: drift 判断 MUST 通过可注入抽象实现(便于测试注入 mock),不绑死特定 LLM 调用通道。
- **FR-016**: drift 的 LLM 判断输出 MUST 仅为"是否跑题 + 复诵文案";MUST NOT 路由 next-speaker、MUST NOT 审内容质量、MUST NOT 改任何产物。
- **FR-017**: drift 判断异常(LLM 不可用/超时)MUST 跳过本次判断 + log warn,任务不挂、不 failed(降级)。

**收尾 gatekeeper（US5）**

- **FR-018**: v2 任务链式自然终止或 stagnation 无解时,Coordinator MUST 作为 gatekeeper 校验 4 核心 artifact(MaterialPool/ReportCore/Outline/Script)依赖图完整性。
- **FR-019**: 校验齐全 MUST emit `coordinator.intervene(kind=gate_pass)` 并把 task 收尾为 done;缺失 MUST emit `coordinator.intervene(kind=gate_reject)` 点名缺失上游 + 收尾为 partial/failed。
- **FR-020**: gatekeeper MUST 在任何收尾路径(含 artifact 不全)给出确定的终止状态码,任务必须终止,不无限挂起。

**v1 零回归（US1,宪章红线）**

- **FR-021**: 所有 P3 改动 MUST 守 `is_v2` 守卫;v1 路径(默认)的 Coordinator chain 路由、起点派单、必经步骤保护、失败兜底与 main(含 P1+P2)**字段级一致**。
- **FR-022**: v1 路径 MUST NOT 触发 work-driver 转换、stagnation/drift 判定、收尾 gatekeeper 的任何 log 或事件。

**用户可见层脱敏（宪章原则 I）**

- **FR-023**: 任何用户可见层(chat 气泡、SSE 推送、API 错误、导出文件名)MUST NOT 出现 `_resolve_target` / `stagnation` / `drift` / `gatekeeper` / `bootstrap` / `quiescence` 等技术词;`coordinator.intervene` 经现有翻译层渲染为业务化中文气泡。

**降级（宪章原则 III）**

- **FR-024**: work-driver 转换 / stagnation 检测 / drift 判断 / gatekeeper 校验的任何 P3 内部错误 MUST NOT 导致整任务异常崩溃;按降级原则给出 partial/failed 且任务终止。

**测试（ScriptedBackend 闭环）**

- **FR-025**: 提供单测覆盖:work-driver 转换(SPEAK→真跑 step)、起点 bootstrap、stagnation 检测+激活、drift 判定 4 分支(偏离/未偏离/异常降级/不路由)、gatekeeper 齐全/缺失、v1 零回归。
- **FR-026**: 提供集成测试:用 `ScriptedBackend` 跑一个 v2 任务,8 个 step 由 subscription 链式驱动闭环(task done),全程 Coordinator 不调 chain 路由。

### Key Entities

- **WorkDriverDecision**:被订阅唤醒的 worker 的行动决策结果(运行 step / 等待 silent / 忽略);复用 P2 的 decide-to-speak 闸门,语义从"是否发言"升级为"是否干活"。
- **BootstrapTrigger**:v2 任务起点触发器,负责把第一棒(资料员)激活,只触发一次。
- **StagnationDetector**:Coordinator observer 的确定性停滞检测器,输入为总线静默状态(inbox 空 + 无 in-flight + 未完成 + 有缺失 artifact),输出"是否停滞 + 应激活哪些 worker"。
- **DriftJudge**:可注入的跑题判断抽象,输入"原始目标 + 最近 K 条发言",输出"是否偏离 + 复诵文案";默认实现走 LLM 调用,测试注入 mock。
- **ArtifactGate**:收尾 gatekeeper 的核心 artifact 完整性校验器,输入 4 核心 artifact 版本状态,输出"放行/拒绝 + 缺失清单 + 终止状态码"。
- **InterveneKind**:`coordinator.intervene` 事件的语义分类,P3 实际使用 `stagnation` / `drift` / `gate_pass` / `gate_reject`(均在 P1 已定义的枚举内)。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 现有 v1 用户跑 5 个 demo task(覆盖多种 report_type × duration),生成的 `events.jsonl` / `script.md` / `task.json` 关键字段与 P2 之后 main 的 baseline **逐字段相同**(ts 浮点宽容;视频/音频 binary 排除)。
- **SC-002**: v2 任务(ScriptedBackend)跑完后,**8 个 step 全部由 subscription 触发并产出产物**,task 状态闭环为 done,全程 Coordinator chain 路由(`_resolve_target`)**0 次调用**。
- **SC-003**: 至少 1 个 step 的运行可通过 trace 关联到上游发言的点名(mention 驱动),证明 subscription 是 work-driver 而非 chat overlay。
- **SC-004**: 构造的死锁场景下,stagnation observer **100%** 检测并激活,任务最终 recover 闭环;stagnation 反复无解时 **100%** 在有限次内进入收尾,无无限挂起。
- **SC-005**: drift 判断 4 分支(偏离/未偏离/异常降级/不路由)单测全 pass;drift 判定为偏离时 emit `intervene(kind=drift)` 命中率 **100%**(mock 控制)。
- **SC-006**: 收尾 gatekeeper:artifact 齐全 → done、缺失 → partial/failed + 点名,两路单测全 pass;任意收尾路径都给出确定终止状态码(无挂起)。
- **SC-007**: 单测 + 集成测试 100% pass:work-driver 转换 + bootstrap + stagnation + drift + gatekeeper + v1 零回归 + ScriptedBackend 闭环 1 case end-to-end。
- **SC-008**: 任意用户可见层 grep 不到 `_resolve_target` / `stagnation` / `drift` / `gatekeeper` / `bootstrap` / `quiescence` 字面量。
- **SC-009**: `pnpm --filter web-frontend build` 与 `pnpm --filter admin-frontend build` 全绿(P3 不动 UI,防御性 check)。

---

## Assumptions

- **验收基线是 ScriptedBackend 测试级闭环**:真 LLM 闭环验收挂在 Windows 环境 issue(`docs/issues/windows-real-pipeline-runnability.md`)解决后人工补,不阻塞 P3 实施(类比 P2 的 T038/T040)。
- **drift LLM 调用通道**:默认实现待 plan 阶段定具体形态(倾向注入式 `DriftJudge` 抽象,与现有 backend 同款可注入模式);测试一律走 mock 注入,不依赖真 LLM。
- **drift 上下文 = minimal context**:只喂"原始目标 + 最近 K 条发言",不碰 8 个 step 的 prompt(transcript-aware 是 P5 的事)。
- **K(最近发言条数)/ N(每几 step 判 drift)是参数化阈值**,本期取合理默认,不做自适应学习。
- **`coordinator.intervene` 的 4 个 kind(stagnation/drift/gate_pass/gate_reject)在 P1 已定义**,P3 只是真实使用,不新增事件 schema。
- **per-agent 串行锁、decide-to-speak 闸门、`requires` 依赖、`_emit_v2_step_overlay` per-step 点名 均为 P2 已落地**,P3 直接复用。

---

## Dependencies

- **Phase 0 宪章修订(阻塞 US4 / FR-013~017)**:P3 实施第一步 MUST 走 `/speckit-constitution`,把宪章原则 IV / `docs/开发文档.md` §9.4.7 决策 1 的"Coordinator 是**纯**规则引擎"放宽为"observer 的 drift 判断允许一次受限 LLM 调用(只发声、不路由、不审质量、不改产物)";版本 1.0.0 → 1.1.0。**此条不过,drift(US4)不得实现**;US1/US2/US3/US5 不依赖宪章修订,可先行。
- **P1(已合 main)**:v2 事件协议(`coordinator.intervene` 等 5 类)+ artifact 版本化 + `harness_version`。
- **P2(已合 main)**:subscription 注册/分发 + decide-to-speak 闸门 + per-agent 锁 + `requires` 依赖 + `_emit_v2_step_overlay`。
- **`docs/开发文档.md` §9.4.5 P3 行 + §9.4.6 必经步骤保护下沉**。
- **设计来源**:`docs/superpowers/specs/2026-05-29-p3-coordinator-transform-design.md`。

---

## Out of Scope（P3 明确不做）

- **budget 监控**(token/轮次预算硬上限)→ P8。
- **drift 全量 transcript-aware**(把群聊全文喂各角色)→ P5;P3 只给 drift 喂 minimal context。
- **真 LLM 闭环验收**(真任务跑通 8 agent)→ 随 Windows 环境 issue 解决后人工补。
- **P4 Reviewer 双轨**(即时质量门 + 流程逻辑校验)、**P5 prompt 重写**(transcript-aware)、**P6 并发**(EventBus fan-out)、**P7 UX**(前端群聊呈现)→ 路线图后续阶段。
- **drift 误报的自适应调参 / 学习**→ 本期固定阈值。
- **删除 v1 路径的 chain routing 代码**→ v1 仍需要它;P3 只在 v2 路径绕过,不删 v1 用的实现。
