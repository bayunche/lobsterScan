# Phase 1 — Data Model

**Feature**: Reviewer 双轨 + verdict.fail 修复闭环（P4）

**Module**: 新增 `apps/web-backend/app/orchestrator/process_review.py` + `harness.py` / `coordinator_observer.py` 扩展

---

## 总览

```text
process_review.py（新增 ~130 行）
├── ProcessReviewResult       # dataclass：passed + findings + fix_targets
├── ProcessReviewer           # 3 纯规则校验器：版本一致 / 依赖图 / 参与度
└── _process_verdict(result)  # ProcessReviewResult → ReviewerVerdict(process_logic)

harness.py 扩
├── AgentWorker._reviewed: set[tuple[str,int]]   # 质量轨版本去重
├── AgentWorker.handle_v2_event：reviewer 特化早期分支
├── AgentWorker._reviewer_handle(event)          # 质量审 / silent 分流
├── AgentWorker._reviewer_quality_review(event)  # ArtifactUpdate → _quick_review → verdict
└── _to_reviewer_verdict / _pad_suggestions      # _quick_review → ReviewerVerdict 适配

coordinator_observer.py 扩
├── REVIEW_FIX_MAX_RETRY 常量
├── CoordinatorObserver._fix_retries: dict[str,int]   # 修复次数(按 fix_agent)
├── CoordinatorObserver._process_reviewed: bool       # 流程逻辑审去重(收尾一次)
├── CoordinatorObserver._on_verdict(event)            # 监听 reviewer.verdict(fail)→ 修复闭环
└── CoordinatorObserver._on_quiescence 改造           # 纳入流程逻辑审 + verdict 综合决策

pipeline.py 小调
└── _quick_review / AGENT_TO_STEP 被 reviewer 质量轨 lazy import 复用；_emit_v2_finalization 示例清理
```

所有新类型:stdlib + dataclass + 复用 P1 `ReviewerVerdict`/`Finding`(无新 schema)。

---

## 1. ProcessReviewResult（dataclass）

```python
@dataclass(frozen=True)
class ProcessReviewResult:
    passed: bool
    findings: tuple[Finding, ...] = ()          # 复用 P1 events_v2.Finding
    fix_targets: tuple[str, ...] = ()           # 违例 artifact 的 producer agent_id(修复定位)
```

---

## 2. ProcessReviewer（3 纯规则）

```python
class ProcessReviewer:
    """流程逻辑轨：3 项确定性规则。无 LLM。读 artifact meta + events。"""

    def check(self, task_id: str, events: list) -> ProcessReviewResult:
        findings: list[Finding] = []
        fix: set[str] = set()
        self._check_version_consistency(task_id, findings, fix)   # 规则①
        self._check_dependency_order(events, findings, fix)       # 规则②
        self._check_participation(task_id, findings, fix)         # 规则③
        return ProcessReviewResult(passed=not findings,
                                   findings=tuple(findings), fix_targets=tuple(fix))
```

| 规则 | 数据来源 | fail 条件 |
|---|---|---|
| **① 版本一致** | 4 核心 artifact `__meta__.base_version`(`artifacts_v2`)| 某 artifact 的 base_version 指向不存在的上游版本 |
| **② 依赖图** | events_log 里 `artifact.update` 的出现序 | 某 artifact 早于其依赖出现(如 Outline 早于 ReportCore)|
| **③ 参与度** | `ArtifactGate` + events | 某核心 artifact 始终未产出(producer 缺席)|

findings 带违例 artifact_id;`fix_targets` = `ARTIFACT_PRODUCER[违例 id]`(修复定位,F2)。
降级(FR-019):任一规则计算异常 → 跳过该规则(不计 finding),不抛。

---

## 3. _process_verdict（ProcessReviewResult → ReviewerVerdict）

```python
def _process_verdict(result: ProcessReviewResult, task_id: str) -> ReviewerVerdict:
    return ReviewerVerdict(
        task_id=task_id,
        verdict="pass" if result.passed else "fail",
        dimension="process_logic",
        findings=list(result.findings),
        suggested_fix_agent=(result.fix_targets[0] if result.fix_targets else None),
        suggestions=_pad_suggestions([f.what for f in result.findings]),  # 凑 ≥3
    )
```

---

## 4. AgentWorker 扩展（reviewer 特化）

