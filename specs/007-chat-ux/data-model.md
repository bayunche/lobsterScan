# Data Model: P7 — 群聊 UX

不新增持久化实体、不改 typed artifact schema。以下是 chat.message 扩展 + 前端实体。

## 1. chat.message 扩展字段(additive · 后端 _chat_msg 产出)

现有字段:id / agent / display_name / avatar / ts / kind / text(+ analysis / artifacts /
attachments / report_meta / task_id 等已有可选)。P7 新增 3 个 additive 可选字段:

| 字段 | 类型 | 何时出现 | 用途 |
|---|---|---|---|
| mentions | list[str] | speak 消息点名下游时 | @高亮数据佐证(agent_id 列表)|
| silent_reason | str | kind=="silent" 时 | silent 灰显气泡的理由文案 |
| artifact_delta | {id, version, summary} | 更新已有 artifact(version≥2)时 | diff 内联行 |

- `kind` 扩展取值:新增 `"silent"`(原 user/intro/result/system/error 不变)。
- artifact_delta.id:**中文友好名**(素材池/重点/大纲/讲稿),非内部 artifact id(脱敏 FR-008)。
- 全部 additive:不传则字段不出现,旧前端忽略 → 零回归(FR-004/011)。

**校验**:
- silent 消息 text 可空(前端用 silent_reason 渲染);silent_reason 空 → 前端省略理由。
- artifact_delta 仅 version≥2 附;version1(首产)不附。
- summary 空 → 前端 diff 行省略摘要部分。

## 2. 前端 ChatMsg 类型(扩展)

```ts
type ArtifactDelta = { id: string; version: number; summary: string };
type ChatMsg = {
  // ... 现有字段 ...
  kind: "user" | "intro" | "result" | "system" | "error" | "silent";  // +silent
  mentions?: string[];          // @高亮佐证
  silent_reason?: string;       // silent 气泡理由
  artifact_delta?: ArtifactDelta;  // diff 行
};
```

## 3. 前端渲染分支(Bubble)

```
kind==="silent"        → 灰显「掠过」小气泡(成员名 + silent_reason)
kind 其他 + text        → renderWithMentions(text):@<成员名> 包高亮 chip
artifact_delta(v≥2)    → 气泡内联「📝 改了 {id} 第 {version} 版:{summary}」
```

## 4. @高亮匹配(renderWithMentions)

| 规则 | 说明 |
|---|---|
| 匹配源 | MEMBERS 9 个中文名(page.tsx 已定义) |
| 匹配模式 | `@` + 精确成员中文名 |
| 不匹配 | `@` + 非成员名(裸词)→ 原样不高亮 |
| 多 @ | 各自独立高亮 |

## 5. prompt 模板(US4 · 纯前端)

| 现 refine action | → prompt 模板文本(填入输入框,可编辑) |
|---|---|
| shorter | 「把讲稿再压缩 30%,保留核心结论」 |
| more_problem | 「更突出风险和问题,说清影响」 |
| more_formal | 「语气更正式书面一些」 |
| more_result | 「强化已落地成果的量化数据」 |
| regenerate_segment | 「重新生成讲稿」 |

- 点击 → 文本填入输入框(setDraft),用户编辑后走现有 send;不直接固定执行(FR-010)。
- 仅任务完成态(done/partial)显示(同现状)。

## 零回归不变量(SC-005)

```
旧消息(无 mentions/silent_reason/artifact_delta)→ Bubble 走原渲染路径
后端 _chat_msg 现有调用(不传新 kw 参数)→ payload 与改造前逐字段一致
silent 推送仅 is_v2 路径(v1 无 silent 概念,不新增 v1 行为)
```
