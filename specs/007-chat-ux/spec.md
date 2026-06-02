# Feature Specification: P7 — 群聊 UX(@高亮 + silent 灰显 + artifact diff + prompt 模板)

**Feature Branch**: `007-chat-ux`

**Created**: 2026-06-01

**Status**: Draft

**Input**: User description: "P7 — 群聊 UX。@高亮 + silent 灰显气泡 + artifact diff 内联 + refine chips→prompt 模板。让群聊感官闭环。设计见 docs/superpowers/specs/2026-06-01-p7-chat-ux-design.md。"

## 背景与动机

v2 群聊化路线图(`docs/开发文档.md` §9.4.5)第 7 阶段。P1-P6 已让后端群聊协议、订阅、
Coordinator、Reviewer、prompt、并发全部就位,但**前端群聊界面还感知不到这些语义**:

- 后端 agent 之间 `@` 点名(mentions)、选择沉默(silent + reason)、更新已有产物
  (artifact 版本 + 改动摘要)等 v2 信息,**没有透传到前端消息**。
- 前端消息只有「谁说了什么」,看不到「@了谁(高亮)」「谁这轮没开口(在场但沉默)」
  「这次改了什么(产物 diff)」。
- 现有 5 个固定快捷调整按钮(refine chips)不够灵活。

P7 补齐这些前端感官,让用户看到一个**有来有往、有人沉默、有版本演进**的真实群聊,
而非一串孤立的发言。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - @提及高亮(Priority: P1)🎯 MVP

作为看群聊的用户,当某条消息里点名了某位成员(如「@分析师 接着看重点」),我希望
那个 `@分析师` 被**高亮**显示,这样我一眼能看出谁在叫谁、对话往哪流转。

**Why this priority**: @高亮是群聊感最基础、最高频的视觉信号(几乎每条接力消息都有),
独立可验收,且不依赖其他子项。

**Independent Test**: 一条文本含 `@分析师` 的消息,渲染后该片段呈高亮样式(与普通文字
区分);非成员名的裸 `@xxx` 不误高亮;一条不含 `@` 的消息无高亮。

**Acceptance Scenarios**:

1. **Given** 一条 agent 消息文本含 `@分析师`,**When** 渲染,**Then** `@分析师` 显示为
   高亮 chip(视觉上与正文区分)。
2. **Given** 一条消息含多个 `@`(如 `@设计师 @视频制作`),**When** 渲染,**Then** 每个
   成员名各自高亮。
3. **Given** 文本含 `@某不存在的名字`,**When** 渲染,**Then** 不高亮(只高亮 9 位成员的
   中文名)。
4. **Given** 消息携带了点名信息(mentions),**When** 渲染,**Then** 高亮与 mentions 一致。

---

### User Story 2 - silent 灰显气泡(Priority: P2)

作为看群聊的用户,当某位成员这一轮选择不发言(等上游就绪/无补充),我希望看到一个
**灰色的「掠过」小气泡**,而不是它完全消失,这样我知道它在场、只是这轮没话说。

**Why this priority**: 让"沉默"可见是群聊真实感的关键(否则用户以为 agent 掉线)。
依赖后端透传 silent 语义,排 P2。

**Independent Test**: 后端推一条 silent 类型消息(带 reason),前端渲染成灰色虚线小气泡,
含成员名 + 简短理由;普通消息不受影响。

**Acceptance Scenarios**:

1. **Given** 某成员这轮 silent(带 reason 如「等大纲就绪」),**When** 渲染,**Then** 显示
   灰色虚线小气泡「{成员名} 掠过 · {reason}」,视觉上比正常气泡淡。
2. **Given** silent 气泡,**When** 用户看,**Then** 不出现技术标识(agent_id 等),只显示
   中文成员名 + 业务化理由(守脱敏)。
3. **Given** 同一群聊既有正常发言又有 silent,**When** 渲染,**Then** 两者视觉清晰区分,
   silent 不抢戏(更小更淡)。

---

### User Story 3 - artifact diff 内联(Priority: P2)

作为看群聊的用户,当某位成员**修改了**之前已产出的产物(不是首次产出,而是改进版),
我希望在那条消息里看到**「改了什么」**的一行说明(如「📝 改了大纲 第2版:补充风险章节」),
这样我能跟上产物的演进,而不是只看到一个新文件。

**Why this priority**: 让产物版本演进可见,配合 P4 reviewer 修复闭环(改进版)。依赖后端
透传 artifact 版本 + 改动摘要,排 P2。

**Independent Test**: 后端推一条带 artifact 改动信息(版本≥2 + 摘要)的消息,前端在气泡内
内联显示一行「📝 改了 {产物名} 第 N 版:{摘要}」;首次产出(版本 1)不显示 diff 行。

**Acceptance Scenarios**:

1. **Given** 某成员更新已有产物到第 2 版(带改动摘要),**When** 渲染,**Then** 气泡内联
   一行「📝 改了 {产物中文名} 第 2 版:{摘要}」。
2. **Given** 某成员首次产出产物(第 1 版),**When** 渲染,**Then** **不**显示 diff 行
   (首次不是"改")。
3. **Given** diff 行,**When** 用户看,**Then** 产物名用中文友好名(大纲/讲稿/素材池等),
   不暴露内部 artifact id。

---

### User Story 4 - refine chips 改为 prompt 模板(Priority: P3)

作为完成任务后想微调的用户,我希望快捷调整区不止是 5 个固定按钮,而是可填入输入框的
**常用调整模板**,这样我能在模板基础上再补充具体要求,更灵活。