```python
class AgentWorker:
    def __init__(self, ...):
        # ... P2/P3 字段
        self._reviewed: set[tuple[str, int]] = set()   # 质量轨版本去重(仅 reviewer 用)

    async def handle_v2_event(self, event):
        # P4：reviewer 特化早期分支(不走 P3 work-driver)
        if self.agent_id == "reviewer" and self.state.is_v2:
            await self._reviewer_handle(event)
            return
        # ... P3 work-driver 原逻辑(其它 agent 完全不变)

    async def _reviewer_handle(self, event):
        from .events_v2 import ArtifactUpdate, AgentSilent
        if isinstance(event, ArtifactUpdate):
            await self._reviewer_quality_review(event)      # 质量轨即时审
        else:
            # 被 mention 等：silent,不跑 step(决策 3)
            await self.state.emit_v2(AgentSilent(
                task_id=self.state.run.task_id, **{"from": "reviewer"},
                reply_to=getattr(event, "message_id", None),
                reason="持续审校中,收尾给结论",
            ))

    async def _reviewer_quality_review(self, event):
        from .pipeline import AGENT_TO_STEP, _quick_review
        key = (event.id, event.version)
        if key in self._reviewed:           # FR-004 版本去重
            return
        self._reviewed.add(key)
        step = self.state.by_key.get(AGENT_TO_STEP.get(event.producer, ""))
        if step is None or getattr(step, "output_json", None) is None:
            return
        qr = await _quick_review(step, self.state.run)
        verdict = _to_reviewer_verdict(qr, dimension="quality",
                                       fix_agent=event.producer,
                                       task_id=self.state.run.task_id)
        await self.state.emit_v2(verdict)
```

**v1 零回归**:`handle_v2_event` 仅 v2 调用(inbox 仅 v2 构造);reviewer 特化分支不影响 v1(`_gate_review` 仍按 v1 跑)。其它 8 agent 的 work-driver 路径零改动。

---

## 5. _to_reviewer_verdict / _pad_suggestions 适配

```python
def _pad_suggestions(seed: list[str]) -> list[str]:
    """凑 ≥3 条建议(满足 P1 ReviewerVerdict.suggestions min_length=3)。"""
    out = [s for s in seed if s][:3]
    fillers = ["保持术语与数据口径一致", "核对与原始材料的对应关系", "收尾前再整体校一遍"]
    i = 0
    while len(out) < 3:
        out.append(fillers[i]); i += 1
    return out

def _to_reviewer_verdict(qr: dict, dimension: str, fix_agent: str, task_id: str) -> ReviewerVerdict:
    from .events_v2 import ReviewerVerdict, Finding
    accept = bool(qr.get("accept", True))
    reason = (qr.get("reason") or "").strip()
    comment = (qr.get("comment") or "").strip()
    findings = [] if accept else [Finding(severity="med", what=(reason or comment or "需改进")[:200])]
    return ReviewerVerdict(
        task_id=task_id, verdict="pass" if accept else "fail",
        dimension=dimension, findings=findings,
        suggested_fix_agent=None if accept else fix_agent,
        suggestions=_pad_suggestions([reason or comment]),
    )
```

---

## 6. CoordinatorObserver 扩展

```python
@dataclass
class CoordinatorObserver:
    # ... P3 字段
    _fix_retries: dict[str, int] = field(default_factory=dict)   # 按 fix_agent 修复计数
    _process_reviewed: bool = False                               # 流程逻辑审收尾去重

    async def start(self):
        if self._task is None:
            self.state.bus.on("agent.speak", self._collect_speak)   # P3
            self.state.bus.on("reviewer.verdict", self._on_verdict) # P4
            self._task = asyncio.create_task(self._loop())

    async def _on_verdict(self, event):
        """监听 reviewer.verdict(fail)→ 转写 + 重置 step + 重新激活修复(FR-009~013)。"""
        payload = getattr(event, "payload", None) or {}
        if payload.get("verdict") != "fail":
            return
        fix_agent = payload.get("suggested_fix_agent")
        if not fix_agent or fix_agent not in self.workers:    # FR-013
            return
        if self._fix_retries.get(fix_agent, 0) >= REVIEW_FIX_MAX_RETRY:  # FR-011
            return
        self._fix_retries[fix_agent] = self._fix_retries.get(fix_agent, 0) + 1
        try:
            await self._emit_intervene("gate_reject", f"{self._display(fix_agent)} 那块再打磨一下。")
            from .pipeline import AGENT_TO_STEP
            step = self.state.by_key.get(AGENT_TO_STEP.get(fix_agent, ""))
            if step is not None:
                step.status = "needs_fix"     # 解除 P3 work-driver 去重
            self.state.inflight_steps += 1
            asyncio.create_task(self.workers[fix_agent].force_run_v2())
        except Exception as e:  # noqa: BLE001 — FR-019
            log.warning("verdict fix dispatch crashed: %s", e)

    async def _on_quiescence(self):
        # P4 ①：流程逻辑审(收尾一次)
        if not self._process_reviewed:
            self._process_reviewed = True
            try:
                from .process_review import ProcessReviewer, _process_verdict
                result = ProcessReviewer().check(self._task_id(), self.state.events_log)
                await self.state.emit_v2(_process_verdict(result, self._task_id()))
                # 流程逻辑 fail 经 bus → _on_verdict 自动触发修复(若可定位)
            except Exception as e:  # noqa: BLE001 — FR-019
                log.warning("process review crashed: %s", e)
            return   # 本拍先出 verdict;下一拍再综合决策(让修复有机会触发)

        # P4 ②：gatekeeper 综合决策(P3 完整性 + 未解决 fail)
        gate = self.gate.check(self._task_id())
        unresolved = any(v >= REVIEW_FIX_MAX_RETRY for v in self._fix_retries.values())
        if gate.passed and not unresolved:
            await self._emit_intervene("gate_pass", "产物齐了、审校也过了,我来收尾。")
            self._set_done("done")
            return
        # 不齐/有未解决 → P3 stagnation 激活 or 修复仍在跑 or 转 partial(沿用 P3 兜底 + 上限)
        activated = await self._activate_ready_silent_workers()
        if activated:
            self._stagnation_retries = 0
            await self._emit_intervene("stagnation", "流程好像卡住了,我来推进一下。")
            return
        self._stagnation_retries += 1
        if self._stagnation_retries >= STAGNATION_MAX_RETRY:
            miss = "、".join(self._display(ARTIFACT_PRODUCER.get(m, m)) for m in gate.missing)
            reason = f"还差 {miss} 的部分,先按现有内容交付。" if gate.missing else "有审校项未通过,先按现有内容交付。"
            await self._emit_intervene("gate_reject", reason)
            self._set_done("partial")
```

