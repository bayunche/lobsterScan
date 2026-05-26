---
name: heygen
description: HeyGen V2 数字人视频生成。avatar / talking_photo + 文本 TTS + 自定义背景 → mp4 落盘。子命令 avatars / voices / video / account。Agent 通过 Bash 调 node 脚本,API key 走环境变量 HEYGEN_API_KEY。
version: 0.1
allowed-tools: [Bash, Read, Write]
---

# HeyGen V2 · 数字人视频生成

> 官方 doc:https://docs.heygen.com/reference/create-an-avatar-video-v2
> 本 skill 直接调 HeyGen V2 REST,不走 Membrane / clawhub 包,无第三方依赖。

## 何时触发
**所有 HeyGen 调用都走本 skill。** 不要自己拼 HTTP。

适合场景:
- 汇报材料数字人讲解开场镜头(5~60 秒,英文 / 中文 / 多语)
- PPT 配文字 → 数字人 lip-sync(HeyGen 强项 — 多语种嘴型对齐)
- 真人照片 → talking_photo(老照片或自定义形象上传后驱动)

**不合适场景**:
- 纯文字 → 风景视频(用 minimax-video 或 klingai)
- 多镜头分镜(HeyGen v2 支持 1-50 个 scene,但本 skill MVP 只暴露单 scene;多镜头自行手拼 video_inputs)

## 鉴权
- env `HEYGEN_API_KEY`(必填,业务后端通过 AGENT_ENV_MAP 注入)
- env `HEYGEN_API_BASE`(可选,默认 `https://api.heygen.com`)
- header `x-api-key: <key>`(注意全小写)

## 调用方式

### 1) 查 avatar / voice(本地过滤,不重复调 API)
```bash
# 找一个中文女声
node scripts/heygen.mjs voices list --language Chinese --gender female --limit 10

# 找一个 Premium 数字人
node scripts/heygen.mjs avatars list --gender female --premium true --limit 10

# 按 name 模糊匹配
node scripts/heygen.mjs avatars list --name "Abigail"
```

返回 JSON 含:`avatar_id` / `voice_id` / `default_voice_id`(每个 avatar 自带推荐音) — 选 ID 给后续 `video generate`。

### 2) 生成视频(标准 t2v · text 模式)
```bash
node scripts/heygen.mjs video generate \
  --avatar_id   "Abigail_expressive_2024112501" \
  --voice_id    "1bd001e7e50f421d891986aae5b3afe1" \
  --text        "大家好,欢迎参加本次汇报。我将带大家梳理本季度的核心成果。" \
  --speed       1.0 \
  --emotion     "Friendly" \
  --background_color "#FFFFFF" \
  --width 1920 --height 1080 \
  --output_dir  data/outputs/<task_id>/video
```

默认行为:**提交 → 轮询(最长 10 分钟)→ 下载到 `output_dir/<video_id>.mp4`**。

加 `--no_wait` 只拿 video_id,后续用 `video wait --video_id ... --download` 异步取。

### 3) talking_photo(真人照片驱动)
```bash
node scripts/heygen.mjs video generate \
  --talking_photo_id "6013fc758b5446a2ba17d8c459538bb4" \
  --voice_id "..." --text "..." \
  --output_dir data/outputs/<task_id>/video
```

### 4) 查状态 / 等待 / 下载
```bash
# 单次查
node scripts/heygen.mjs video status --video_id "<id>"

# 轮询到完成,只拿 URL 不下载
node scripts/heygen.mjs video wait --video_id "<id>"

# 轮询到完成,直接下载
node scripts/heygen.mjs video wait --video_id "<id>" --download --output_dir ./video
```

### 5) 凭据 ping
```bash
node scripts/heygen.mjs account check
# → {"ok":true,"source":"env","voices_count":1523}
```
调一次 list-voices 当 ping(轻量只读,不计费)。

## 完整 flag 表(video generate)

