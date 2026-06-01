# Feature Specification: P5 — Transcript-Aware Prompt + speak/silent/done 输出契约

**Feature Branch**: `005-transcript-aware-prompt`

**Created**: 2026-05-31

**Status**: Draft

**Input**: User description: "P5 — Transcript-Aware Prompt 重写 + speak/silent/done 输出契约。把 8 个 step 的 prompt 从孤立串行升级为群聊感知,并把 agent 输出从 v1 的 prose+JSON(含 handoff)改为群聊信封 speak/silent/done。设计见 docs/superpowers/specs/2026-05-31-p5-transcript-aware-prompt-design.md。"

## 背景与动机

v2 群聊化路线图(`docs/开发文档.md` §9.4.5)的第 5 阶段。P1-P4 已让 9 个 agent 在
事件协议、订阅闸门、Coordinator observer、Reviewer 双轨上群聊化,但**每个 agent 的
prompt 仍是 v1 串行管线写法**:agent 互相看不见对方在群里说了什么,输出仍是「思考过程
prose + 一段 typed JSON(含 handoff.to 单目标交棒)」。

这导致两个落差:
1. agent 不感知 transcript —— 无法互相呼应、挑刺、对立(P8 yes-man 防御的前提)。
2. 输出契约是串行语义(handoff 交棒),而非群聊语义(speak/silent/done 三态自决)。

P5 补齐这两点,让所有 agent 真正"跑在群聊模式"。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent 感知群聊上下文(transcript-aware prompt)(Priority: P1)

作为协作链路里的一个 agent(如表达教练、文书),当我被触发开始干活时,我的 prompt 里应当
包含**最近一段群聊发言 + 当前可见 artifact 的摘要**,这样我能基于"同事们刚说了什么、
产出了什么"来工作,而不是只看自己那一棒的孤立输入。

**Why this priority**: 这是 P5 "从串行管线 → 群聊"的核心质变,也是后续 yes-man 防御 /
角色对立(P8)的前提。即便输出契约不变,光是 transcript 感知本身就交付了"群聊协作"的
价值,可独立验收。

**Independent Test**: 在 transcript 模式下触发任一 agent,检查其实际收到的 prompt 文本中
包含最近 K 条群聊发言原文 + 可见 artifact 的 delta_summary;关闭模式则 prompt 不含这段。

**Acceptance Scenarios**:

1. **Given** 任务进行中已有若干 agent 发过言、产出过 artifact,**When** 下游 agent 被触发
   构造 prompt,**Then** prompt 含「群聊上下文」段落,列出最近 K 条发言(发言人 + 内容)
   与当前可见核心 artifact 的摘要。
2. **Given** transcript 模式关闭(legacy),**When** 同一 agent 被触发,**Then** prompt
   不含群聊上下文段落,与 P4 现状字段级一致。
3. **Given** 群聊发言数量超过 K,**When** 构造 prompt,**Then** 只注入最近 K 条
   (K 默认 8,可经配置调整),不注入全量历史(压缩是后续阶段职责)。
4. **Given** 任务刚开始尚无任何发言/artifact,**When** 第一个 agent 被触发,**Then**
   群聊上下文段落为空或省略,不报错。

---

### User Story 2 - speak/silent/done 群聊信封输出契约(Priority: P2)

作为一个 agent,我的结构化输出应当是一个**群聊信封**,声明我这一步是「发言(speak)、
保持沉默(silent)、还是认为整体可收尾(done)」,并在发言时点名下游、附上我的产物;
而我产出的业务数据(讲稿、大纲、素材池等)作为信封内的 artifact 子对象,其格式不变。

**Why this priority**: 把交棒语义(handoff 单目标)升级为群聊三态自决,与 P2/P3 的
decide-to-speak / work-driver 语义对齐。依赖 US1 的 transcript 感知先建立群聊语境,
故排 P2。

**Independent Test**: transcript 模式下跑一个 agent,其输出 JSON 为信封结构
`{action, mentions, intent, reason, artifact}`;系统解析后能正确取出 artifact 子对象
作为该 step 产物,且 action=speak 时点名的下游被触发、silent/done 时按对应语义处理。

**Acceptance Scenarios**:

1. **Given** transcript 模式,**When** agent 完成工作并产出信封 `action=speak`,**Then**
   系统取出 `artifact` 子对象作为该 step 的产物(与 legacy 下 typed JSON 等价),并按
   `mentions` 点名下游、按 `intent` 标注语气。
2. **Given** agent 因依赖未就绪输出 `action=silent`,**When** 系统处理,**Then** 不产出
   artifact、不点名下游,记录 silent 及其 reason(与现有"沉默"语义一致)。
3. **Given** agent 输出 `action=done`,**When** 系统处理,**Then** 视为该角色认为整体
   可收尾,交由收尾管控(gatekeeper)综合判断,不强制结束。
4. **Given** agent 在 envelope 模式却输出了旧格式(无 action、直接是 typed JSON 或带
   handoff),**When** 系统解析,**Then** 容错降级:把整体当作 artifact、action 推断为
   speak、下游取 handoff.to,任务不中断。
5. **Given** 业务产物 schema(讲稿/大纲/素材池等),**When** 经信封包裹再解出,**Then**
   该 schema 字段级不变,下游处理、4 核心 artifact 抽取、导出全部与 legacy 一致。

---

### User Story 3 - 双轨可回退,真 LLM 链路零破坏(Priority: P1)

作为维护者,我需要 P5 的新契约可以通过一个开关一键开启/关闭,关闭时系统行为与 P4 现状
完全一致;这样万一新契约在真 LLM 下不稳定,可立即回退而不丢失已跑通的能力。

**Why this priority**: 输出契约是真 LLM 最敏感区(LLM JSON 输出不可靠),而真 LLM 全链路
刚跑通(P4 后)。回退能力是不破坏既有能力的安全前提,与 US1 并列 P1。

**Independent Test**: 开关置 legacy 跑回归套件全绿 + 与主干字段级一致;开关置 envelope
跑新契约测试全绿;切换开关不需改代码、不需重启以外的操作。

**Acceptance Scenarios**:

1. **Given** 开关=legacy(默认),**When** 跑既有回归测试套件,**Then** 全部通过,且
   产物字段与主干分支一致(零回归)。
2. **Given** 开关=envelope,**When** 跑新契约 + transcript 测试,**Then** 全部通过。
3. **Given** 任一模式,**When** 真 LLM 端到端跑一个常规汇报任务,**Then** 任务到达
   done 或 partial(非 failed),全部内容步骤成功,无解析失败 / 无因契约改造引入的崩溃。

### Edge Cases

- 群聊发言为空(任务起点)时,transcript 段落如何呈现 → 空段或省略,不报错(US1-AC4)。
- LLM 在 envelope 模式输出畸形信封(缺 action / artifact 嵌套层级错 / action 取值非法)
  → 容错:尽力取出 artifact,action 缺省按 speak 推断,绝不让任务崩溃(US2-AC4)。
- transcript 注入使 prompt 过长 → 由 K 上限控制(US1-AC3);压缩不在本期范围。
- silent / done 时 LLM 仍附带了 artifact → 按 action 语义处理,silent/done 不产出 artifact。
- 同一 agent 在任务生命周期内多次被触发(P3 已允许)→ 每次都注入当时最新的 transcript_tail。

## Requirements *(mandatory)*

### Functional Requirements

**transcript 感知(US1)**

- **FR-001**: 系统 MUST 在 transcript 模式下,为每个 agent 的 prompt 注入「群聊上下文」段落,
  内容为最近 K 条群聊发言原文(发言人 + 文本)。
- **FR-002**: 系统 MUST 在该段落中额外包含当前可见核心 artifact 的摘要(delta_summary)。
- **FR-003**: 系统 MUST 将注入的发言条数限制为最近 K 条(K 默认 8,可经配置项调整),
  不注入全量历史。
- **FR-004**: 群聊上下文为空(任务起点)时,系统 MUST 正常构造 prompt 而不报错。
- **FR-005**: 群聊上下文 MUST 复用既有的发言 / artifact 收集机制(不新增重复的事件订阅源)。

**输出契约(US2)**

- **FR-006**: 系统 MUST 在 envelope 模式下,指示 agent 输出群聊信封
  `{action, mentions, intent, reason, artifact}`,其中 `action ∈ {speak, silent, done}`。