**Why this priority**: 提升微调灵活性,纯前端、独立、低风险,但价值低于前三项群聊感官,排 P3。

**Independent Test**: 任务完成态下,快捷调整区显示常用模板;点击模板把对应 prompt 文本填入
输入框(供用户编辑后发送),而非直接固定执行。

**Acceptance Scenarios**:

1. **Given** 任务完成(done/partial),**When** 看快捷调整区,**Then** 显示一组常用调整
   模板(覆盖现有 5 个动作语义 + 可扩展)。
2. **Given** 点击某模板,**When** 操作,**Then** 对应 prompt 文本填入输入框,用户可编辑
   后再发送(保留现有发送/微调链路)。
3. **Given** 任务未完成态,**When** 看,**Then** 不显示模板区(与现状一致)。

### Edge Cases

- 消息文本含 `@` 但后面不是成员名 → 不高亮(US1-AC3)。
- silent 消息无 reason → 气泡显示「{成员名} 掠过」省略理由,不报错。
- artifact 改动摘要为空 → diff 行显示「📝 改了 {产物名} 第 N 版」省略摘要。
- 旧消息(后端未透传新字段)→ 前端走原渲染,无高亮/silent/diff(向后兼容,零回归)。
- 同一消息既有 @ 又有 artifact diff → 两者都渲染,互不干扰。

## Requirements *(mandatory)*

### Functional Requirements

**后端透传(additive,US1-3 前提)**

- **FR-001**: 后端 MUST 在群聊消息中附带点名信息(mentions),供前端高亮。
- **FR-002**: 后端 MUST 在成员选择沉默时也推送一条群聊消息(标记为 silent 类型 + 理由),
  而非完全不推送。
- **FR-003**: 后端 MUST 在成员更新已有产物(版本 ≥2)时,于消息中附带产物改动信息
  (产物标识 + 版本 + 改动摘要)。
- **FR-004**: 上述新增字段 MUST 为 additive(可选),旧前端不读时行为不变(向后兼容,零回归)。

**前端渲染(US1-3)**

- **FR-005**: 前端 MUST 把消息文本里的 `@<成员中文名>` 渲染为高亮样式;只匹配 9 位成员的
  精确中文名,非成员名不高亮。
- **FR-006**: 前端 MUST 把 silent 类型消息渲染为灰色弱化的「掠过」气泡(含成员名 + 理由),
  视觉上与正常发言区分。
- **FR-007**: 前端 MUST 在消息携带产物改动信息(版本 ≥2)时,气泡内联显示一行
  「改了 {产物中文名} 第 N 版:{摘要}」;首版不显示。
- **FR-008**: 前端 MUST 在所有新渲染中只显示中文成员名 / 产物友好名,不暴露技术标识
  (agent_id / artifact 内部 id)(守脱敏原则 I)。

**prompt 模板(US4)**

- **FR-009**: 前端 MUST 在任务完成态把快捷调整区呈现为常用 prompt 模板组。
- **FR-010**: 点击模板 MUST 把对应文本填入输入框供用户编辑后发送(复用现有发送链路),
  不直接固定执行。

**回归**

- **FR-011**: 旧消息(无新字段)MUST 走原渲染路径,与改造前一致(零回归)。

### Key Entities *(include if feature involves data)*

- **群聊消息(扩展)**: 在现有消息基础上新增可选字段——点名信息(mentions)、沉默标记 +
  理由、产物改动信息(标识 + 版本 + 摘要)。均 additive,旧消费方忽略。
- **成员名集合**: 9 位成员的中文显示名(前端已定义),用于 @ 精确匹配高亮。
- **prompt 模板**: 一组常用调整文本(覆盖现有 5 动作语义),点击填入输入框。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 含 `@<成员名>` 的消息 100% 高亮该片段;非成员名 0 误高亮。
- **SC-002**: silent 消息 100% 渲染为可辨识的弱化气泡(含成员名),不被误当普通发言或丢失。
- **SC-003**: 产物第 2 版及以上 100% 显示 diff 行;第 1 版 0 显示 diff 行。
- **SC-004**: 所有新 UX 元素 0 暴露技术标识(agent_id / artifact id)。
- **SC-005**: 旧消息(无新字段)渲染与改造前逐项一致(零回归);后端新字段为 additive,
  既有消息字段不变。
- **SC-006**: 前端组件测试 100% 通过(@高亮/silent/diff 三种渲染);CDP 实测浏览器截图
  确认三种 UX 真实可见。

## Assumptions

- 前端测试用 Vitest + Testing Library(项目当前无前端测试基建,本期引入)。
- CDP 实测用已安装的 Playwright(后端 video 依赖)驱动浏览器;真实任务偶发网络抖动属环境,
  不计入 UX 渲染判定。
- 后端新增字段为 additive 可选,无需 flag(旧前端忽略即向后兼容)。
- 9 位成员中文名已在前端定义,直接复用作 @ 匹配源。
- 不做用户 `@<成员>` 进群单聊(deferred,需后端把用户 @ 路由到 worker,可能触碰工作驱动逻辑)。
- 产物中文友好名映射沿用现有(大纲/讲稿/素材池等),不暴露内部 id。
- 无需宪章修订:纯 UX + additive 字段,不改 Coordinator/Reviewer 职责,不引入新 LLM 决策权,
  守原则 I(脱敏)。

## Dependencies

- 依赖 P1-P6(v2 事件协议含 mentions/silent/artifact 版本、并发等),均已合主干且真 LLM 已跑通。
- 后端 chat.message 生成链路(overlay / pipeline chat 推送)、前端群聊界面(page.tsx)。
