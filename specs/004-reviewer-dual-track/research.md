# Phase 0 — Research & Technical Decisions

**Feature**: Reviewer 双轨(质量 + 流程逻辑)+ verdict.fail 修复闭环（P4）

**Branch**: `004-reviewer-dual-track` | **Date**: 2026-05-30

---

## 现状基线（P3 之后,本期改造起点）

- `pipeline._quick_review(s: StepState, run)`(line 2491)→ `{accept, comment, reason}`;v1 由
  `_gate_review`(REVIEW_GATES 4 step 后)调用。**入参是 StepState**(看 `output_json`),不是 artifact。
- `coordinator_observer._on_quiescence`(line 235)= P3 的 gatekeeper + stagnation;P4 在此插入流程逻辑审。
- `ReviewerVerdict`(P1 schema):`verdict`(pass/fail) / `dimension`(quality/process_logic/both) /
  `findings` / `suggested_fix_agent` / `suggestions`(**min_length=3**)。
- P3:reviewer 被 mention(video→review)→ work-driver 跑 review step;P2 `WORKER_PROFILE["reviewer"]`
  订阅 `mention_includes("reviewer")` + `artifact_id_in({4 核心})`,`requires=()`。

---

## 决策汇总

| # | 决策点 | 选择 |
|---|---|---|
| 0 | reviewer 全程审校的载体 | `handle_v2_event` **reviewer 特化分支**(早期拦截,不走 P3 work-driver) |
| 1 | 质量轨怎么审 artifact | reviewer 被 `ArtifactUpdate(producer=A)` 触发 → `AGENT_TO_STEP[A]` → `by_key[step]` → `_quick_review(step)` → 转 `ReviewerVerdict(quality)` |
| 2 | `_quick_review` → `ReviewerVerdict` 映射 | `accept→verdict`;`reason→findings`;`suggested_fix_agent=producer`;`suggestions` 凑 ≥3(满足 schema) |
| 3 | reviewer 被 mention(非 artifact)行为 | **silent**(reviewer 不再跑链式 step;审校全由质量轨 + 流程逻辑轨承担) |
| 4 | 质量轨版本去重 | reviewer 维护 `reviewed: set[(artifact_id, version)]`;已审版本跳过 |
| 5 | 流程逻辑轨实现 | 新模块 `process_review.py` 的 `ProcessReviewer`(3 纯规则,读 artifact meta + events) |
| 6 | verdict.fail 修复闭环入口 | observer `bus.on("reviewer.verdict")` 监听 → fail → 转写 intervene + 重置 step + `force_run_v2` |
| 7 | `_on_quiescence` 改造 | 先流程逻辑审 → 综合 [artifact 完整性 + quality verdicts + process_logic verdict] 决策 |
| 8 | v1 零开销 + 零回归 | 全 `is_v2` 守卫;v1 `_gate_review`/REVIEW_GATES 不动 |

---

## 0. reviewer 全程审校载体 —— handle_v2_event 特化分支

**Decision**: `AgentWorker.handle_v2_event` 在 `decide_to_speak` 之前加 **reviewer 特化分支**:
`self.agent_id == "reviewer"` 时,按 event 类型分流,**不走 P3 work-driver**(SPEAK→跑 step):

```python
async def handle_v2_event(self, event):
    if self.agent_id == "reviewer" and self.state.is_v2:
        await self._reviewer_handle(event)   # 质量审 / silent
        return
    # ... P3 work-driver 原逻辑(其它 agent 不变)
```

**Rationale**:
- reviewer 在 P4 不再是链式 work-driver 一环(spec US2/角色转变);它是订阅审校者。
- 早期分支隔离,P3 work-driver 路径(其它 8 agent)零影响。
- reviewer 特化逻辑集中一处(`_reviewer_handle`),可读可测。

