---
name: report-reviewer
description: 对最终汇报包做 8 项结构检查 + Humanizer 二次扫描，输出 ≥3 条可执行建议与 4 个快捷指令的启用矩阵。详见 docs/Agent-Prompts/reviewer.md
version: 0.1
---

# Report Reviewer

## 8 项检查（PRD §10.8）
- has_summary
- key_points_ok (≤3 且具体)
- has_risks
- risks_have_impact
- has_next_steps
- support_clear
- length_ok（按 duration 与字数表）
- audience_fit（语气、术语难度）

## Humanizer 二次扫描
仅取 ai_signal_score ∈ [0,1]，不重写。
> 0.35 即视为"AI 套话仍较多"。

## 输出
docs/开发文档.md §8.6 的 ReviewSuggestion。

## 建议触发规则
- !has_summary → "建议在开头补一句话总体判断"
- key_points > 3 → "建议把重点收敛到 3 条以内"
- risks_have_impact == false → "建议补充每条风险的影响"
- support_clear == false → "建议指明每条诉求由谁推动"
- ai_signal_score > 0.35 → "口语化处理：去掉赋能/抓手/闭环类词"
- length_ok == false → "字数 / 时长不匹配，建议切短"
