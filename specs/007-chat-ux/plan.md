# Implementation Plan: P7 — 群聊 UX(@高亮 + silent 灰显 + artifact diff + prompt 模板)

**Branch**: `007-chat-ux` | **Date**: 2026-06-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-chat-ux/spec.md`
设计文档:`docs/superpowers/specs/2026-06-01-p7-chat-ux-design.md`

## Summary

后端在 chat.message 工厂(`_chat_msg`)加 additive 可选字段(mentions / silent_reason /
artifact_delta)并在 AgentSilent 时推一条 silent 消息;前端 page.tsx 的 Bubble 渲染 @高亮、
silent 灰显气泡、artifact diff 内联,并把 refine chips 改为可填入的 prompt 模板。新增 Vitest
前端组件测试基建 + Python Playwright CDP 实测。additive 字段保证零回归。

## Technical Context

**Language/Version**: Python 3.12(web-backend)+ TypeScript/React(Next.js 14,web-frontend)

**Primary Dependencies**: 既有 FastAPI / Next.js / React。新增前端 devDep:vitest +
@testing-library/react + jsdom。CDP 复用已装 Python playwright。

**Storage**: N/A(chat.jsonl 落盘已存在;新字段随 msg 一起落,additive)

**Testing**: 后端 pytest(字段透传断言)+ 前端 Vitest+RTL(Bubble 渲染)+ Playwright CDP(实测)

**Target Platform**: web(:3000 前端 / :8000 后端);Windows 开发机

**Project Type**: web(frontend + backend,首个跨前后端的 spec feature)

**Performance Goals**: 无(纯 UX 渲染)

**Constraints**:
- additive 字段,旧前端忽略 → 零回归(SC-005)
- 守宪章 I 脱敏:@高亮显示中文名、diff 显示产物友好名,不暴露 agent_id / artifact id
- 不改 v2 事件协议 / observer / reviewer / 并发(P1-P6)
- 不动 typed artifact schema / 导出

**Scale/Scope**: 后端 `_chat_msg` + 几处 _broadcast 调用 + AgentSilent→chat 推送;前端
page.tsx Bubble + ChatMsg 类型 + refine 区;新增 vitest 配置 + 测试 + CDP 脚本。

## Constitution Check

*GATE: 通过。P7 无需宪章修订。*

| 原则 | 影响 | 判定 |
|---|---|---|
| I 用户可见脱敏 | @高亮显示中文成员名(非 agent_id);diff 显示产物友好名(非 artifact id);silent reason 业务化 | ✅ 强相关,设计已守 |
| II 中文产品语言 | 所有新 UX 文案中文(掠过 / 改了 / 产物名)| ✅ |
| III 降级而非崩溃 | additive 字段缺失 → 前端走原渲染;silent/diff 字段空 → 省略不报错 | ✅ |
| IV Coordinator/Reviewer 边界 | 不改决策逻辑;仅透传已有 v2 语义到 UI | ✅ |
| V Agent 自治与隔离 | 不涉及 | ✅ |

工程约束:CORS 双写不动;设计语言 SSOT(tokens.css)—— 新样式复用 CSS 变量。
→ 无 Complexity Tracking。

## Project Structure

### Documentation (this feature)

```text
specs/007-chat-ux/
├── plan.md / research.md / data-model.md / quickstart.md / spec.md / tasks.md
```
contracts/ 跳过:chat.message 是内部 SSE 消息,additive 字段在 data-model 描述即可,无对外 API 契约变化。

### Source Code(跨前后端)

```text
apps/web-backend/app/orchestrator/pipeline.py
├── _chat_msg(...) 加 additive 可选参数 mentions/silent_reason/artifact_delta       # FR-001/003/004
├── AgentSilent 路径 / silent 语义处 → _broadcast 一条 kind=silent chat.message      # FR-002
└── result_msg(带 artifact)生成处 → 附 artifact_delta(version≥2)                  # FR-003

apps/web-frontend/
├── app/page.tsx
│   ├── ChatMsg 类型加 mentions? / silent_reason? / artifact_delta?;kind 加 "silent"  # FR-005-008
│   ├── renderWithMentions(text):@<成员名> → 高亮 chip                               # FR-005
│   ├── Bubble:kind==="silent" → 灰显气泡分支                                         # FR-006
│   ├── Bubble:artifact_delta(version≥2)→ 内联 diff 行                              # FR-007
│   └── refine chips → prompt 模板(点击填入输入框)                                   # FR-009/010
├── package.json:加 test script + vitest/@testing-library/jsdom devDeps
├── vitest.config.ts(新增)
└── __tests__/Bubble.test.tsx(新增,组件测试)

scripts/ 或 apps/web-backend/tests/
└── p7_cdp_smoke.py(新增,Playwright CDP 实测截图)
```

**Structure Decision**: 跨前后端但改动集中。后端 `_chat_msg` 是单一工厂,加可选参数即覆盖
FR-001/003;silent 推送是新增一条 _broadcast。前端集中在 page.tsx Bubble。additive → 零回归。

## Phases

- **Phase 0**(research.md):5 决策(_chat_msg additive 改法 / silent 推送落点 /
  artifact_delta 来源 / @高亮正则 / Vitest+CDP 验收分工)。
- **Phase 1**(data-model.md / quickstart.md):chat.message 扩展字段 + prompt 模板实体;
  组件测试 + CDP quickstart。
- **Phase 2**(tasks.md):按 US1→US2→US3→US4 拆 + 测试基建。

## Complexity Tracking

> 无违规,无需填写。