**`_reviewer_handle(event)`**:
- `isinstance(event, ArtifactUpdate)` → 质量审(决策 1)
- 否则(被 mention 的 AgentSpeak 等)→ **silent**(决策 3):emit `AgentSilent(reason="持续审校中,收尾给结论")`,不跑 step

**Alternatives**:
- 新建 `ReviewerWorker` 子类:类型分叉 + 构造逻辑要改,改动大。否,用分支。
- 让 reviewer 仍走 work-driver 跑 review step:回到 P3,reviewer 产"review 结果"但不是核心 artifact,且与双轨重复。否。

---

## 1. 质量轨怎么审 artifact

**Decision**: reviewer 被 `ArtifactUpdate(id=X, producer=A, version=V)` 触发 → 用 `AGENT_TO_STEP[A]`
找 `by_key[step_key]` 的 StepState → `_quick_review(step, run)` → 转 `ReviewerVerdict(quality)`。

**Rationale**:
- artifact 内容 = producer step 的 `output_json`;`_quick_review` 正是看 `StepState.output_json`,直接复用(决策 B,不加抽象)。
- `ArtifactUpdate.producer` 是 agent_id;`pipeline.AGENT_TO_STEP` 反查 step_key。

**实现要点**:
```python
async def _reviewer_quality_review(self, event: ArtifactUpdate):
    from .pipeline import AGENT_TO_STEP, _quick_review
    if (event.id, event.version) in self._reviewed:      # 决策 4 版本去重
        return
    self._reviewed.add((event.id, event.version))
    step = self.state.by_key.get(AGENT_TO_STEP.get(event.producer, ""))
    if step is None: return
    qr = await _quick_review(step, self.state.run)        # {accept, comment, reason}
    verdict = _to_reviewer_verdict(qr, dimension="quality", fix_agent=event.producer)
    await self.state.emit_v2(verdict)
```

`self._reviewed: set[tuple[str,int]]` 是 reviewer worker 的新字段(仅 v2 用)。

---

## 2. _quick_review → ReviewerVerdict 映射

**Decision**: 适配函数 `_to_reviewer_verdict(qr, dimension, fix_agent)`:

```python
def _to_reviewer_verdict(qr, dimension, fix_agent, task_id):
    accept = qr.get("accept", True)
    reason = qr.get("reason") or ""
    comment = qr.get("comment") or ""
    suggestions = _pad_suggestions([reason] if reason else [comment])  # 凑 ≥3
    findings = [] if accept else [Finding(severity="med", what=reason[:200] or comment[:200])]
    return ReviewerVerdict(
        task_id=task_id, verdict="pass" if accept else "fail",
        dimension=dimension,
        findings=findings,
        suggested_fix_agent=None if accept else fix_agent,
        suggestions=suggestions,
    )
```

**`suggestions` 凑 ≥3**(满足 P1 schema `min_length=3`):`_pad_suggestions(seed)` —— 有内容的放前面,不足 3 条用通用建议补(如"保持术语一致 / 核对数据 / 收尾再校一次")。

**Rationale**:
- P1 schema 强制 reviewer 给 ≥3 建议(reviewer 本职就该多建议);P4 遵守,不改 schema。
- pass 时也凑 3 条(肯定性"保持"建议),fail 时具体问题 + 补足。

**Alternatives**:
- 改 P1 `ReviewerVerdict.suggestions` min_length 3→0:动 P1 schema,影响 P1 测试 + 语义。否。

---

## 3. reviewer 被 mention 时 silent（不跑 step）

**Decision**: reviewer 收到非 ArtifactUpdate 事件(被 mention 的 AgentSpeak)→ emit `AgentSilent`,不跑 step。

**Rationale**:
- reviewer 不产核心 artifact、不是链式一环;链式到 review 的那一棒(video→review mention)不应让 reviewer 跑 work-driver。
- silent 让链自然终止 → quiescence → observer 触发流程逻辑审(决策 7)。
- reviewer 的审校全由质量轨(artifact 即时)+ 流程逻辑轨(收尾)承担,不需 mention 驱动。

