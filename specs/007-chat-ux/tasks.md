# Tasks: P7 — 群聊 UX(@高亮 + silent 灰显 + artifact diff + prompt 模板)

**Feature**: `007-chat-ux` | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

验收基线(spec SC):后端字段透传 pytest + 前端 Vitest 组件测试 + next build 不回归
+ CDP 浏览器实测截图(SC-006);additive 字段零回归(SC-005)。
跨前后端:后端 `apps/web-backend/app/orchestrator/pipeline.py` + 前端 `apps/web-frontend/`。

测试请求:spec SC-006 明确要求组件测试 + CDP 实测,故本清单**含测试 + 测试基建任务**。

---

## Phase 1: Setup(前端测试基建)

- [X] T001 在 `apps/web-frontend/package.json` 加 devDeps:`vitest` / `@testing-library/react` / `@testing-library/jest-dom` / `jsdom` / `@vitejs/plugin-react`,加 `"test": "vitest run"` script;`pnpm --filter web-frontend install`
- [X] T002 新增 `apps/web-frontend/vitest.config.ts`(environment: jsdom,plugin-react,setupFiles 引 @testing-library/jest-dom)+ `apps/web-frontend/vitest.setup.ts`

---

## Phase 2: Foundational(后端 additive 字段 — US1/US2/US3 共同前提)

**目标**:`_chat_msg` 支持透传新字段,前端 ChatMsg 类型扩展。两端类型就位后各 US 才能渲染。

- [X] T003 改 `pipeline.py` 的 `_chat_msg`(line ~712):加 keyword-only 可选参数 `mentions=None / silent_reason=None / artifact_delta=None`,非 None 才写入 payload(additive,现有调用零改动)(FR-001/003/004,research 决策 1)
- [X] T004 [P] 改 `apps/web-frontend/app/page.tsx` 的 `ChatMsg` 类型:加 `mentions?: string[]` / `silent_reason?: string` / `artifact_delta?: {id;version;summary}`,`kind` 加 `"silent"`(data-model §2)

---

## Phase 3: User Story 1 — @提及高亮(Priority: P1)🎯 MVP

**Goal**: 消息文本里 `@<成员中文名>` 渲染为高亮 chip。
**Independent Test**: 含 @分析师 的消息 → 高亮;裸 @词 不高亮;多 @ 各自高亮。

- [X] T005 [US1] 在 `page.tsx` 新增 `renderWithMentions(text)`:按 MEMBERS 9 个中文名正则匹配 `@<名>` → 包高亮 `<span className="mention">`;非成员名不匹配(FR-005,research 决策 4)
- [X] T006 [US1] 在 `page.tsx` Bubble 的正文渲染处用 `renderWithMentions` 替代裸 text(仅 agent 消息正文;user/system 不变)+ 加 `.mention` 高亮样式(复用 tokens.css 变量,脱敏:显示中文名,FR-008)
- [X] T007 [P] [US1] 新增 `apps/web-frontend/__tests__/Bubble.test.tsx`:测 @分析师 → 渲染含 mention chip(US1-AC1);@设计师 @视频制作 → 两个 chip(US1-AC2);@不存在名 → 无 chip(US1-AC3)

**Checkpoint**: US1 可独立验收 —— @高亮工作。

---

## Phase 4: User Story 2 — silent 灰显气泡(Priority: P2)

**Goal**: silent 成员显示灰色「掠过」小气泡。
**Independent Test**: 后端推 kind=silent + reason → 灰显气泡含成员名 + 理由;普通消息不受影响。

- [X] T008 [US2] 改 `pipeline.py`:v2 路径 AgentSilent emit 处(is_v2 守卫内)额外 `_broadcast(run, "chat.message", _chat_msg(agent, "", kind="silent", silent_reason=reason))`(FR-002,research 决策 2;仅 v2,v1 不推)
- [X] T009 [US2] 在 `page.tsx` Bubble 加 `kind==="silent"` 分支:灰色虚线小气泡「{display_name} 掠过{· reason}」,比正常气泡淡(复用 bb-intro 风格弱化)(FR-006/008)
- [X] T010 [P] [US2] 在 Bubble.test.tsx 加 silent 用例:kind=silent + silent_reason → 渲染含「掠过」+ 成员名 + 理由(US2-AC1);无 reason → 仅「{名} 掠过」不报错(edge case)
- [X] T011 [P] [US2] 后端 pytest:新增/扩 `apps/web-backend/tests/` 断言 `_chat_msg(agent,"",kind="silent",silent_reason="x")` payload 含 kind=silent + silent_reason(FR-002)

**Checkpoint**: US2 可独立验收 —— silent 气泡可见。

---

## Phase 5: User Story 3 — artifact diff 内联(Priority: P2)

**Goal**: 更新已有产物(version≥2)时气泡内联「📝 改了 {产物名} 第 N 版:{摘要}」。
**Independent Test**: 后端推带 artifact_delta(v≥2)→ diff 行出现;v1 不出现。

- [X] T012 [US3] 在 `pipeline.py` 加 artifact 中文友好名映射(MaterialPool→素材池 / ReportCore→重点 / Outline→大纲 / Script→讲稿);result_msg 生成处,若对应核心 artifact version≥2 → 附 `artifact_delta={id:中文名, version, summary:delta_summary}`(FR-003/007/008,research 决策 3)
- [X] T013 [US3] 在 `page.tsx` Bubble 加 `artifact_delta` 内联行渲染:「📝 改了 {id} 第 {version} 版{:summary}」;无 artifact_delta 不显示(FR-007)
- [X] T014 [P] [US3] 在 Bubble.test.tsx 加 diff 用例:artifact_delta version=2 → diff 行出现含产物名/版本/摘要(US3-AC1);version=1 的消息(或无 artifact_delta)→ 无 diff 行(US3-AC2)
- [X] T015 [P] [US3] 后端 pytest:断言 result_msg 对 version≥2 核心 artifact 附 artifact_delta(中文友好名)、version1 不附(FR-003)

