# P8 — 运营兜底(operational safety net)设计

**日期**: 2026-06-02
**状态**: 已批准,进入 /speckit-specify
**基于**: P1–P7(均已合 main)

## 背景与目的

v2 群聊化 roadmap 的收官阶段。P1–P7 把编排从 Coordinator 硬驱动转为事件驱动的群聊,
但缺少「上线安全网」:长任务 transcript 无界增长、LLM 可能橡皮图章式互相附和、token 无硬上限。
P8 补齐三个**互相独立**的兜底能力,让 v2 路径具备上生产的安全边界。

三者皆 roadmap §9.4 标注「低-中」复杂度。

## 设计原则

- **三者独立 flag-gated**,彼此正交,也与 `V2_PROMPT_MODE`(P5)/`V2_FANOUT`(P6)/`is_v2` 正交。
- 全部**默认关**(off / `0` / legacy)→ unset 时全短路 = P7 行为,**零回归**(沿用 P5/P6 双轨范式)。
- 守宪章原则 IV:不新增 LLM 决策权。rolling summary 默认纯规则折叠(mock-first,同 `NoDriftJudge`);
  yes-man 是纯 prompt 工程;budget 是纯规则计数 + 复用既有 gatekeeper。
- 守原则 I 脱敏:budget 发声走业务化中文,不暴露 token 数 / task_id / agent_id。
- 全部 try/except 降级:任何子能力异常 → 退回未启用行为,绝不阻塞报告产出。

## ① 预算硬上限 `V2_BUDGET_CAP`(默认 `0` = off / 无限)

**累计**:`HarnessState.spent_tokens`(新字段)。在 `_run_step` 拿到 `TurnResult` 后
`spent_tokens += s.total_tokens`(主 turn 汇集点);reviewer 质量审 / `_quick_review` turn 也补计。

**检测**:observer `_loop` 每拍 `if cap > 0 and state.spent_tokens >= cap` →
`_on_budget_exceeded()`(`_budget_landed` 标志去重,只触发一次)。

**软着陆**(用户选定):
1. emit `CoordinatorIntervene(kind="budget")`——schema 已有此 kind;业务化中文发声。
2. 复用既有 gatekeeper:跑 `gate.check`——4 核心 artifact 齐 → `done`,不齐 → `partial`。
3. **已完成 step 的产物照常保留**,绝不丢成果。

**"硬"在哪**:新增 `state.budget_exceeded` 标志。`force_run_v2` 与 SPEAK 派发路径
开头检查该标志 → 触顶后**不再启动任何新 turn**,杜绝失控刷量。

## ② rolling summary `V2_ROLLING_SUMMARY`(默认 `off`;`V2_SUMMARY_THRESHOLD` 默认 `20`)

**插入点**:`_transcript_block`(P5 已渲染最近 K 条发言 + artifact 摘要)。
当 `_recent` 条数超 `V2_SUMMARY_THRESHOLD` → 保留尾部 K 条逐条,
把更早的折叠成**一行确定性摘要**:`（前 N 条已折叠:<拼接截断>）`。

**决策 B**:默认纯规则折叠(不调 LLM,mock-first)→ 避免新增 LLM 决策权(宪章原则 IV)。
预留 `RollingSummarizer` 注入抽象(同 `DriftJudge` / `set_default_drift_judge` 范式)供将来真 LLM。

此能力**反向减少 prompt token**,与 budget cap 互补:长任务下 prompt 上下文有界。

## ③ yes-man 防御 `V2_YESMAN_DEFENSE`(默认 `off`)

**纯 prompt 工程**:on 时给 reviewer 评审路径(`QUICK_REVIEW_PROMPT` + harness 质量审)
前置 `_YESMAN_BLOCK`「对立质疑」段:要求主动找瑕疵、假设作者可能过度自夸、
不许橡皮图章式附和。抵消现有 `QUICK_REVIEW_PROMPT` 里「不要过度严格」的松绑措辞。

## 改动模块(沿用各 P 落点)

| 模块 | 改动 |
|---|---|
| `subscription.py` | +4 flag(`V2_BUDGET_CAP`/`V2_ROLLING_SUMMARY`/`V2_SUMMARY_THRESHOLD`/`V2_YESMAN_DEFENSE`)+ `__all__` |
| `harness.py` | `HarnessState.{spent_tokens, budget_exceeded}` + `_run_step` 累计 + 派发短路 |
| `coordinator_observer.py` | `_loop` budget 检测 + `_on_budget_exceeded` 软着陆(复用 `gate.check` / `_emit_intervene` / `_set_done`) |
| `pipeline.py` | `_transcript_block` 折叠 + `_yesman_block()` + `_budget_enabled`/`_rolling_enabled`/`_yesman_enabled` helper(每次读 subscription,支持 monkeypatch) |

## 验收(用户选定:测试级绿 + 1 次真 LLM 端到端)

- ScriptedBackend / monkeypatch 单测覆盖三轨:
  - budget:低 cap → 触顶软着陆出 partial、产物保留、emit budget intervene、触顶后不再派发新 turn。
  - rolling:超阈值折叠、尾部逐条保留、未超阈值原样、observer 缺失降级空串。
  - yesman:on 时 reviewer prompt 含对立质疑段,off 时不含。
- `test_v1_regression` 字段级零回归(三 flag unset → prompt / 行为与 P7 一致)。
- 1 次真 LLM 任务:set `V2_BUDGET_CAP` 较低 → 验证触顶软着陆、partial、群里 budget 发声;
  rolling 不泄漏技术 ID;yesman 生效。

## 宪章合规

无需新宪章修订(v1.1.0 已够):
- budget = 纯规则计数 + 复用 gatekeeper(原则 IV 路由/兜底纯规则)。
- rolling 默认纯规则折叠(LLM 注入点预留但不交付,同 P3 `NoDriftJudge`)。
- yes-man = prompt 工程,不引入新 LLM 决策权。
- 全程脱敏发声守原则 I。

## 非目标(YAGNI / deferred)

- 真 LLM rolling 摘要(`LLMRollingSummarizer`)——注入点预留,本期不交付(同 P3 drift LLM)。
- 按 provider / 双通道分别计费的细粒度预算——本期只做单一累计 token 硬上限。
- 跨 task 的全局预算池——本期 budget 是 per-task(per-HarnessState)。
