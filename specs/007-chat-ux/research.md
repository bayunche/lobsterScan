# Research: P7 — 群聊 UX

Phase 0 技术决策。spec 无 NEEDS_CLARIFICATION(决策已 brainstorm 敲定);本文记录实现层 5 决策。

## 决策 1 · _chat_msg additive 字段改法

- **Decision**: `_chat_msg(agent, text, kind="result", *, mentions=None, silent_reason=None,
  artifact_delta=None)` 加 3 个 keyword-only 可选参数;非 None 时才写入 payload dict
  (不污染旧消息)。
- **Rationale**: FR-001/003/004。keyword-only + 默认 None → 所有现有 `_chat_msg(agent,text)`
  /  `_chat_msg(agent,text,kind)` 调用完全不变(零回归);只有需要的几处显式传新参数。
- **Alternatives**: 改 ChatMsg 为 dataclass —— 改动面大,现有是 dict 直接 _broadcast,否。
- **现状核实**: `_chat_msg`(pipeline.py:712)返回 dict,字段 id/agent/display_name/avatar/
  ts/kind/text。加字段 additive,前端 ChatMsg 类型同步加可选。

## 决策 2 · silent 消息推送落点

- **Decision**: v2 路径 AgentSilent emit 处(overlay 的 silent 分支 / harness silent),
  额外 `_broadcast(run, "chat.message", _chat_msg(agent, "", kind="silent",
  silent_reason=reason))`。文本可空(前端用 silent_reason 渲染)。
- **Rationale**: FR-002。让前端看到 silent。复用现有 _broadcast + _chat_msg。
- **Alternatives**: 前端直接订阅 v2 agent.silent 事件 —— 前端目前只消费 chat.message,
  不解析 v2 原始事件,统一走 chat.message 更一致。否。
- **派生发现**: silent 推送应仅 v2 路径(is_v2 / envelope 下 silent 才有语义);v1 无 silent
  概念,不推。需在 silent emit 已有的 is_v2 守卫内加(不新增 v1 行为)。

## 决策 3 · artifact_delta 来源(version≥2)

- **Decision**: result_msg(step 产物对应核心 artifact)生成时,若该 artifact 版本 ≥2,
  附 `artifact_delta={id: 中文友好名, version: N, summary: delta_summary}`。版本/summary 来自
  ArtifactUpdate(write_versioned 已产 version + delta_summary)。
- **Rationale**: FR-003/007。首版(version 1)不附(不是"改")。id 用中文友好名(脱敏,FR-008)。
- **Alternatives**: 前端自己比对版本 —— 前端无版本历史,后端透传最直接。否。
- **现状核实**: write_versioned 已有 version + delta_summary(P1)。artifact 中文名映射:
  MaterialPool→素材池 / ReportCore→重点 / Outline→大纲 / Script→讲稿(新增小映射表)。

## 决策 4 · @高亮匹配规则

- **Decision**: 前端 `renderWithMentions(text)`:正则匹配 `@` + MEMBERS 中任一精确中文名
  (9 个),命中包成高亮 `<span>`。非成员名的 `@xxx` 不匹配。
- **Rationale**: FR-005/008。MEMBERS 中文名已在 page.tsx 定义,直接复用。精确匹配避免误高亮。
- **Alternatives**: 用后端 mentions 字段定位 —— mentions 是 agent_id,需映射回中文名再在
  text 找;直接按中文名正则更简单且 text 里本就是中文名。mentions 字段作辅助校验
  (US1-AC4)。两者结合:正则高亮为主,mentions 作数据佐证。
- **派生发现**: 正则需转义 + 按名字长度降序匹配(避免短名先匹配吃掉长名),9 个名字无包含
  关系(资料员/分析师/结构师/表达教练/文书/设计师/视频制作/质量检查员/汇报总控),无此问题。

## 决策 5 · 验收分工(Vitest 组件测试 + Playwright CDP)

- **Decision**:
  - 后端:pytest 断言 `_chat_msg` 透传字段(mentions/silent_reason/artifact_delta 正确填充
    + 不传时不出现)。
  - 前端组件:Vitest + @testing-library/react + jsdom,测 Bubble 三种新渲染(@高亮 chip 出现
    / silent 气泡含 reason / diff 行 version≥2 出现、version1 不出现)。
  - CDP 实测:Python Playwright(已装)起前后端,连浏览器,提交 task,截图确认三种 UX。
    作辅证(真 LLM 抖动不计入);组件测试为主证。
- **Rationale**: SC-006。组件测试快且确定(无需起服务);CDP 实测验证真实集成 + 视觉。
- **Alternatives**: 只 CDP —— 无细粒度组件断言,且依赖真 LLM 不稳;只组件测试 —— 缺真实
  集成验证。两者结合最稳。
- **现状核实**: 前端 package.json 仅 dev/build/lint/start,无测试;需加 vitest devDeps +
  config + test script。Python playwright 已装(web-backend 依赖)。

## 综合:改动面与零回归

- 后端:`_chat_msg` 加 3 keyword-only 可选参数 + silent 推送(is_v2 守卫内)+ result_msg
  附 artifact_delta。现有调用零改动。
- 前端:page.tsx ChatMsg 类型 + Bubble 渲染 + refine 模板;新增 vitest 基建。
- additive → 旧消息/旧前端行为不变(SC-005 零回归)。
- 不动:v2 协议 / observer / reviewer / 并发 / typed schema / 导出。
