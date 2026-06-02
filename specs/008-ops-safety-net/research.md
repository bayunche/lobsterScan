# Phase 0 Research: P8 — 运营兜底

**Date**: 2026-06-02 | **Branch**: `008-ops-safety-net`

Technical Context 无 `NEEDS CLARIFICATION`(设计稿已定调)。本文件记录 5 个关键技术决策 + 现状核实。

---

## 决策 1 — 预算累计点与硬上限的「硬」实现

**Decision**: 在 `HarnessState` 新增 `spent_tokens: int = 0` 与 `budget_exceeded: bool = False`。
累计点放 `_run_step`:每个 step 拿到 `TurnResult` 后,`st.spent_tokens += s.total_tokens`(主 turn 汇集点);
reviewer 质量审 / `_quick_review` turn 也补计同样数额。检测放 observer `_loop` 每拍:
`if cap > 0 and state.spent_tokens >= cap → _on_budget_exceeded()`。「硬」靠 `budget_exceeded` 标志:
`force_run_v2` 与 `handle_v2_event` 的 SPEAK 真跑分支开头检查该标志 → True 则直接 return,不启动新 turn。

**Rationale**:
- `_run_step` 是所有 v2 step turn 的唯一汇集点(P5 已把 `__state__` 塞进 prev,observer 也持有 state),
  累计在此最完整且无需新订阅。
- 检测复用 observer 既有 0.5s 轮询拍(`OBSERVER_TICK_SEC`),零新增轮询、零新增 task。
- 标志短路而非「取消正在跑的 turn」:正在运行的 step 允许跑完(其成果保留,符合 FR-010/边界),
  只阻止**新**环节——既是「硬」(不再消耗)又不丢正在产出的成果。

**Alternatives considered**:
- 在 `report_usage`(pipeline 级、且 POST admin)累计:它不持有 HarnessState、且 v1 也调,污染零回归边界。否。
- 每个 emit 时检测:emit 高频且 budget 检测含 gate.check(读文件),放高频路径不划算;observer 拍足够及时。否。
- 触顶即 `_end` 硬停(用户问题里的「硬停」选项):用户已选**软着陆**,丢弃可完成的收尾产物违背 FR-010。否。

---

## 决策 2 — budget 软着陆复用既有 gatekeeper

**Decision**: `_on_budget_exceeded()`:① 置 `state.budget_exceeded = True`;② emit `CoordinatorIntervene(kind="budget")`
(业务化中文,如「为控制生成开销,我先用现有内容收尾」);③ 跑 `self.gate.check(task_id)`:
`passed → _set_done("done")`,否则 `_set_done("partial")`;④ `_budget_landed` 标志保证只跑一次。

**Rationale**: gatekeeper(`ArtifactGate.check` 4 核心 artifact 完整性)是 P3 已落地的纯规则收尾判定,
budget 软着陆与 quiescence 收尾**同一口径**(齐→done/不齐→partial),复用即一致、零新逻辑、守原则 IV(纯规则)。
`CoordinatorIntervene(kind="budget")` 的 kind 在 P1 schema(events_v2)早已预留(docs §9.4 示例列 `budget`)。

**Alternatives considered**:
- 新写一套 budget 专属收尾判定:与 quiescence 收尾口径漂移风险,违 DRY。否。
- budget 触顶后仍允许 quiescence 自然收尾:无法保证「停启新环节」(FR-008),且可能继续消耗。否。

**现状核实**: `coordinator_observer.py` 已有 `gate: ArtifactGate`、`_set_done(reason)`、
`_emit_intervene(kind, text)`(已脱敏、`text[:200]`)、`_task_id()`。`_on_budget_exceeded` 全部复用,无新依赖。
`events_v2.CoordinatorIntervene` 的 `kind` 字段已接受 `budget`(P1 schema 未对 kind 做白名单硬校验需 Phase 1 核对)。

---

## 决策 3 — rolling summary 纯规则折叠(决策 B)

**Decision**: 折叠就地在 `_transcript_block`。当 `_rolling_enabled()` 且 `len(recent_all) > V2_SUMMARY_THRESHOLD`:
保留尾部 `K`(= `V2_TRANSCRIPT_K`)条逐条渲染,把更早的 `N = len - K` 条折叠为**一行**确定性摘要:
`（前 N 条发言已折叠:<把这 N 条文本拼接后截断到 ~200 字>）`。纯字符串操作,不调 LLM。
预留 `RollingSummarizer` 抽象(`get/set_default_rolling_summarizer`,默认 `RuleBasedRollingSummarizer`),
但本期只交付规则实现(同 P3 `NoDriftJudge` mock-first 范式)。

**Rationale**:
- `_transcript_block` 是 P5 唯一渲染「群聊上下文」的入口,且已从 observer 的 `_recent` 取数,折叠在此最自然。
- 纯规则折叠确定性、可单测、不引入新 LLM 决策权(守原则 IV);同时**反向减 prompt token**,与 budget 互补。
- 摘要只含发言文本(本就脱敏的 agent.speak.text)截断,不含 id,守原则 I。