| Flag | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--avatar_id` | string | — | 跟 `--talking_photo_id` 二选一 |
| `--talking_photo_id` | string | — | 真人照片驱动 |
| `--voice_id` | string | — | text 模式必填 |
| `--text` / `--input_text` | string | — | 讲稿(text 模式必填) |
| `--audio_url` | url | — | 自带音频(替代 text 模式) |
| `--audio_asset_id` | string | — | 已上传的 HeyGen asset id |
| `--silence_seconds` | float | — | 静音段落 1.0–100.0 |
| `--speed` | float | 1 | 0.5–1.5 |
| `--pitch` | int | 0 | -50–50 |
| `--emotion` | enum | — | Excited / Friendly / Serious / Soothing / Broadcaster |
| `--locale` | string | — | 如 en-US / zh-CN |
| `--background_color` | hex | #FFFFFF | 纯色背景 |
| `--background_image_url` | url | — | 替代色背景 |
| `--background_video_url` | url | — | 视频背景 |
| `--background_fit` | enum | cover | crop / cover / contain / none |
| `--background_play_style` | enum | loop | freeze / loop / fit_to_scene(仅 video bg) |
| `--width` | int | 1920 | 输出宽 |
| `--height` | int | 1080 | 输出高 |
| `--scale` | float | 1 | character 缩放 0.0–5.0 |
| `--avatar_style` | enum | normal | normal / circle / closeUp |
| `--talking_photo_style` | enum | — | circle |
| `--matting` | bool | false | 真人照片抠图 |
| `--expression` | enum | — | default / happy |
| `--talking_style` | enum | — | stable / expressive |
| `--use_avatar_iv` | bool | false | Avatar IV(更自然动态)|
| `--motion_prompt` | string | — | Avatar IV 的动作 prompt |
| `--caption` | bool | false | 自动字幕 |
| `--title` | string | — | 视频元数据标题 |
| `--callback_id` | string | — | webhook tracking id |
| `--callback_url` | url | — | 完成后回调(替代轮询) |
| `--output_dir` | path | ./output | 落盘目录 |
| `--no_wait` | bool | false | 只提交不轮询 |
| `--no_download` | bool | false | 轮询到完成只返 URL 不下载 |

## 输出 JSON Schema

**成功(`video generate` + 默认下载)**:
```json
{
  "ok": true,
  "video_id": "af273759c9xa47369e05418c69drq174",
  "status": "completed",
  "path": "data/outputs/<task>/video/<video_id>.mp4",
  "bytes": 1234567,
  "duration": 12.5,
  "thumbnail_url": "https://..."
}
```

**异步模式(`--no_wait`)**:
```json
{"ok": true, "video_id": "<id>", "wait": false}
```

**失败 — 参数校验**:
```json
{"ok": false, "error": "missing_avatar_or_talking_photo_id", "hint": "..."}
{"ok": false, "error": "missing_voice_id", "hint": "..."}
{"ok": false, "error": "missing_voice_input", "hint": "..."}
```

**失败 — HeyGen API**:
```json
{"ok": false, "error": "heygen_api", "op": "generate_video", "code": "invalid_parameter", "message": "..."}
{"ok": false, "error": "heygen_fail", "video_id": "...", "heygen_error": {"code": 40119, "message": "Video is too long"}}
{"ok": false, "error": "timeout", "video_id": "..."}
{"ok": false, "error": "download_failed", "detail": "...", "video_id": "..."}
```

## 退出码
- `0` 成功
- `1` 通用错误(参数 / API / 下载)
- `2` 鉴权失败(`no_credentials`)
- `3` 配额耗尽
- `4` 轮询超时

## 重要规则
- **video_url 7 天过期** — 务必本次 run 内 `--download` 落盘,别只存 URL
- **API 单次最长 60 分钟,常规建议 < 5 分钟** — 长视频用多 scene 拼或外部 ffmpeg 连接
- **avatar_id / voice_id 必须先 list 拿,不能凭空猜** — 写死的 id 会很快过期
- 不要打印 `HEYGEN_API_KEY` 到日志,只用前 7 / 后 4 掩码
- 一个 avatar 通常有 `default_voice_id`(语气匹配) — 优先用它,除非用户指定其它

## 失败处理 — 给 video-producer agent 的契约
- exit 2(no_credentials)→ degraded=true / degrade_reason=no_credentials
- exit 3(quota_exhausted)→ degraded=true / degrade_reason=quota_exhausted
- exit 4(timeout)→ degraded=true / degrade_reason=timeout
- exit 1(heygen_fail 等)→ degraded=true / degrade_reason=heygen_<具体>
- 任何视频失败但 TTS 成功 → 保留 audio_segments,intro_video=null,pipeline 继续
