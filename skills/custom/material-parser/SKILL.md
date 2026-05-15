---
name: material-parser
description: |
  当 Agent 收到 raw_text + 文件路径数组时，把内容解析为「素材池」（MaterialPool）。
  详见 docs/Skill接入清单.md §二.1
version: 0.1
allowed-tools: [Read, Bash, Grep]
---

# Material Parser

## 触发
该 Agent 收到 `task.step.request` 且 step == "material_parsing"。

## 步骤
1. 列出 `payload.files`，按 mime 选择解析器：
   - `.md` / `.txt` → 直接读
   - `.docx` → `python-docx` 抽段落
   - `.xlsx` → `pandas.read_excel` 转 markdown 表
   - `.pdf`  → `pdfminer.six` 抽文本
2. 把 raw_text + 解析所得正文合并喂给 LLM，按下表抽字段：

   | 字段 | 含义 |
   | --- | --- |
   | completed | 已完成事项 |
   | in_progress | 进行中事项 |
   | key_data | 关键数据（name/value/note） |
   | risks | 风险描述 |
   | support_needed | 需要的支持 |
   | next_steps | 下一步计划 |
   | people | 提到的人/部门 |
   | data_gaps | 文档里缺失的关键数据 |

3. 输出严格符合 docs/开发文档.md §8.2 的 MaterialPool JSON。

## 失败处理
- 文件解析失败 → 在 data_gaps 写："文件 X 解析失败，建议复制核心内容到文本框"
- raw_text 与所有文件都空 → 输出 `task.step.error` code=INPUT_TOO_SHORT