**Alternatives considered**:
- 真 LLM 摘要:成本/延迟 + 触碰原则 IV(新 LLM 决策);列为非目标,仅留注入点。否(本期)。
- 折叠到固定字符预算而非条数:条数阈值更直观可测,且与 K 对齐;字符预算留给未来。否。

**现状核实**: `_transcript_block(state, k)` 现取 `recent = _recent[-k:]`(已截断到 k 条)。
折叠需改为:先取**全量** `_recent`,若超阈值则 `tail = _recent[-K:]` + 一行 fold(头部 `_recent[:-K]`)。
关闭(默认)时维持 `_recent[-k:]` 现状,逐字段零回归。

---

## 决策 4 — yes-man 防御为纯 prompt 注入

**Decision**: 新增 `_yesman_block() -> str`(对立质疑指令段)。`_yesman_enabled()` 为真时,
把该段拼进审校路径的 prompt:`QUICK_REVIEW_PROMPT`(pipeline `_quick_review`)以及 harness 的质量审 prompt
(P4 `_reviewer_quality_review`,若其复用 `_quick_review`/`QUICK_REVIEW_PROMPT` 则一处覆盖)。
内容要点:主动找瑕疵、假设作者可能夸大、对不达标如实指出、禁止「看上去 OK」式放行——抵消现有
「不要过度严格:除非真的有明显缺陷再 reject」的松绑。

**Rationale**: 纯措辞、最低风险、最易单测(检字符串)。是 Reviewer「质量验证」本职的强化(守原则 IV,
不让 Coordinator 审质量)。flag 关闭时一字不拼,逐字段零回归。

**Alternatives considered**:
- 改 reviewer 的 system prompt 文件(workspaces):跨 admin 同步、影响 v1、难单测。否,运行期 prompt 注入更干净。
- 引入独立「红队 agent」:超 P8 范围(YAGNI),也触碰 agent 拓扑。否。

**现状核实**: `_quick_review` 用 `QUICK_REVIEW_PROMPT.format(...)`(pipeline ~2759 行);P4 reviewer 质量审在
harness `_reviewer_quality_review` 经 `_to_reviewer_verdict` 适配——Phase 1 data-model 需核对其 prompt 是否复用
`QUICK_REVIEW_PROMPT`,以决定注入是「一处」还是「两处」。

---

## 决策 5 — 四个 flag 的形态与读取(零回归与正交)

**Decision**: 全部进 `subscription.py`(v2 flag SSOT),env var 可覆盖,默认关:
- `V2_BUDGET_CAP: int = int(os.environ.get("V2_BUDGET_CAP", "0"))` —— 0 = off/无限
- `V2_ROLLING_SUMMARY: str = os.environ.get("V2_ROLLING_SUMMARY", "off")` —— off/on
- `V2_SUMMARY_THRESHOLD: int = int(os.environ.get("V2_SUMMARY_THRESHOLD", "20"))`
- `V2_YESMAN_DEFENSE: str = os.environ.get("V2_YESMAN_DEFENSE", "off")` —— off/on
pipeline 侧 3 个 `_*_enabled()` helper **每次读** `subscription.X`(支持测试 monkeypatch),
异常按关闭降级(同 `_envelope_enabled`/`_fanout_enabled` 范式)。

**Rationale**: 沿用 P5(`V2_PROMPT_MODE`)/P6(`V2_FANOUT`)成熟范式:flag 集中、每次读支持 monkeypatch、
默认关短路。三 flag 互相独立读取 → 天然正交,任意组合可生效(FR-003)。

**Alternatives considered**:
- additive 无 flag(P7 范式):用户已选 **flag-gated**;且 budget/rolling/yesman 改 LLM 行为,非纯 UI 增量字段,需可回退。否。
- 单一总开关 `V2_OPS_SAFETY`:三能力独立价值/独立验收,合并开关丧失正交性与按需灰度。否。

---

## 零回归与降级总纲(贯穿)

- 四 flag 全默认关 → `_run_step` 不累计(或累计但无人读)、observer budget 检测 `cap==0` 短路、
  `_transcript_block` 走 `_recent[-k:]` 现状、审校 prompt 不拼 yesman 段 ⇒ **逐字段 = P7**(FR-002 / SC-001)。
- 三能力各自 try/except:budget 检测异常仅 log(observer `_loop` 已有 per-tick try/except 兜底);
  rolling 折叠异常 → `_transcript_block` 既有 except 返回空串;yesman 注入异常 → 退回原 prompt。绝不阻塞报告(FR-004 / SC-006)。
- 与 `is_v2` 正交:flag 仅在 v2 路径有渲染/检测入口(observer/transcript 仅 is_v2 构造),v1 路径根本不触达。
