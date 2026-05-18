---
name: minimax-image
description: MiniMax 图片生成(image-01)。给汇报材料生成封面图 / 配图 / 头图。Agent 通过 Bash 调脚本,优先用 mmx CLI(免传 key),失败回退 HTTP(MINIMAX_API_KEY)。
version: 0.1
allowed-tools: [Bash, Read, Write]
---

# MiniMax image-01 · 图片生成

## 何时触发
当你(Agent)需要给汇报材料生成图片素材:
- HTML 汇报页封面图(替代占位的 gpt-image-2)
- slide 配图(数据可视化抽象图 / 场景示意 / 主题装饰)
- 数字人开场镜头(若 video 通道没额度)

## 标准调用
```bash
python3 .agents/skills/minimax-image/scripts/generate.py \
  --prompt "现代职场封面图,简洁正式,书写台面" \
  --aspect-ratio "16:9" \
  --output data/outputs/<task_id>/images/cover.jpg
```

参数:
- `--prompt`  必填,中英文均可;建议含主体 + 场景 + 风格
- `--aspect-ratio` 16:9 / 1:1 / 9:16 / 4:3 等(默认 16:9)
- `--n` 生成几张(默认 1);>1 时用 `--out-dir`
- `--output` 必填(`--n=1`),指定 mp4 路径
- `--out-dir` `--n>1` 时用,目录路径
- `--seed` 复现用

## 输出 JSON
```json
{
  "ok": true,
  "path": "data/outputs/<task>/images/cover.jpg",
  "bytes": 234567,
  "model": "image-01",
  "via": "mmx"
}
```

## 失败处理
- `MINIMAX_API_KEY missing`(只在 mmx 不存在 且 fallback HTTP 走时):key 没填
- `mmx_nonzero`:plan 额度耗尽 / model 名错 / 网络问题 — 看 stderr
- `usage limit exceeded`:TokenPlan 周配额耗尽(image-01 是 0/50 周 350)

## TokenPlan 与额度
- `image-01` 当前 plan 列表里有 — 周 0/350,日 0/50
- 默认走 mmx(用户 mmx auth login 配的 key),plan 自动扣
- 想生成无水印:不要加 `--aigc-watermark`
