---
name: point-extractor
description: 从 MaterialPool 提炼一句话总结 + ≤3 重点 + 进度 + 风险 + 诉求 + 计划。详见 docs/Agent-Prompts/point-extractor.md
version: 0.1
---

# Point Extractor

## 触发
step == "point_extraction"

## 硬规则
1. 一句话总结 ≤80 字
2. key_points 数量 1-3，每条 ≤30 字，必须含「动词 + 对象 + 数据/结果」
3. progress_status ∈ { 正常 | 有轻微风险 | 有风险 | 已延期 }
4. risks 每条 `{item, impact}`，impact 不可为空
5. support_needed 每条 `{item, owner}`
6. 缺数据 → 写入 ReportCore.data_gaps，不要编造

## 反例（被禁止输出）
- "推进了多项工作"
- "整体顺利"
- "完成度大约较高"

## 输出
严格符合 docs/开发文档.md §8.3 的 ReportCore JSON。
