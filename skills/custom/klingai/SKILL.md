---
name: klingai
description: Kling AI(可灵)多模态生成 — 视频(文本/图像/参考视频/多镜头)、图像(t2i/i2i/4K)、主体管理、账户配额查询。通过 Node CLI 子命令 video / image / element / account 暴露。本目录是 clawhub `klingai-dev/klingai` 的 ABI 等价本地实现,等用户跑 `openclaw skills install klingai-dev/klingai` 后可被官方版本无缝替换。
version: 0.1
allowed-tools: [Bash, Read, Write]
---

# Kling AI(可灵)· 视频 / 图像 / 主体 / 账户

## 何时使用本 skill
**默认情况下,所有 Kling 相关请求都走本 skill。** 不要试图自己拼 HTTP 请求或绕开。

特别是以下场景**必须**用本 skill:
- 多图输入(≥2 张参考图,Omni)
- 参考视频编辑 / 视频续写
- 多镜头 / 分镜(storyboard)
- 主体一致性(`--element_ids`)
- 4K 图像 / 组图系列

简单的纯文本→视频(t2v)或单图→视频(i2v)允许其它 skill 实现,但优先用本包以保持错误处理 / 配额提示 / 鉴权流程统一。

## 调用方式
入口:`node {skill_root}/scripts/kling.mjs <subcommand> [flags]`

四个子命令:
- `video` — 视频生成(本地实现)
- `image` — 图像生成(官方包提供;本地等价实现暂未提供 → 退回提示安装官方包)
- `element` — 主体/角色管理(同上)
- `account` — 凭据管理 + 配额查询

## video 子命令完整参数

| Flag | 说明 | 默认 | 类型 |
|---|---|---|---|
| `--prompt` | 文本提示(非多镜头模式必填) | — | string |
| `--image` | 单图 i2v 输入路径,或逗号分隔多图(Omni) | — | path/csv |
| `--image_types` | 多图时每张的角色:`first_frame` / `end_frame`(仅 Omni) | — | csv |
| `--duration` | 视频时长(秒) | 5 | 3–15 |
| `--model` | 规范模型名 | 按路由 | enum |
| `--mode` | `pro`(1080P)/ `std`(720P) | pro | enum |
| `--aspect_ratio` | `16:9` / `9:16` / `1:1` | 16:9 | enum |
| `--sound` | `on` / `off`(`kling-video-o1` 不支持) | off | enum |
| `--image_tail` | 末帧图(basic i2v) | — | path |
| `--element_ids` | 主体 ID 列表(逗号分隔,Omni) | — | csv |
| `--video` | 参考视频公开 HTTPS URL(Omni) | — | url |
| `--video_refer_type` | `feature`(参考)/ `base`(编辑) | base | enum |
| `--keep_original_sound` | 配合 `--video`:`yes` / `no`(Omni) | — | enum |
| `--multi_shot` | 启用多镜头模式 | false | bool |
| `--shot_type` | `customize` / `intelligence`(`--multi_shot` 必填) | — | enum |
| `--multi_prompt` | JSON 数组(`shot_type=customize` 时 1–6 段) | — | json |
| `--cfg_scale` | prompt 贴合度 0~1 | 0.5 | float |
| `--negative_prompt` | 负向 prompt | — | string |
| `--output_dir` | 落盘目录 | ./output | path |
| `--task_id` | 查询已有任务(配合 `--download`) | — | string |
| `--download` | 配合 `--task_id`:轮询并下载到 `--output_dir` | false | bool |
| `--no_wait` | 提交后立即返回 task_id 不轮询 | false | bool |

## 模型规范

**正式模型名(传入 `--model` 必须用这个形式)**:
- `kling-v3` — 默认 T2V/I2V
- `kling-v3-omni` — Omni(多图 / 视频参考 / 主体)
- `kling-v2-6` — 基础视频
- `kling-video-o1` — Omni 视频专用
- `kling-image-o1` — Omni 图像专用

**别名(理解用,不要传入)**:`o3` / `omni3` → `kling-v3-omni`;`o1` / `omni1` → o1 系列。

