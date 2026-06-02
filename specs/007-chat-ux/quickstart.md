# Quickstart: P7 — 群聊 UX 验证

## 前置
- P1-P6 已合 main。在 `007-chat-ux` 分支。

## §1 · 后端字段透传(pytest)

```bash
uv run --project apps/web-backend pytest apps/web-backend/tests -q
# 新增断言:_chat_msg 传 mentions/silent_reason/artifact_delta → payload 含;不传 → 不含。
# 零回归:现有 chat.message 测试字段不变。
```

## §2 · 前端组件测试(Vitest)

```bash
# 首次:装 devDeps(vitest @testing-library/react @testing-library/jest-dom jsdom)
pnpm --filter web-frontend install
pnpm --filter web-frontend test
# 测 Bubble:@高亮 chip 出现 / silent 灰显气泡含 reason / artifact_delta(v≥2)diff 行出现、v1 不出现。
```

## §3 · 前端构建不回归

```bash
pnpm --filter web-frontend lint
pnpm --filter web-frontend build    # 或 ./node_modules/.bin/next build
```

## §4 · CDP 浏览器实测(Playwright)

```bash
# 起前后端
set -a; source .env; set +a
export OPENCLAW_BIN="$(pwd)/node_modules/.bin/openclaw"
( uv run --project apps/admin-backend uvicorn app.main:app --host 127.0.0.1 --port 8100 >data/.logs/admin-backend.log 2>&1 & )
( uv run --project apps/web-backend  uvicorn app.main:app --host 127.0.0.1 --port 8000 >data/.logs/web-backend.log 2>&1 & )
( pnpm --filter web-frontend dev >data/.logs/web-frontend.log 2>&1 & )
sleep 15
# 跑 CDP smoke:连浏览器 → 打开 /?task=<真实id> → 截图确认 @高亮/silent 气泡/diff 行
uv run --project apps/web-backend python scripts/p7_cdp_smoke.py <task_id>
```

**验收断言**:
- 组件测试全绿(SC-006 主证)。
- CDP 截图:@分析师 高亮可见、silent「掠过」气泡可见、artifact diff 行可见(SC-006 辅证)。
- 截图中无 agent_id / artifact 内部 id 泄漏(SC-004)。

## §5 · 零回归(SC-005)

```bash
# 后端:不传新 kw 的 _chat_msg 调用 → 字段与主干一致
# 前端:无新字段的旧消息 → Bubble 原渲染(组件测试覆盖旧消息 case)
uv run --project apps/web-backend pytest apps/web-backend/tests -q   # 全绿
```

## 注意
- 真 LLM task 偶发 deepseek 网络抖动属环境,不计入 UX 渲染判定;CDP 也可用历史已完成 task
  的 chat 回放(/api/tasks/{id}/chat)看渲染,不强依赖新跑真 LLM。
- @高亮只匹配 9 位成员中文名,裸 @词 不高亮。
