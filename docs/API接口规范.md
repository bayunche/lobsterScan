# API 接口规范

> 配套：`docs/开发文档.md` §6.3、§7.4

## 一、规约
- 所有 JSON 字段 `snake_case`
- 时间统一 ISO-8601 UTC（`2026-05-15T08:00:00Z`）
- 错误统一：
  ```jsonc
  {
    "error": {
      "code":        "INPUT_TOO_SHORT",       // 仅 admin / 日志可见
      "biz_message": "材料不足以生成汇报，请补充本周完成事项",
      "field":       "raw_text",
      "retryable":   true
    }
  }
  ```
- 前端展示只读 `biz_message`

## 二、业务应用 API（`/api`）

### 2.1 `POST /api/tasks` — 创建任务
请求：
```jsonc
{
  "report_type": "project_progress",          // 必填
  "title":       "本周项目进度汇报",
  "audience":    "直属领导",
  "duration":    "3分钟",                     // 1/3/5
  "style":       "简洁正式",
  "raw_text":    "…",                          // 至少 raw_text 或 file_ids 非空
  "file_ids":    ["file_xxx"],
  "user_hints":  { "industry": "互联网运营" }
}
```
响应 `200`：
```jsonc
{ "task_id": "tsk_…" }
```
错误：`INPUT_TOO_SHORT` / `UNSUPPORTED_FILE` / `RATE_LIMIT`

### 2.2 `GET /api/tasks/{task_id}` — 详情
```jsonc
{
  "task_id": "…",
  "status":  "running | partial | done | failed",
  "report_type": "…",
  "artifacts": {
    "summary":  { "ready": true,  "url": "/api/tasks/.../exports/summary" },
    "script":   { "ready": true,  "url": "/api/tasks/.../exports/script" },
    "slides":   { "ready": true,  "url": "/api/tasks/.../exports/html" },
    "video":    { "ready": false, "url": null },
    "review":   { "ready": true,  "url": "/api/tasks/.../exports/review" }
  },
  "review_summary": {
    "suggestions": ["…","…","…"],
    "quick_actions": ["shorter","more_problem","more_formal","more_result"],
    "estimated_duration": "2分20秒",
    "ai_signal_score": 0.18
  },
  "created_at": "…",
  "updated_at": "…"
}
```

### 2.3 `GET /api/tasks/{task_id}/events` — **SSE**
事件类型：

```
event: task.step
data: { "step": "html_design", "label": "正在生成 HTML 页面",
        "status": "running", "progress": 0.4 }

event: task.artifact
data: { "kind": "script", "ready": true,
        "url": "/api/tasks/.../exports/script" }

event: task.message
data: { "level": "warn",
        "text": "视频生成时间较长，将稍后再返回。" }

event: task.done
data: { "status": "done | partial | failed" }
```

- 心跳：每 15s 一条 `event: ping`
- 客户端断线自动重连用 `Last-Event-ID`

### 2.4 `POST /api/tasks/{task_id}/refine` — 快捷指令
```jsonc
{ "action": "shorter | more_problem | more_formal | more_result | regenerate_segment",
  "segment_id": "page_3"   // regenerate_segment 必填
}
```
响应：`{ "ok": true }`，新一轮事件继续走同一 SSE 通道

### 2.5 `POST /api/files` — 上传素材
- multipart/form-data：`task_id`（可选，未创建时给临时 id）、`file`
- 支持：`.txt .md .docx .xlsx .pdf`
- 响应：`{ "file_id": "file_…", "filename": "...", "size": 12345 }`

### 2.6 `GET /api/tasks/{task_id}/exports/{kind}`
- `kind` ∈ `summary | script | html | video | review`
- 返回头 `Content-Disposition: attachment; filename="本周项目进度汇报_20260515.<ext>"`
- `html` → `.zip`（含 `dist/`）；`video` → `.mp4`；其它 → `.md`

## 三、管理平台 API（`/admin/api`）

### 3.1 Agents

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/admin/api/agents` | `[ { id, display_name, status, provider, model, skill_count, last_active_at, success_rate_24h } ]` |
| GET | `/admin/api/agents/{id}` | 详情 |
| GET | `/admin/api/agents/{id}/files/{which}` | which: `soul`/`agents`/`user`；返回 `{ content, etag, updated_at }` |
| PUT | `/admin/api/agents/{id}/files/{which}` | `{ content, etag }`；写入前做 YAML frontmatter 校验，自动备份到 `.backups/` |
| POST | `/admin/api/agents/{id}/actions/reload` | 触发 `system.reload-agent` |

### 3.2 Skills

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/admin/api/skills/catalog?source=&category=&installed=` | 市场（含未安装） |
| GET | `/admin/api/skills/{name}` | SKILL.md + README + 版本列表 |
| POST | `/admin/api/skills/install` | `{ name, source, version, target_agents: ["html-designer"] }` |
| DELETE | `/admin/api/skills/{name}?target_agent=<id>` | 卸载 |

### 3.3 Bindings & Channels

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/admin/api/bindings` | 当前 `openclaw.json` 的 channels + bindings |
| PUT | `/admin/api/bindings` | 写入 + 调用 `openclaw gateway reload` |
| GET | `/admin/api/channels/{name}/probe` | 探测连通 |

### 3.4 Sessions

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/admin/api/sessions?agent_id=&limit=&before=` | 列表 |
| GET | `/admin/api/sessions/{id}/messages?limit=&before=` | 时间线（脱敏：去掉真实 task_id 之外的所有内部 ID） |

### 3.5 Pipelines

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/admin/api/pipelines?status=&limit=` | TaskRun 列表（含汇总指标） |
| GET | `/admin/api/pipelines/{task_id}` | 步骤详情，每步含 `input_ref` `output_ref` `error` `duration_ms` |
| GET | `/admin/api/pipelines/{task_id}/steps/{step}/input` | 拉对应 JSON |

### 3.6 Health / Config / Templates

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/admin/api/health` | `{ gateway, agents[], providers[], queues[] }` |
| GET | `/admin/api/config` | Provider / TTS / 视频参数 |
| PUT | `/admin/api/config` | 修改后广播 `system.reload-provider` 等 |
| GET | `/admin/api/templates/{kind}` | kind ∈ `report-structures` / `html-themes` / `avatars` |
| PUT | `/admin/api/templates/{kind}/{id}` | upsert |

## 四、错误码字典（节选，仅 admin / 日志）

| code | biz_message（中文） | retryable |
| --- | --- | --- |
| `INPUT_TOO_SHORT` | 材料不足以生成汇报，请补充本周完成事项 | true |
| `UNSUPPORTED_FILE` | 文件内容识别不完整，建议复制关键内容到文本框后重新生成 | false |
| `MODEL_TIMEOUT` | 生成时间较长，请稍后重试，或减少输入材料后再次生成 | true |
| `HEYGEN_QUOTA` | 视频生成额度紧张，已为您先返回讲稿与 HTML | true |
| `VIDEO_PIPELINE_FAILED` | 视频生成未成功，可点击重试 | true |
| `AGENT_UNREACHABLE` | 系统忙，请稍后重试 | true |