- **FR-007**: 信封中的 `artifact` 子对象 MUST 沿用各步骤现有的业务产物结构(schema 不变)。
- **FR-008**: 系统 MUST 在解析后取出 `artifact` 子对象作为该步骤产物,使下游处理、
  核心 artifact 抽取、导出与 legacy 等价。
- **FR-009**: `action=speak` 时,系统 MUST 按 `mentions` 触发下游、按 `intent` 标注语气。
- **FR-010**: `action=silent` 时,系统 MUST 不产出 artifact、不点名下游,并记录 reason。
- **FR-011**: `action=done` 时,系统 MUST 将其作为"该角色认为可收尾"的信号交收尾管控
  综合判断,不强制结束任务。
- **FR-012**: 当 envelope 模式下收到旧格式输出(无 action)时,系统 MUST 容错降级:
  整体当 artifact、action 推断 speak、下游取 handoff.to,任务不中断。

**双轨与回退(US3)**

- **FR-013**: 系统 MUST 提供一个配置开关控制 transcript 感知 + 信封契约的开启
  (默认关闭 = legacy,等价 P4 现状)。
- **FR-014**: 开关关闭时,系统 MUST 完全短路 P5 新增逻辑,行为与主干字段级一致(零回归)。
- **FR-015**: 切换开关 MUST 不需要修改代码(仅配置),支持一键回退。

**降级(贯穿,沿用宪章原则 III)**

- **FR-016**: 任何 transcript 渲染 / 信封解析的异常 MUST 降级处理(记录告警),
  不得使步骤或任务崩溃。

### Key Entities *(include if feature involves data)*

- **群聊上下文(transcript_tail)**: 注入 prompt 的只读上下文片段,由最近 K 条发言
  (发言人、文本)+ 当前可见核心 artifact 摘要组成。无持久化,每次构造 prompt 时实时渲染。
- **群聊信封(envelope)**: agent 结构化输出的外层结构。属性:action(speak/silent/done)、
  mentions(点名的下游 agent 列表)、intent(语气)、reason(silent 时说明)、
  artifact(内含各步骤原有业务产物,schema 不变)。
- **契约模式开关**: 一个配置值,取 legacy(默认)或 envelope,决定 prompt 写法与解析路径。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: legacy 模式下,既有回归测试套件 100% 通过,且产物字段与主干分支逐字段一致
  (零回归)。
- **SC-002**: envelope 模式下,新增的 transcript 渲染 + 信封解析 + 链式闭环测试 100% 通过。
- **SC-003**: envelope 模式下真 LLM 端到端跑 1 个常规汇报任务,任务到达 done/partial
  (非 failed),全部内容步骤成功,无因契约改造引入的解析失败或崩溃。
- **SC-004**: transcript 模式下,被触发 agent 的 prompt 100% 含群聊上下文段落;
  且注入发言条数 ≤ K(默认 8)。
- **SC-005**: envelope 模式下,经信封包裹再解出的业务产物与 legacy 模式逐字段一致
  (信封不改变 artifact schema)。

## Assumptions

- 真 LLM 验证使用当前已配置且连通的 provider(deepseek);真 LLM 偶发网络抖动属环境因素,
  不计入契约改造的成功判定。
- transcript 的发言 / artifact 数据源已存在(P3 observer 已收集最近发言与 artifact 时序),
  P5 复用之,不新建独立收集器。
- rolling summary / transcript 压缩、并发、UX 不在本期范围(分别为 P8 / P6 / P7)。
- 业务产物 schema(讲稿、大纲、素材池、slides、narrations 等)在本期保持不变。
- 验收基线沿用 P2-P4 惯例:ScriptedBackend 测试级全绿 + v1 字段级零回归 + 1 次真 LLM 端到端。
- 无需宪章修订:transcript-aware 是 prompt 工程,不引入新的 LLM 决策权,Coordinator 仍是
  规则引擎,Reviewer 职责不变。

## Dependencies

- 依赖 P1(事件协议 / artifact 版本)、P2(订阅闸门)、P3(observer 收集发言 + artifact
  时序、work-driver)、P4(Reviewer 双轨),均已合入主干且真 LLM 链路已跑通。
