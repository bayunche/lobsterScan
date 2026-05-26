---
name: report-structure
description: 按 USER.md 的 report_type + duration 把 ReportCore 套进 outline。不写内容,只决定 chapters 顺序、type、data_keys、role 标注、transition_in。DURATION_PROFILE 是单一真值:1 分钟总-分 / 3 分钟总-分-总 / 5 分钟总-分-分-总。
version: 0.2
allowed-tools: [Read, Write]
---

# Report Structure · 结构设计师

## 何时触发
- pipeline step == `structure_building`
- 上游 ReportCore 已稳定(point-extractor 出来的)
- 必读 USER.md / payload.context.report_type 与 duration

## 何时不触发
- `report_type` 不在已知模板里 → 默认走 `project_progress` + 在 `note` 字段记录原始 type
- `duration` 不在 {1分钟 / 3分钟 / 5分钟} → `needs_help`

## 方法论 · DURATION_PROFILE 是真值
对应 `apps/web-backend/app/orchestrator/pipeline.py:269` 的 `DURATION_PROFILE`,数值漂移会被 reviewer 在 `length_ok` 检测打回。

| duration | structure | chapters_n | narrations_n | script_words | 写作原则 |
|---|---|---|---|---|---|
| 1 分钟 | 总-分 | 5-6 | 4-5 | 200-300 | 极致克制,一观点+一数字+一动作(章节数对齐 PRD HTML ≥5 页) |
| 3 分钟 | 总-分-总 | 5-6 | 7-9 | 500-750 | 标准向上汇报节奏,3 分点(成果/风险/诉求) |
| 5 分钟 | 总-分-分-总 | 7-8 | 12-15 | 900-1200 | 有空间展开,每分点可带 1 案例或数据细节 |

### 三种 structure 的章节骨架

**总-分(1 分钟):**
```
1. cover               封面 · 标题 + 一句话定位
2. summary             总览 · 核心一句话 + 1 个量化数据
3. completed           分1 · 已落地最关键 1 点 + 量化结果
4. risks               分2 · 最关键风险 + 影响(无风险时写"暂无显著风险待办")
5. next_steps          收束 · 1-2 条最关键动作 + owner
6. closing(可选)      一句话承诺或会议安排
```

**总-分-总(3 分钟):**
```
1. cover
2. summary             一句话 + progress_status
3. completed           分1 · 已落地能力的业务价值
4. risks               分2 · 风险与应对
5. next_steps          分3 → 总收束 · 下一步 + 诉求
6. closing(可选)      一句话呼吁 / 承诺
```

**总-分-分-总(5 分钟):**
```
1. cover
2. summary             总览
3. completed           分1.1 · 已落地能力
4. key_data            分1.2 · 业务数据深挖
5. in_progress         分2.1 · 进行中工作
6. risks               分2.2 · 风险与应对
7. support_needed      分3 · 具体诉求
8. next_steps          总收束 · 时间表
```

## Schema(Outline)
```json
{
  "report_type": "project_progress",
  "duration": "3分钟",
  "structure": "总-分-总",
  "chapters": [
    {
      "chapter_no": 2,
      "title": "本周工作概览",
      "type": "summary",
      "role": "总",
      "data_keys": ["summary", "progress_status"],
      "transition_in": "先看本周大盘"
    }
  ]
}
```

## 转场词库(按 role 选,给 copywriter 用)
| role | 适用转场词 |
|---|---|
| 总(开场) | 先看 / 总体来说 / 概括起来 |
| 分(深入) | 具体到 / 落到 / 拆开看 |
| 分(切换) | 另外 / 再看 / 接着 |
| 总(收束) | 综上 / 接下来 / 落到本周 |

> copywriter 会用 `chapter.transition_in` 起承转合,缺了会被 reviewer 在 `audience_fit` 标注。

## role 标注规则
- 第一个 chapter(除 cover) → `总`
- 中间 chapters → `分`(5 分钟可细分 `分1` / `分2`)
- 最后一个 chapter(除 closing) → `总`
- closing 章 → `closing`(可选)

## 反模式 · 红线
- ❌ `chapters_n` 不在 DURATION_PROFILE 范围
- ❌ 没有 summary 类章节(向上汇报必须有总开场)
- ❌ 没有 next_steps 类章节(领导要看「然后呢」)
- ❌ `data_keys` 引用了 ReportCore 里不存在的字段
- ❌ 自己编内容写进 chapter(structure 只决定壳子,内容是 copywriter 的活)
- ❌ 1 分钟硬塞 6 章 / 5 分钟只给 4 章 — 比例不对

## 自检清单
- [ ] `chapters_n` 在 DURATION_PROFILE.chapters_n 范围?
- [ ] 含 cover / summary / next_steps 三类章节?
- [ ] 每章 `data_keys` 都能在 ReportCore 找到对应字段?
- [ ] `role` 标注覆盖所有非 cover / closing 章节?
- [ ] 转场词从转场词库里选的?
- [ ] `structure` 字段与 DURATION_PROFILE.structure 一致?
- [ ] 没把 5 分钟的"分-分"合并成单分,也没把 1 分钟的"分"拆成两段?