---

## 4. 质量轨版本去重

**Decision**: reviewer worker 新字段 `_reviewed: set[tuple[str, int]]`(artifact_id, version);质量审前查,审后加。

**Rationale**: spec FR-004 + SC-002(同版本 0 重审)。同一 artifact 多次 `artifact.update`(如 base_version 推进)只审最新未审版本;同版本不重审(省 LLM)。

---

## 5. 流程逻辑轨 —— ProcessReviewer（新模块）

**Decision**: 新模块 `process_review.py`,`ProcessReviewer.check(task_id, events) -> ProcessReviewResult`,3 纯规则:

| 规则 | 数据来源 | 判定 |
|---|---|---|
| 版本一致 | 4 核心 artifact 的 `__meta__`(`base_version` 链)| 下游引用的上游版本存在 |
| 依赖图 | events 里 artifact.update 的时间序 | 产出顺序符合 MaterialPool→ReportCore→Outline→Script |
| 参与度 | events + ArtifactGate | 每个核心 artifact 都产出(producer agent 参与)|

**Rationale**:
- 纯规则(决策 A),独立模块(像 P3 `ArtifactGate`),便于测试。
- 读 artifact meta(`artifacts_v2` 已存 `__meta__`)+ events_log(observer 可访问)。

**`ProcessReviewResult`**: `{passed: bool, findings: list[Finding]}`。observer 据此 emit `ReviewerVerdict(process_logic)`。

---

## 6. verdict.fail 修复闭环

**Decision**: observer `start()` 时 `bus.on("reviewer.verdict", self._on_verdict)`;`_on_verdict` 处理 fail:

```python
async def _on_verdict(self, event):
    payload = event.payload or {}
    if payload.get("verdict") != "fail": return
    fix_agent = payload.get("suggested_fix_agent")
    if not fix_agent or fix_agent not in self.workers:  # FR-013 缺失/无效不修
        return
    if self._fix_retries.get(fix_agent, 0) >= REVIEW_FIX_MAX_RETRY:  # FR-011 上限
        return
    self._fix_retries[fix_agent] = self._fix_retries.get(fix_agent, 0) + 1
    # 转写 intervene 点名(Reviewer 不直接 @,宪章 IV)
    await self._emit_intervene("gate_reject", f"{self._display(fix_agent)} 那块还要再打磨一下。")
    # 重置 step status 解除 work-driver 去重 + 重新激活
    step = self.state.by_key.get(AGENT_TO_STEP.get(fix_agent, ""))
    if step is not None:
        step.status = "needs_fix"
    self.state.inflight_steps += 1
    asyncio.create_task(self.workers[fix_agent].force_run_v2())
```

**Rationale**:
- bus.on 监听 `reviewer.verdict`(质量轨 + 流程逻辑轨的 fail 统一入口,FR-009)。
- 重置 `status="needs_fix"` 解除 P3 work-driver "success 跳过" 去重(handle_v2_event SPEAK 检查 `status=="success"`);`force_run_v2` 直接跑(绕 decide_to_speak)。
- `_fix_retries: dict[agent, int]` 上限(FR-011);`intervene` 转写(FR-009,Reviewer 不直接 @)。
- `intervene` 复用 P3 `kind`;`gate_reject` 语义最近(也可考虑新 kind,但 P1 枚举无"fix",复用 gate_reject 表达"打回")。

**Alternatives**:
- 直接重跑而不重置 status:`force_run_v2` 不查 status 能跑,但 quiescence 时 gatekeeper 会因 step 仍 success 误判已完成。重置 `needs_fix` 让 gatekeeper 看到"有待修"。
- 新 InterveneKind "fix":动 P1 schema。否,复用 gate_reject。

---

## 7. _on_quiescence 改造（gatekeeper 纳入 verdict）