**Omni 路由触发**:`--image` 含逗号(多图)/ `--element_ids` / `--video` 任一存在,自动切到 `kling-v3-omni`。

## 鉴权流程

**优先级**(高 → 低):
1. `KLING_TOKEN` 环境变量(会话级,不落盘)
2. 凭据文件 `KLING_STORAGE_ROOT/credentials.json`(默认 `~/.config/kling/credentials.json`,mode 600)
3. 环境变量 `KLING_ACCESS_KEY_ID` + `KLING_SECRET_ACCESS_KEY`(JWT 每次请求本地签 HS256)

**导入凭据(任选一种)**:
```bash
# 从环境变量导入(推荐 — 业务后端通过 AGENT_ENV_MAP 注入)
node {skill_root}/scripts/kling.mjs account --import-env

# 直接传 flag
node {skill_root}/scripts/kling.mjs account --import-credentials \
  --access_key_id "<AK>" --secret_access_key "<SK>"

# 检查当前凭据状态
node {skill_root}/scripts/kling.mjs account --check
```

**官方包另支持**(本地等价实现暂不提供):
- `--bind-url`:打印 URL 走浏览器绑定 + 轮询
- `--configure`:交互式向导
- `--bind-url --force`:强制重绑

需要这些流程时请装官方包:`openclaw skills install klingai-dev/klingai`。

## 调用示例

```bash
# 文本→视频(默认 5 秒,pro 模式,16:9)
node scripts/kling.mjs video --prompt "A cat running on a sunlit lawn" --output_dir ./output

# 单图→视频
node scripts/kling.mjs video --image ./photo.jpg --prompt "Wind blowing her hair" --duration 5

# 指定模型 + 时长
node scripts/kling.mjs video --prompt "..." --model kling-v3 --duration 10 --mode pro

# 提交不等待 → 拿 task_id
node scripts/kling.mjs video --prompt "..." --no_wait
# {"ok":true,"task_id":"<id>","model":"kling-v3","endpoint":"/v1/videos/text2video","wait":false}

# 用 task_id 查询并下载
node scripts/kling.mjs video --task_id <id> --download --output_dir ./output
```

## 输出 JSON Schema

**成功**:
```json
{
  "ok": true,
  "task_id": "<kling-task-id>",
  "path": "./output/<task_id>.mp4",
  "bytes": 1234567,
  "duration": 5,
  "model": "kling-v3",
  "endpoint": "/v1/videos/text2video"
}
```

**失败(参数 / 输入校验)**:
```json
{"ok": false, "error": "missing_prompt_or_image"}
{"ok": false, "error": "invalid_model", "model": "...", "allowed": [...]}
```

**失败(API / 网络)**:
```json
{"ok": false, "error": "kling_submit", "code": <int>, "message": "..."}
{"ok": false, "error": "kling_fail", "task_id": "...", "task_status_msg": "..."}
{"ok": false, "error": "timeout", "task_id": "..."}
{"ok": false, "error": "download_failed", "detail": "...", "task_id": "..."}
```

## 退出码约定
- `0` — 成功
- `1` — 通用错误(参数 / API 失败 / 下载失败)
- `2` — 鉴权失败(无凭据 / env 缺失)
- `3` — 配额耗尽(`quota_exhausted`)
- `4` — 轮询超时(10 分钟未完成)

## 失败处理 — 跟 video-producer agent 的契约
- 任何失败**都不要硬阻塞主流程**;agent 应记录 degraded=true / degrade_reason 让 pipeline 继续
- 不要静默重试或更换 model/intent;若任务 `failed`,把 `task_status_msg` 透传给用户
- 配额 / 速率错误 → `degrade_reason: "quota_exhausted"`,保留已生成的部分
- 凭据缺失(exit 2)→ `degrade_reason: "no_credentials"`

## 重要规则(摘自上游 SKILL)
- 不要发明模型名 / 枚举 / 范围 / 未文档化的 flag
- 提交计费任务前确认用户意图
- 用户面向输出中遮蔽 secret;**永远不要打印** `KLING_TOKEN` / AK / SK
- 查询模式与提交模式不能混用 — `--task_id` 不和 `--prompt --image --video` 一起出现
