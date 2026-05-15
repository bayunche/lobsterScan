---
name: report-structure
description: 按 report_type 从大纲模板库挑选 Outline；不写内容，只决定章节顺序与字段映射。
version: 0.1
---

# Report Structure

## 资源
- assets/report-structures/daily.yaml
- assets/report-structures/project_progress.yaml
- assets/report-structures/review.yaml
- assets/report-structures/introduction.yaml

## 步骤
1. 读 USER.md 或 payload.context.report_type
2. 载入对应 YAML
3. 输出 Outline JSON（详见 docs/Agent-Prompts/structure.md）

## 约束
- chapters 数量 5-7
- 必含 cover / summary / next_steps 三类页
