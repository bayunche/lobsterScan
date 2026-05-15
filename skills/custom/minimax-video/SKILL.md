---
name: minimax-video
description: MiniMax 视频生成（Hailuo-02 / T2V-01）。把一段 prompt 变成 6-10 秒短视频 mp4，可用作汇报材料的数字人开场或封面镜头。Agent 通过 Bash 调脚本，API key 走环境变量 MINIMAX_API_KEY。
version: 0.1
allowed-tools: [Bash, Read, Write]
---

# MiniMax Hailuo · 短视频生成

## 何时触发
当你（Agent）需要给汇报材料配一段 5-10 秒的开场镜头（如数字人讲解 / 主题封面动画 / 场景过渡）。**不要**用它做完整的 PPT 配音视频——那是 minimax-tts 的事。

## 适用场景
- HTML 汇报页封面：一段 6 秒的"数字人对镜头微笑"
- 数据展示前：一段 6 秒的"现代职场办公"过渡
- 述职开场：一段 6 秒的"演讲者站在屏幕前"

## 标准调用
```bash
python3 scripts/generate.py \
  --prompt "现代职场场景，一位中国职业女性身穿西装，正对着镜头自信地讲解项目进展，会议室背景，专业摄影机位" \
  --duration 6 \
  --output data/outputs/<task_id>/video/intro.mp4
```

参数：
- `--prompt` 必填，中文/英文均可；建议含主体 + 场景 + 摄影机视角
- `--duration` 6 或 10（秒）；默认 6
- `--resolution` 默认 `768P`，可选 `1080P`
- `--model` 默认 `MiniMax-Hailuo-02`，可选 `T2V-01-Director`
- `--output` 必填，mp4 落盘路径

## 输出 JSON
脚本会持续轮询直到完成（最长 5 分钟），最终输出：
```json
{
  "ok": true,
  "path": "data/outputs/<task>/video/intro.mp4",
  "bytes": 1234567,
  "task_id": "minimax-task-id",
  "duration": 6
}
```

## prompt 写作建议（针对汇报场景）
- 「**职场人 + 动作 + 场景**」三段式：
  - 主体：中国职业女性 / 中年男性管理者
  - 动作：对镜头微笑 / 翻阅文件 / 用手指数据 / 自信地讲解
  - 场景：会议室 / 现代办公室 / 屏幕前
- 加摄影词："专业摄影"、"中景"、"自然光"、"45 度角"
- 避免：复杂特效、抽象概念、超过 3 个主体

## 失败处理
- `MINIMAX_API_KEY` 未注入：输出 `{"ok": false, "error": "MINIMAX_API_KEY missing"}`，让 video-producer 走降级（仅交付讲稿与 TTS）
- API 配额超限（status_code=2056）：`{"ok": false, "error": "quota_exhausted"}`
- 轮询 5 分钟未完成：`{"ok": false, "error": "timeout"}`
- 任何失败都不要硬阻塞主流程，输出 `{"degraded": true}` 让 pipeline 继续
