# P7 — 群聊 UX(@高亮 + silent 灰显 + artifact diff + prompt 模板)· 设计

> 设计日期:2026-06-01
> 状态:已与用户敲定决策,待 specify→plan→tasks→implement
> 前置:P1-P6 均已合 main;真 LLM 链路已跑通
> roadmap 出处:`docs/开发文档.md` §9.4.5 P7 行

## 1. 目标

让群聊感官闭环。4 个子项(其一 deferred):
- **@高亮**:消息文本里的 `@分析师` 等成员名渲染成高亮 chip。
- **silent 灰显气泡**:agent 选择沉默(silent)时显示灰色虚线小气泡「{name} 掠过」,
  而非完全不显示——让用户看到"它在场但这轮没话说"。
- **artifact diff 内联**:agent 更新已有 artifact(版本 ≥2)时,气泡内联显示「改了 Outline v2:{summary}」。
- **refine chips → prompt 模板**:把现有 5 个固定 refine chips 改为更灵活的常用 prompt 模板。

**非目标(本期不做)**:用户 `@<agent>` 进群单聊(需后端把用户 @ 路由到 worker,
可能触碰 P3 work-driver,deferred 到后续)。

## 2. 核心现状(已勘查)

- 前端 `apps/web-frontend/app/page.tsx`:群聊 UI 本体。消费**后端转好的 `chat.message`**
  (经 SSE `/api/tasks/{id}/events` 或 cluster session),`ChatMsg.kind ∈ {user,intro,result,
  system,error}`,**无 mentions / silent / intent / artifact 语义**。
- 后端 v2 事件(`agent.speak.mentions` / `AgentSilent.reason` / `ArtifactUpdate.version+
  delta_summary`)P1-P6 已有,但**没透传到 chat.message**。
- 前端**无任何测试框架**(package.json scripts 仅 dev/build/lint/start)。
- Python Playwright **已装**(`apps/web-backend` 依赖,video 录屏用)→ CDP 实测可复用。

## 3. 已敲定决策

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 范围 | @高亮 + silent 灰显 + artifact diff + refine→模板(不做用户@进群)|
| 2 | 验收 | Vitest + Testing Library 组件测试 + Python Playwright CDP 实测 |
| 3 | 字段透传 | **直接加 additive 字段**(无 flag;旧前端不读即无影响,向后兼容)|
| 4 | 前端测试框架 | **Vitest + Testing Library** |

## 4. 架构改动点

### 4.1 后端:chat.message 补 v2 语义字段(additive)
- 定位:chat.message 的生成处(`_emit_v2_step_overlay` 转 UI 消息 / pipeline chat 推送)。
- 加可选字段(不动现有 kind,旧前端忽略):
  - `mentions: list[str]`(@高亮)——来自 overlay 的 AgentSpeak.mentions。
  - `kind: "silent"` + `silent_reason: str`(灰显气泡)——AgentSilent 时也推一条 chat.message。
  - `artifact_delta: {id, version, summary}`(diff 内联)——ArtifactUpdate version≥2 时附带。
- **零回归**:additive,不改 user/intro/result/system/error 既有渲染。

### 4.2 前端:page.tsx ChatMsg 类型 + Bubble 渲染
- `ChatMsg` 加 `mentions? / silent_reason? / artifact_delta?` 可选字段;`kind` 加 `"silent"`。
- Bubble:
  - @高亮:`text` 经 `renderWithMentions()` 把 `@<成员名>` 包成 `<span class="mention">`。
  - silent 气泡:`kind==="silent"` → 灰色虚线小气泡分支(类似现有 bb-intro 但更淡)。
  - artifact diff:消息带 `artifact_delta` → 气泡内联一行「📝 改了 {id} v{version}:{summary}」。
- refine chips → 模板:把 5 个固定 chip 改为可编辑常用 prompt 模板(点击填入输入框而非直接 refine,
  或保留 refine 语义但文案模板化)。纯前端,复用现有 send/refine。

### 4.3 前端测试基建(Vitest)
- 新增 `vitest` + `@testing-library/react` + `jsdom` devDeps + `vitest.config.ts` + `test` script。
- 组件测试:`Bubble` 渲染 @高亮(mention chip 出现)、silent(灰显气泡 + reason)、
  artifact_delta(diff 行)。

### 4.4 CDP 实测(Python Playwright)
- 复用已装 Playwright。脚本起前端 dev(:3000)+ 后端(:8000/:8100),CDP 连浏览器,
  提交一个 task,截图确认 @高亮 / silent 气泡 / diff 行真实渲染。
- 放 `apps/web-backend/tests/` 或 `scripts/` 下;真 LLM task 偶发网络抖动属环境。

## 5. 回归策略

- 后端字段 additive(决策 3):旧前端不读新字段 → 行为不变。无 flag。
- 前端新渲染分支只在新字段存在时触发;旧消息(无 mentions/silent/delta)走原渲染。
- 后端 pytest 加字段断言(mentions/silent_reason/artifact_delta 正确填充)。

## 6. 测试计划(验收)

- 后端:pytest 断言 chat.message 透传字段(mentions / kind=silent+reason / artifact_delta)。
- 前端组件:Vitest + RTL 测 Bubble 三种新渲染。
- 前端构建:`next lint` + `next build` 不报错。
- CDP 实测:Playwright 连浏览器,真 task 截图确认三种 UX 渲染。

## 7. 风险

| 风险 | 对策 |
|---|---|
| 后端透传字段破坏现有 chat.message | additive 可选字段,旧渲染不读;pytest 断言旧字段不变 |
| 前端无测试基建,引入 Vitest 成本 | Vitest 轻量,一次性配好;只测纯 Bubble 渲染(无需起服务) |
| CDP 实测依赖前后端都起 + 真 LLM | Playwright 已装;真 LLM 抖动不计入 UX 渲染判定;截图为辅证,组件测试为主证 |
| @高亮误匹配(成员名是普通词) | 只匹配 `@` 前缀 + MEMBERS 精确名,非裸词 |

## 8. 不做 / 边界

- 不做用户 @<agent> 进群单聊(deferred)。
- 不改 v2 事件协议 / observer / reviewer / 并发(P1-P6 已定)。
- 不动 typed artifact schema / 导出。
- 宪章:无需修订(纯 UX + additive 字段,不改 Coordinator/Reviewer 职责,不引入 LLM 决策权;
  守原则 I 用户可见脱敏——@高亮显示的是中文成员名非 agent_id,silent/diff 文案业务化)。
