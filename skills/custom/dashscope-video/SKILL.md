---
name: dashscope-video
description: 阿里 DashScope 万相 2.7 文生视频。把一段 prompt 变成 5-10 秒短视频 mp4。Agent 通过 Bash 调脚本，API key 走环境变量 DASHSCOPE_API_KEY。
version: 0.1
allowed-tools: [Bash, Read, Write]
---

# DashScope 万相 · 文生视频

## 何时使用
需要生成数字人出镜视频或场景短片时，调用本 skill。

## 调用方式
入口：`python3 {skill_root}/scripts/dashscope_video.py [flags]`

## 参数

| Flag | 说明 | 默认 | 类型 |
|---|---|---|---|
| `--prompt` | 视频描述文本（必填） | — | string |
| `--model` | 模型 ID | wan2.7-t2v-2026-04-25 | enum |
| `--duration` | 视频时长（秒） | 5 | 5 / 10 |
| `--resolution` | 分辨率 | 720P | 480P / 720P |
| `--ratio` | 画面比例 | 16:9 | 16:9 / 9:16 / 1:1 |
| `--negative_prompt` | 负向 prompt | — | string |
| `--output_dir` | 落盘目录 | ./output | path |
| `--no_wait` | 提交后立即返回 task_id | false | bool |
| `--task_id` | 查询已有任务 | — | string |
| `--download` | 配合 --task_id 下载 | false | bool |
| `--check` | 验证 API key 有效性 | false | bool |

## 可用模型
- `wan2.7-t2v-2026-04-25` — 高质量（默认）
- `wan2.7-t2v-turbo` — 快速

## 鉴权
环境变量 `DASHSCOPE_API_KEY`（backend 已注入）。

## 调用示例

```bash
# 验证凭据
python3 {skill_root}/scripts/dashscope_video.py --check

# 文本生成 5 秒视频
python3 {skill_root}/scripts/dashscope_video.py \
  --prompt "中国年轻职场女性，身穿深色职业装，坐在明亮会议室中景，自然微笑讲话" \
  --model wan2.7-t2v-2026-04-25 --duration 5 --resolution 720P \
  --output_dir data/outputs/<task_id>/video

# 提交不等待
python3 {skill_root}/scripts/dashscope_video.py --prompt "..." --no_wait
```

## 输出 JSON

**成功**：
```json
{"ok": true, "task_id": "<id>", "path": "./output/<id>.mp4", "bytes": 1234567, "model": "wan2.7-t2v-2026-04-25"}
```

**失败**：
```json
{"ok": false, "error": "auth_failed", "status": 401}
{"ok": false, "error": "task_failed", "task_id": "...", "message": "..."}
{"ok": false, "error": "timeout", "task_id": "..."}
```

## 退出码
- `0` — 成功
- `1` — 通用错误
- `2` — 鉴权失败
- `4` — 轮询超时