**关键**:流程逻辑审在第一次 quiescence 出 verdict(emit → bus → `_on_verdict` 可能触发修复 → 非 quiescent),`return` 让下一拍再综合;修复跑完再 quiescent 时综合决策。`_process_reviewed` 防重复审。

---

## 7. 配置常量（coordinator_observer.py）

| 常量 | 默认 | 用途 |
|---|---|---|
| `REVIEW_FIX_MAX_RETRY` | 2 | 同一 fix_agent 的 verdict.fail 修复次数上限(FR-011) |

(沿用 P3 的 OBSERVER_TICK_SEC / STAGNATION_MAX_RETRY 等。)

---

## 状态转移

### v2 任务 Reviewer 双轨 + 修复

```
链式推进中,每产出核心 artifact:
  → emit artifact.update
      → reviewer 订阅唤醒 → _reviewer_handle → 质量审(_quick_review)
          → emit ReviewerVerdict(quality, pass/fail)
              ├─ pass → 无事
              └─ fail → bus → observer._on_verdict
                          → 转写 intervene 点名 producer + 重置 step needs_fix
                          → force_run_v2 修复(上限 REVIEW_FIX_MAX_RETRY)
                              → 修复重产 artifact → 质量轨重审(闭环)

链式终止 → quiescence:
  observer._on_quiescence:
    ① 流程逻辑审(ProcessReviewer 3 规则)→ emit ReviewerVerdict(process_logic)
        → fail 经 bus → _on_verdict 触发修复(若可定位)
    ② 综合决策:
        · 产物齐 + 无未解决 fail → gate_pass + done
        · 有未解决 fail / 缺产物 → stagnation 激活 / 修复 / gate_reject + partial
```

### v1 任务（不变）

reviewer 走 v1 `_gate_review`(REVIEW_GATES 4 step 后 `_quick_review`,fail 重做 1 次);
无 ReviewerVerdict / 双轨 / 修复闭环(handle_v2_event 不被调用)。

---

## 验证规则汇总

| 来源 | 规则 |
|---|---|
| FR-001/002 | reviewer 订阅 artifact.update → _quick_review → emit verdict(quality) |
| FR-003 | fail 带 suggested_fix_agent=producer;reviewer 不直接 @ |
| FR-004 | _reviewed 版本去重 |
| FR-005/006/007/008 | ProcessReviewer 收尾 3 纯规则 → verdict(process_logic);无 LLM |
| FR-009~013 | observer._on_verdict 转写 + 重置 + force_run_v2 + 上限 + fix_agent 缺失不修 |
| FR-014/015 | _on_quiescence 双因子(完整性 + verdict)决策 done/partial |
| FR-016/017 | 全 is_v2 守卫;v1 _gate_review 不变 |
| FR-018 | verdict/intervene 文案业务化中文(_display)|
| FR-019 | reviewer 审/流程审/修复/收尾全 try/except 降级 |
| FR-020 | Reviewer 只出 verdict;修复由 producer 自跑(force_run_v2 跑的是 producer 的 step)|

---

## 已知未决（留给后续）

- **质量轨真 LLM**(report-reviewer subprocess)受 Windows issue 阻塞;本期 ScriptedBackend 脚本化 reviewer turn 测试
- **跨引用一致性**:归质量轨 LLM 或 P5,不进 ProcessReviewer
- **InterveneKind "fix"**:本期复用 gate_reject 表达"打回";若后续需要独立语义再扩 P1 枚举
