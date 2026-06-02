# Implementation Plan: P8 — 运营兜底(operational safety net)

**Branch**: `008-ops-safety-net` | **Date**: 2026-06-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-ops-safety-net/spec.md`

## Summary

v2 群聊化路线图收官阶段。在 P1–P7(均已合 main)的事件驱动群聊之上补三道独立的上线安全网:
① **预算硬上限** —— 任务级累计 token,触顶后停启新环节并复用既有 gatekeeper 软着陆(done/partial,保留已完成产物);
② **rolling summary** —— 群聊上下文发言超阈值时折叠较早发言为一行确定性摘要,使注入上下文有界;
③ **yes-man 防御** —— 给审校路径注入「对立质疑」prompt 段,防止橡皮图章式附和。
三者各由独立开关控制,**默认全关 → 逐字段零回归**,彼此正交、且与 `V2_PROMPT_MODE`(P5)/`V2_FANOUT`(P6)/`is_v2` 正交。

## Technical Context

**Language/Version**: Python 3.11(web-backend,FastAPI/asyncio)

**Primary Dependencies**: 既有 v2 harness 栈 —— `harness.py`(EventBus/AgentWorker/HarnessState)、
`coordinator_observer.py`(watchdog observer + gatekeeper)、`subscription.py`(flag SSOT)、
`pipeline.py`(prompt 构造 + `_run_step` + `_transcript_block` + `_quick_review`)。无新增第三方依赖。

**Storage**: N/A(预算计数为 task-level 内存态,挂在 `HarnessState`;产物仍走既有 `data/outputs/<task_id>/`)

**Testing**: pytest,`apps/web-backend/tests/orchestrator/`。ScriptedBackend / monkeypatch 单测覆盖三轨 +
`test_v1_regression.py` 字段级零回归扩充。沿用 P5/P6 基线。

**Target Platform**: Windows 11 本地开发(ProactorEventLoop)+ Linux server 部署

**Project Type**: web-service(monorepo 内 web-backend 单服务改动;无前端改动)

**Performance Goals**: 预算检测复用 observer 既有 0.5s 轮询拍,无新增轮询;rolling 折叠 O(条数) 字符串拼接;
yesman 仅 prompt 字符串拼接。三者对热路径无可测开销;关闭时全短路零开销。

**Constraints**: 三能力默认全关、逐字段零回归(FR-002);全程 try/except 降级不阻塞报告(FR-004,原则 III);
所有用户可见发声脱敏(FR-005,原则 I);不新增 LLM 决策权(原则 IV)。

**Scale/Scope**: 改动 4 个既有文件(subscription/harness/coordinator_observer/pipeline),
新增约 ~150 行 + 3 个测试文件。无新模块(rolling 折叠就地在 `_transcript_block`,
`RollingSummarizer` 注入点为可选轻量抽象)。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 关系 | 结论 |
|---|---|---|
| **I. 用户可见层脱敏(NON-NEGOTIABLE)** | budget 触顶发声、rolling 折叠摘要均为面向用户文本 | ✅ FR-005 强制业务化中文,不含 token 数/task_id/agent_id;budget 复用既有 `_emit_intervene`(已脱敏);摘要只含发言文本截断 |
| **II. 中文为产品语言** | 同上 | ✅ 发声与摘要全中文 |
| **III. 降级而非崩溃** | 三能力均可能在热路径注入 | ✅ FR-004 全程 try/except 降级回关闭行为;budget 软着陆本身就是「partial 而非 failed」的降级体现 |
| **IV. Coordinator/Reviewer 职责边界** | budget 检测/收尾在 observer;yesman 改 reviewer prompt | ✅ 原则 IV 明列「**预算逼近时在群里发声**」为合法 observer 流程纠偏职责(line 71);budget 收尾走既有 gatekeeper **纯规则**(gate.check);**不**新增 LLM 决策(rolling 默认纯规则折叠,LLM 注入点不交付);yesman 是 Reviewer「质量验证」本职的 prompt 强化,不让 Coordinator 审质量 |
| **V. Agent 自治与隔离** | 无 agentDir / 进程模型改动 | ✅ 不涉及 |
| **灰度(治理)** | v2-only 行为须有回退开关 | ✅ 三 flag 默认关 = 回退到 P7 |

**结论:无需新宪章修订**(v1.1.0 已够)。budget 是 §9.4.7 决策 1「Coordinator 纯规则」范畴内的纯规则计数 +
复用 gatekeeper;rolling 默认纯规则(不触碰 drift 的受限 LLM 例外);yesman 是 prompt 工程。
Complexity Tracking 留空(无违规)。

## Project Structure

### Documentation (this feature)

```text
specs/008-ops-safety-net/
├── plan.md              # 本文件
├── research.md          # Phase 0 输出(本次决策 + 现状核实)
├── data-model.md        # Phase 1 输出(flag/state 字段/折叠算法/yesman 块)
├── quickstart.md        # Phase 1 输出(pytest + 真 LLM 端到端步骤)
├── checklists/
│   └── requirements.md  # /speckit-specify 已生成
└── tasks.md             # /speckit-tasks 输出(本命令不创建)
# contracts/ skipped — 复用 P1 v2 事件 schema(CoordinatorIntervene kind=budget 已存在);
#   三能力均为内部行为/prompt 变化,无新对外接口
```

### Source Code (repository root)

```text
apps/web-backend/app/orchestrator/
├── subscription.py            # +V2_BUDGET_CAP / V2_ROLLING_SUMMARY / V2_SUMMARY_THRESHOLD / V2_YESMAN_DEFENSE + __all__
├── harness.py                 # HarnessState.{spent_tokens, budget_exceeded};_run_step 后累计 token;
│                              #   force_run_v2 / SPEAK 派发开头 budget_exceeded 短路
├── coordinator_observer.py    # _loop 每拍 budget 检测 → _on_budget_exceeded(emit intervene(budget)
│                              #   + 复用 gate.check + _set_done) ;_budget_landed 去重标志
└── pipeline.py                # _transcript_block 折叠(超阈值);_yesman_block();
                               #   _budget_enabled/_rolling_enabled/_yesman_enabled helper(每次读 subscription)
                               #   QUICK_REVIEW_PROMPT 注入 yesman 段

apps/web-backend/tests/orchestrator/
├── test_p8_budget.py          # US1:累计/触顶/软着陆/产物保留/去重/派发短路(ScriptedBackend)
├── test_p8_rolling.py         # US2:超阈值折叠/未超原样/降级空串/阈值=1 边界
├── test_p8_yesman.py          # US3:on 含对立质疑段 / off 不含
└── test_v1_regression.py      # 扩:三 flag unset → prompt/行为字段级与 P7 一致
```

**Structure Decision**: 沿用 v2 harness 既有四文件布局(subscription=flag SSOT、harness=状态与派发、
coordinator_observer=watchdog、pipeline=prompt 构造)。不新建模块:rolling 折叠就地在 `_transcript_block`,
budget 软着陆复用 observer 既有 `gate.check`/`_emit_intervene`/`_set_done`。`RollingSummarizer` 注入抽象
仅在 research 评估;若引入则极轻(同 `DriftJudge` set/get default 范式),默认纯规则不交付 LLM 实现。

## Complexity Tracking

> 无 Constitution Check 违规,本节留空。
