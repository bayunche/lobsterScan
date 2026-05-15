---
name: upward-translator
description: 将部门视角的 ReportCore 改写为领导视角的 ReportCore。结论前置、风险显化、诉求具体化。详见 docs/Agent-Prompts/upward-opt.md
version: 0.1
---

# Upward Translator

## 触发
step == "upward_optimization"，且在调用 humanizer 之前。

## 转换原则
1. 结论前置——summary 必须先说总体进度，再说事项
2. 动词改主动 + 量化（"推进了多项工作" → "完成 18 个客户回访，12 个确认推进"）
3. 风险改 `{item, impact}`，impact 必须可解读为业务影响
4. 诉求改 `{item, owner}`，owner 必须明确到角色
5. 下一步计划改 `动作 + 时间 + 责任人`

## 反例（CHINESE-AI-PHRASES）
赋能 / 抓手 / 闭环 / 链路 / 落地 / 沉淀 / 心智 / 复盘起来 / 拉齐对齐

## 输出
ReportCore 同 schema，内容做视角转换。