**Checkpoint**: US3 可独立验收 —— diff 行可见。

---

## Phase 6: User Story 4 — refine chips 改 prompt 模板(Priority: P3)

**Goal**: 快捷调整区改为可填入输入框的常用 prompt 模板。
**Independent Test**: 完成态显示模板;点击填入输入框(可编辑)而非直接执行。

- [X] T016 [US4] 改 `page.tsx` refine-chips 区:5 个固定 refine chip 改为 prompt 模板按钮(data-model §5 文案);点击 `setDraft(模板文本)` 填入输入框(替代直接 `refine(action)`),用户编辑后走现有 send;仅 done/partial 态显示(FR-009/010)
- [X] T017 [P] [US4] 在 Bubble.test.tsx 或新增 page 交互测试:点击模板 → 输入框被填入对应文本(US4-AC2)

**Checkpoint**: US4 可独立验收 —— 模板填入工作。

---

## Phase 7: 验收 & Polish

- [X] T018 前端组件测试全绿:`pnpm --filter web-frontend test`(Bubble @高亮/silent/diff/模板 全过,SC-006 主证)
- [X] T019 前端不回归:`pnpm --filter web-frontend lint` + `next build` 不报错
- [X] T020 后端零回归:`uv run --project apps/web-backend pytest apps/web-backend/tests -q` 全绿(additive 不破 P1-P6,SC-005)
- [~] T021 (deferred·环境网络阻 Chromium 下载) CDP 浏览器实测:新增 `scripts/p7_cdp_smoke.py`(Playwright 连浏览器,打开 /?task=<真实id> 或历史 task chat 回放,截图确认 @高亮/silent 气泡/diff 行 + 无 id 泄漏,SC-006 辅证)
- [X] T022 [P] 更新 `docs/开发文档.md` §9.4.5 P7 行「已落地」+ 实现位置
- [X] T023 [P] 更新 CLAUDE.md SPECKIT 块:P7 Planning→Implemented + Code/Tests 位置
- [X] T024 终轮全绿 + commit「P7 全栈落地」

---

## Dependencies(完成顺序)

```
Setup(T001-T002)  前端测试基建
  └─> Foundational(T003-T004)  后端 _chat_msg additive + 前端 ChatMsg 类型
        ├─> US1(T005-T007)  P1 🎯MVP  @高亮(纯前端,只依赖 ChatMsg 类型)
        ├─> US2(T008-T011)  P2        silent(后端推送 + 前端气泡)
        ├─> US3(T012-T015)  P2        artifact diff(后端附字段 + 前端行)
        └─> US4(T016-T017)  P3        prompt 模板(纯前端,独立)
              └─> 验收&Polish(T018-T024)
```

**关键点**:US1/US4 纯前端,只依赖 T004 ChatMsg 类型;US2/US3 需后端透传(T008/T012)+ 前端渲染。
四个 US 改 page.tsx 不同分支,但同文件 → 实现时注意顺序(同文件串行编辑)。

## Parallel 机会

- T004(前端类型)与 T003(后端)不同端 → [P]。
- 各 US 的测试任务(T007/T010/T011/T014/T015/T017)多独立 → [P]。
- Polish T022/T023 不同文件 → [P]。

## Implementation Strategy

- **MVP = US1**(T001-T007):@高亮纯前端,只依赖 ChatMsg 类型扩展,最快见效。
- **增量 2 = US2+US3**(T008-T015):后端透传 silent/diff + 前端渲染。
- **增量 3 = US4**(T016-T017):prompt 模板,纯前端独立。
- additive 字段全程零回归;前端旧消息走原渲染。

## 总计

- 任务数:24(Setup 2 + Foundational 2 + US1 3 + US2 4 + US3 4 + US4 2 + 验收&Polish 7)
- 测试任务:6(T007/T010/T011/T014/T015/T017)+ 验收 T018-T021
- 跨前后端首个 feature:后端 pytest + 前端 Vitest + CDP 实测

## 验收结论(诚实记录)

| SC | 结果 |
|---|---|
| SC-001/005 零回归 | ✅ 后端 141 passed;additive 字段旧前端忽略;next build 通过 |
| SC-002/006 组件测试 | ✅ Bubble.test 11 绿(@高亮 5 + silent 2 + diff 2 + 模板 2)+ test_p7_chat_fields 7 绿 |
| SC-004 脱敏 | ✅ @高亮显示中文名、diff 用 ARTIFACT_DISPLAY 中文友好名,无 agent_id/artifact id |
| SC-006 CDP 实测 | ⚠️ deferred:`scripts/p7_cdp_smoke.py` 就位,但 Playwright Chromium 下载受环境网络阻(同 deepseek/浏览器下载),无法在当前环境跑通。主证(Vitest 组件测试 + next build)已覆盖渲染正确性;CDP 为视觉辅证,网络恢复后可跑 |

实现关键转变:Bubble 从 app/page.tsx 抽到 `components/Bubble.tsx`(Next.js page 文件不允许
default 以外的 named export,否则 build 失败 —— 真实约束,抽出后既合规又利于组件测试 import)。
