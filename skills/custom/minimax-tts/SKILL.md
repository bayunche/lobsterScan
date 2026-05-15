---
name: minimax-tts
description: MiniMax 中文 TTS（speech-02-hd / speech-02-turbo）。把汇报讲稿切成段落，每段生成 mp3 与 SRT 字幕段，落盘到 data/outputs/<task_id>/audio/。在 video-producer 等需要把文本转配音的 Agent 上调用。
version: 0.1
allowed-tools: [Bash, Read, Write]
---

# MiniMax TTS · 自渲染数字人配音

## 何时触发
当你（Agent）需要把一段中文讲稿合成为可播放的 mp3，并准备好用于 web-video-presentation 或 ffmpeg 合成视频时。

## 输入与决策
你拿到的输入通常是：
- `script_md`：完整朗读稿（已经按段落分好）
- `slides`：每页 title + content
- `narrations`：可选；如果上游已经给了 `[{chapter, step, text}]` 直接用；否则你按页切讲稿。

## 标准流程
1. **切段**：按 slides 数（去掉封面与封底），把 script_md 切成对应段数。每段一句话/一组连贯句，控制 5–25 秒。
2. **挑 voice**：默认 `male-qn-qingse`（沉稳男声）。需要女声用 `female-shaonv`（少女）/ `female-yujie`（御姐）/ `female-chengshu`（成熟）。其它可用 voice 见 `references/voices.md`。
3. **逐段调脚本**：
   ```bash
   python3 scripts/synthesize.py \
     --text "本周项目整体推进到 80%……" \
     --voice male-qn-qingse \
     --model speech-02-hd \
     --output data/outputs/<task_id>/audio/01.mp3
   ```
   每段调用都会打印 JSON：`{"ok":true,"path":"...","bytes":xxx,"duration_estimate":x.x}`。
4. **生成 SRT**：用 `scripts/build_srt.py` 把所有段拼成 `subtitles.srt`。
5. **输出 JSON**：参见下面的 schema。

## 输出 JSON
```json
{
  "audio_segments": [
    {"index": 1, "text": "...", "voice": "male-qn-qingse",
     "path": "data/outputs/<task_id>/audio/01.mp3",
     "duration_estimate_sec": 6.4}
  ],
  "subtitle_path": "data/outputs/<task_id>/audio/subtitles.srt",
  "merged_path": "data/outputs/<task_id>/audio/full.mp3",
  "voice_style": "沉稳男声",
  "tts_provider": "minimax",
  "tts_model": "speech-02-hd"
}
```

## 失败处理
- API 配额超限（status_code=2056）：等待重置时间，先返回 `{"degraded": true, "reason":"quota_exhausted", "audio_segments":[]}`，让 pipeline 走降级路径
- voice_id 不存在（status_code=2054）：换 voice 重试 1 次
- API key 未注入（env MINIMAX_API_KEY 为空）：报 `{"error":"MINIMAX_API_KEY missing"}`，让 pipeline 走 stub
- 网络超时：retry 一次后降级

## 字数 → 时长粗算
中文按 4-5 字/秒（speech-02-hd 默认速度）。50 字 → 约 11 秒，500 字 → 约 110 秒。
