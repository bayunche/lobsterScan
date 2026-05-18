---
name: minimax-music
description: MiniMax 音乐生成(music-2.6 / music-2.6-free)。给汇报视频 / 数字人开场配 BGM 或主题音乐。Agent 通过 Bash 调脚本,优先 mmx CLI 免传 key。
version: 0.1
allowed-tools: [Bash, Read, Write]
---

# MiniMax music · 背景音乐 / 主题曲

## 何时触发
- 汇报视频 BGM(纯器乐,低存在感衬底)
- 开场主题曲(短促有氛围)
- 章节过渡(15-30 秒小段)

不适合:配音、对话(用 minimax-tts)。

## 标准调用 — 纯器乐 BGM
```bash
python3 .agents/skills/minimax-music/scripts/generate.py \
  --prompt "cinematic ambient, soft strings, professional, low presence" \
  --instrumental \
  --output data/outputs/<task_id>/audio/bgm.mp3
```

## 标准调用 — 带歌词主题曲
```bash
python3 .agents/skills/minimax-music/scripts/generate.py \
  --prompt "uplifting corporate pop, modern, bright" \
  --lyrics "[Verse]\n用户管理这一周\n稳步推进很顺利\n[Chorus]\n汇报会上展实力" \
  --output data/outputs/<task_id>/audio/theme.mp3
```

参数:
- `--prompt` 必填,音乐风格描述
- `--lyrics` / `--lyrics-file` / `--lyrics-optimizer` / `--instrumental` 四选一(歌词来源)
- `--model` 默认 `music-2.6`(plan 100/天 700/周);`music-2.6-free` 需要 Max plan
- `--output` 必填,mp3 路径

## 输出 JSON
```json
{
  "ok": true,
  "path": "data/outputs/<task>/audio/bgm.mp3",
  "bytes": 1234567,
  "model": "music-2.6-free",
  "via": "mmx"
}
```

## TokenPlan 与额度
- `music-2.6`:**默认**,plan 配额 0/100 每天,0/700 周(普通 plan 即可)
- `music-2.6-free`:需要 **Max plan** 及以上,基础 plan 会被拒
- `music-cover`:0/100 每天,需要参考音频(`mmx music cover`,本 skill 暂未支持)
