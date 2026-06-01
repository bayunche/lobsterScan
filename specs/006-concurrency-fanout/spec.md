# Feature Specification: P6 — EventBus fan-out 并发 + html/video 真并行

**Feature Branch**: `006-concurrency-fanout`

**Created**: 2026-06-01

**Status**: Draft

**Input**: User description: "P6 — EventBus fan-out 并发 + html/video 真并行。让 html-designer + video-producer 真并行(同依赖 copywriting、不同 agentDir、不写核心 artifact),并把 EventBus.emit 从串行改为 fan-out 并发,降低总耗时。设计见 docs/superpowers/specs/2026-06-01-p6-concurrency-design.md。"

## 背景与动机

v2 群聊化路线图(`docs/开发文档.md` §9.4.5)的第 6 阶段。P1-P5 已让群聊协议、订阅、
Coordinator、Reviewer、transcript-aware prompt 全部就位且真 LLM 跑通。但执行仍是**串行**:

1. **html-designer 和 video-producer 串行跑**,尽管两者都只依赖 copywriting 的产物
   (Script),互不依赖、写不同产物、用不同隔离环境 —— 本可同时跑。
2. **事件分发串行**:每个事件逐个 handler `await`,虽然真正耗时在各 agent 的 LLM 调用,
   但串行分发在并行场景下成为不必要的等待。

P6 让这两处并发化,降低任务总耗时,同时**不破坏** P3/P4 的顺序去重保证、P5 的契约,
默认开关关闭即等价今天行为。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - html/video 真并行(Priority: P1)🎯 MVP

作为等报告的用户,当讲稿(Script)就绪后,HTML 演示页和数字人视频这两件互不依赖的产物
应当**同时开始制作**,而不是一个做完再做另一个,从而更快拿到完整报告。

**Why this priority**: 这是 P6 总耗时下降的主要来源(两个最慢的产物步骤之一并行)。
两者同依赖 copywriting、不同隔离环境、不写共享产物,并行最安全、收益最直接,可独立验收。

**Independent Test**: 并发开关开启时,讲稿就绪后 html-designer 与 video-producer 同时进入
执行态(两个"开始"信号在对方完成前都出现);关闭时仍串行(一个完成才另一个开始)。

**Acceptance Scenarios**:

1. **Given** 并发开关开启且 copywriting 已产出 Script,**When** 系统推进下游,**Then**
   html-designer 与 video-producer **同时**被唤醒并进入执行态(两个"开始"事件交错出现,
   不是一个结束后才另一个开始)。
2. **Given** 两个并行步骤都在执行,**When** 系统统计在跑步骤数,**Then** 峰值并发达到 2。
3. **Given** 两个并行步骤都完成,**When** 系统判断收尾,**Then** 在跑步骤数归零后才进入
   收尾管控,最终状态为完成或部分完成(非失败)。
4. **Given** 并发开关关闭(默认),**When** 同样流程,**Then** html-designer 与
   video-producer 串行(一个完成才另一个开始),与现状一致。
5. **Given** 两个并行步骤之一失败(如视频环境不可用),**When** 系统处理,**Then** 另一个
   不受影响照常完成,任务降级为部分完成(沿用降级原则),不整体失败。

---

### User Story 2 - EventBus 事件分发并发(Priority: P2)

作为系统内部的事件分发机制,当一个事件有多个订阅者时,这些订阅者应当**并发**收到并处理,
而不是逐个串行等待,以减少分发延迟。

**Why this priority**: 配合 US1 的并行执行,事件分发并发化进一步减少等待。但真正耗时在
LLM 调用,分发并发是次要收益,故排 P2;且它触及原串行的顺序保证,需谨慎,依赖 US1 先立住。

**Independent Test**: 并发开关开启时,一个事件的多个订阅处理函数并发执行(总耗时接近最慢
单个而非求和);单个处理函数抛错被隔离,不影响其他处理函数。

**Acceptance Scenarios**:

1. **Given** 并发开关开启且某事件有多个订阅处理函数,**When** 事件发出,**Then** 这些处理
   函数并发执行(整体完成时间接近最慢的单个,而非所有之和)。
2. **Given** 某个订阅处理函数执行中抛错,**When** 并发分发,**Then** 错误被隔离(仅记录),
   其余处理函数照常完成,事件流不中断。
3. **Given** 并发开关关闭(默认),**When** 事件发出,**Then** 处理函数按原串行顺序逐个
   执行,行为与现状一致。
4. **Given** 同一步骤被多条路径(订阅 + mention)触发,**When** 并发分发,**Then** 该步骤
   仍只真正执行一次(去重由每个角色的串行锁 + "已完成则跳过"保证,不依赖分发串行)。

---

### User Story 3 - 双开关可回退,零回归(Priority: P1)

作为维护者,我需要 P6 的并发行为可由开关一键关闭,关闭时系统行为与 P5 现状完全一致,
确保并发改造不破坏已跑通的串行链路。

**Why this priority**: 并发触及执行顺序与去重等关键不变量,而完整链路刚跑通。可回退是
不破坏既有能力的安全前提,与 US1 并列 P1。

**Independent Test**: 开关关闭跑既有全量测试全绿 + 与主干字段级一致;开关开启跑并发测试
全绿;切换仅改配置不改代码。

**Acceptance Scenarios**:

1. **Given** 开关关闭(默认),**When** 跑既有全量测试套件,**Then** 全部通过,产物字段
   与主干一致(零回归)。
2. **Given** 开关开启,**When** 跑并发相关测试,**Then** 全部通过。
3. **Given** 任一开关状态,**When** 真实跑一个常规汇报任务,**Then** 到达完成/部分完成
   (非失败),全部内容步骤成功;开启时 Script 就绪后到收尾的耗时不长于关闭时(并发更快)。

### Edge Cases

- 两个并行步骤之一失败 → 另一个照常,任务部分完成(US1-AC5,沿用降级原则)。
- 真实环境下负责讲稿的角色只点名了一个下游(未同时点名两个)→ 系统兜底补齐双目标,
  或缺失的那个由停滞监测激活,不漏步。
- 一个并行步骤远慢于另一个 → 收尾等在跑步骤数归零(两个都完成)才触发,不早退。
- 事件分发并发时多个处理函数同时修改在跑计数 → 单执行环境下计数增减安全,无竞争。
- 开关开启但某任务实际只有单订阅者 → 并发分发退化为单个,无副作用。

## Requirements *(mandatory)*

### Functional Requirements

**html/video 并行(US1)**

- **FR-001**: 并发开关开启时,讲稿(Script)就绪后,系统 MUST 同时唤醒 html-designer 与
  video-producer 两个步骤(而非串行)。
- **FR-002**: 两个并行步骤 MUST 各自在独立隔离环境执行(不共享隔离环境,沿用隔离原则)。
- **FR-003**: 系统 MUST 在两个并行步骤**都**完成后才进入收尾判断(在跑步骤数归零)。
- **FR-004**: 两个并行步骤之一失败时,系统 MUST 让另一个照常完成,任务降级为部分完成,
  不整体失败。
- **FR-005**: 并发开关关闭时,html-designer 与 video-producer MUST 串行(与现状一致)。

**事件分发并发(US2)**

- **FR-006**: 并发开关开启时,一个事件的多个订阅处理函数 MUST 并发执行。
- **FR-007**: 并发分发时,单个处理函数抛错 MUST 被隔离(仅记录),不影响其他处理函数。
- **FR-008**: 并发分发 MUST NOT 破坏"同一步骤只真正执行一次"的去重(去重由每角色串行锁 +
  "已完成则跳过"保证,不依赖分发串行)。
- **FR-009**: 并发开关关闭时,事件分发 MUST 按原串行顺序执行(与现状一致)。

**双开关与回退(US3)**

- **FR-010**: 系统 MUST 提供配置开关控制并发(默认关闭 = 现状)。
- **FR-011**: 开关关闭时,系统 MUST 完全短路 P6 新增逻辑,行为与主干字段级一致(零回归)。
- **FR-012**: 切换开关 MUST 不需修改代码(仅配置),支持一键回退。

**降级(贯穿,沿用降级原则)**

- **FR-013**: 任何并发执行/分发的异常 MUST 降级处理(记录告警),不得使任务崩溃。

### Key Entities *(include if feature involves data)*

- **并发开关**: 一个配置值,取关闭(默认)或开启,决定 html/video 并行触发 + 事件分发并发。
- **并行触发目标**: 讲稿就绪后被同时唤醒的下游集合(html-designer + video-producer)。
- **在跑步骤计数**: 当前并发执行中的步骤数;并行使其峰值达 2,收尾依赖其归零。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 开关关闭时,既有全量测试套件 100% 通过,产物字段与主干逐字段一致(零回归)。
- **SC-002**: 开关开启时,新增的并行触发 + 分发并发 + 收尾测试 100% 通过。
- **SC-003**: 开关开启时真实跑一个常规汇报任务,到达完成/部分完成(非失败),全部内容
  步骤成功;Script 就绪到收尾的耗时**不长于**开关关闭时(并行不更慢,通常更快)。
- **SC-004**: 开关开启时,html-designer 与 video-producer 的"开始"信号在对方完成前**都**
  出现(确证并行,而非串行)。
- **SC-005**: 任一开关状态下,同一步骤在一次任务内只真正执行一次(去重不被并发破坏)。

## Assumptions

- 真实验证使用当前已连通的 provider(deepseek);偶发网络抖动属环境因素,不计入并发改造
  的成功判定。
- html-designer / video-producer 都只依赖 copywriting 的产物,互不依赖,且不写 4 个核心
  共享产物(各写各自的 HTML / 视频),本期并行无产物冲突。
- 执行环境为单一异步事件循环(无多线程),故在跑步骤计数的增减无数据竞争。
- 并发收益主要体现在 html/video 两步并行;事件分发并发是次要收益(真正耗时在外部调用)。
- 不做产物的乐观并发合并(html/video 不写共享产物,无需);不做事件压缩 / UX(后续阶段)。
- 验收基线沿用 P2-P5 惯例:测试级全绿 + 字段级零回归 + 1 次真实端到端(对比开/关耗时)。
- 无需宪章修订:并发是执行优化,不改 Coordinator/Reviewer 职责,不引入新的 LLM 决策权。

## Dependencies

- 依赖 P1-P5(协议 / 订阅闸门 + 串行锁 / observer + 在跑计数 / Reviewer / transcript-aware
  prompt),均已合入主干且真 LLM 双向(串行 emit + 单/双契约)已跑通。