**Decision**: `_on_quiescence` 在现有 artifact 完整性检查前/后,纳入流程逻辑审 + quality verdict 汇总:

```python
async def _on_quiescence(self):
    # ① 流程逻辑审(收尾触发,只一次)
    if not self._process_reviewed:
        self._process_reviewed = True
        result = ProcessReviewer().check(self._task_id(), self.state.events_log)
        await self.state.emit_v2(_process_verdict(result))   # ReviewerVerdict(process_logic)
        if not result.passed:
            ...  # 流程逻辑 fail 也走 _on_verdict 修复闭环(若可定位 fix_agent)
    # ② gatekeeper 综合决策(P3 完整性 + verdict)
    gate = self.gate.check(self._task_id())
    has_unresolved_fail = self._has_unresolved_fail()    # 达上限的 fail
    if gate.passed and not has_unresolved_fail:
        await self._emit_intervene("gate_pass", "产物齐了、审校也过了,我来收尾。")
        self._set_done("done")
    elif <可修> :
        ... # 触发修复(决策 6),不收尾
    else:
        await self._emit_intervene("gate_reject", "<缺失/未解决说明>")
        self._set_done("partial")
```

**Rationale**: spec FR-014/015 双因子收尾。流程逻辑审在收尾触发一次(`_process_reviewed` flag 去重)。quality verdict 的未解决 fail 通过 `_fix_retries` 达上限判定。

**注**:stagnation 激活(P3)与 P4 修复触发并存 —— stagnation 激活"依赖就绪却没产出"的,修复激活"产出了但 verdict.fail"的;两者都用 `force_run_v2`,不冲突。

---

## 8. v1 零开销 + 零回归

**Decision**: 全 `if is_v2:` / `agent_id=="reviewer" and is_v2` 守卫:
- `handle_v2_event` reviewer 特化分支仅 v2(handle_v2_event 本就只 v2 调用)
- observer `_on_verdict` / 流程逻辑审仅 v2(observer 仅 v2 构造)
- `_quick_review` 被 v2 质量轨复用,但 v1 `_gate_review` 路径不变(REVIEW_GATES 仍按 v1 跑)

**测试守护**:沿用 US1 红线 —— v1 events 无 `reviewer.verdict`/`needs_fix`;v1 `_gate_review` 行为字段级不变。

---

## 派生发现（Derived Findings）

### F1 — `_quick_review` 是 pipeline 私有,reviewer worker 需 lazy import

`_quick_review` / `AGENT_TO_STEP` 在 `pipeline.py`。reviewer worker(harness.py)调用需 lazy import(避免循环,同 P3 `_emit_v2_step_overlay` 模式)。

### F2 — 流程逻辑审的 fix_agent 定位

流程逻辑 fail(如版本不一致)的 `suggested_fix_agent` = 违例 artifact 的 producer。`ProcessReviewer` findings 带上违例 artifact_id → `ARTIFACT_PRODUCER[id]` 反查。无法定位时不触发修复(FR-013)。

### F3 — REVIEW_FIX_MAX_RETRY 与 STAGNATION_MAX_RETRY 独立

修复上限(P4)与 stagnation 上限(P3)是两个独立计数器;一个防"反复打回",一个防"无解死锁"。

### F4 — `_emit_v2_finalization` 的 ReviewerVerdict 示例移除

P3 已让 execute 不调 `_emit_v2_finalization`(observer 接管)。P4 进一步:该函数里的 ReviewerVerdict 示例(line 1896)在真双轨落地后无意义,可清理(或保留供 P1 test_v2_integration,不影响)。

---

## 阶段产出

完成本研究 → Phase 1(data-model + quickstart),见 [data-model.md](./data-model.md) 与 [quickstart.md](./quickstart.md)。
**contracts/ 跳过 —— P4 内部架构,复用 P1 `ReviewerVerdict` schema,无新对外 API/事件。**

**所有 NEEDS CLARIFICATION 状态:✅ 0 项遗留。**
