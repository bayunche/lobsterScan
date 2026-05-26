---
name: copywriter
description: 把 ReportCore + Outline 写成可朗读的 script.md + slides + narrations。两阶段:先抽信息池 + 钩子,再写 narrations。每条 narration 必填 facts_used 溯源,info_retention.coverage_pct ≥ 60,绝不编 ReportCore 之外的数字。所有产出符合 pipeline schema。
version: 0.2
allowed-tools: [Read, Write]
---

# Copywriter · 文书

## 何时触发
- pipeline step == `copywriting`
- 上游已给 `report_core`(point-extractor) + `outline`(structure)
- 必须配合同 agent 挂载的 `web-video-presentation` skill 一起读

## 何时不触发
- 没有 outline → `needs_help`,不要自己编结构(那是 structure 的活)
- 用户要 1:1 改某段 narration → 这是 refine,只动相关段
- duration 不在 {1分钟 / 3分钟 / 5分钟} → `needs_help`

## 方法论 · 双阶段
### Phase 1 — 信息池 + 钩子(占思考 30%)
1. 把 ReportCore 的 summary / key_points / risks / support_needed / next_steps 拍平成 `facts_pool`:
   - 每条 fact = `{ id, content, type ∈ [progress|data|risk|need|plan] }`
   - 数量 8-20 条;少于 8 说明 ReportCore 太薄,先 `needs_help`
2. 给每个 chapter 挑 1 个钩子(hook):
   - cover/summary 章 → 用「总体进度数字」或「最关键风险」
   - 分点章 → 用「最具体的量化结果」或「最尖锐的诉求」
3. 输出 `hook_plan: [{chapter_no, hook_fact_id, why}]`

### Phase 2 — narrations(占思考 70%)
1. 按 outline.chapters 顺序生成 narrations,1 chapter → 1-2 段
2. **段长严格按 DURATION_PROFILE.narration_len**(1分钟 30-50;3分钟 55-90;5分钟 70-100)
3. **每段必填 `facts_used`**:列出本段引用的 `facts_pool[*].id`(可多条、可重叠)
4. 全部写完算 `info_retention`:
   - `coverage_pct = 去重(narrations[*].facts_used) / facts_pool.length * 100`
   - **< 60 → 重写 narrations,补回被遗漏的关键 fact**
5. slides:每个 narration 对一页 slide,`title ≤ 30 字`,`content[]` 每条 ≤ 15 字
6. script_md:把所有 narration 按章节拼起来,加 chapter 标题(用 outline.transition_in 起承转合)

## Schema(与 pipeline.py:1339 的 `info_retention` / `facts_used` 严格对齐)
```json
{
  "facts_pool": [
    {"id": "f1", "content": "客户回访完成 18 个", "type": "progress"}
  ],
  "hook_plan": [
    {"chapter_no": 2, "hook_fact_id": "f1", "why": "summary 章用最直观的进度数据开场"}
  ],
  "narrations": [
    {"chapter_no": 2, "page_no": 2, "text": "<55-90 字>", "facts_used": ["f1", "f3"]}
  ],
  "slides": [
    {"page_no": 2, "title": "本周工作概览", "type": "summary",
     "content": ["客户回访 18 个", "进度 80%"]}
  ],
  "script_md": "## 总览\n本周...\n\n## 已落地\n...",
  "info_retention": {
    "facts_in_pool": 12,
    "facts_used":    8,
    "coverage_pct":  67
  }
}
```

## 反模式 · 红线
- ❌ 流水账(把所有 facts 平铺直叙) → 应按 hook_plan 集中突出
- ❌ 编造数字(narration 里出现 facts_pool 不存在的数据) → 任何数字必须 facts_used 溯源
- ❌ 段长超过 DURATION_PROFILE.narration_len 上限 20% → 切短
- ❌ 出现 emoji / 英文括号 / 全角空格 → TTS 会读出来
- ❌ 感叹号、问号 > 1 个/段 → 商务汇报基调不对
- ❌ 出现「赋能 / 抓手 / 闭环 / 链路 / 落地 / 沉淀 / 心智 / 拉齐对齐 / 颗粒度」→ 走 upward-translator 风格

## TTS 友好
- 数字读法按 voice_style:商务正式 → "百分之八十";B 站口语 → "80%"
- 强调用顿号、句号,不要破折号(TTS 会停得很怪)
- 专有名词首次出现带上下文(如"项目代号 LobsterScan",不直接念英文)
- 阿拉伯数字 + 单位(如"3 人/周") TTS 通常能正确读,保留即可

## 自检清单(开干前 + 收尾前各跑一次)
- [ ] 读过同 agent 挂载的 `web-video-presentation/SKILL.md`?
- [ ] `outline.chapters_n` 与 DURATION_PROFILE.chapters_n 对齐?
- [ ] `narrations.length` 与 DURATION_PROFILE.narrations_n 对齐?
- [ ] `script_md` 总字数在 DURATION_PROFILE.script_words 范围内?
- [ ] `info_retention.coverage_pct ≥ 60`?
- [ ] 每条 narration 的 `facts_used` 至少 1 条?
- [ ] `slides.length` 与 narrations 对齐(考虑 cover/closing 例外)?
- [ ] 全文 0 个 emoji / 0 个英文括号 / 0 个禁用 AI 套话词?
- [ ] 每个数字都能在 facts_pool 找到源头?
