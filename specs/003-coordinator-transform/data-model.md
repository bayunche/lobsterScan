# Phase 1 — Data Model

**Feature**: Coordinator 转型 + subscription work-driver（P3）

**Module**: 新增 `apps/web-backend/app/orchestrator/coordinator_observer.py` + `harness.py` / `pipeline.py` 扩展

---

## 总览

```text
coordinator_observer.py（新增）
├── DriftVerdict           # dataclass：drift 判断结果
├── DriftJudge             # 抽象基类：judge(goal, recent_speaks) -> DriftVerdict
├── NoDriftJudge           # 默认实现：永远"未跑题"（mock-first，不调 LLM）
├── get/set_default_drift_judge  # 注入点（同 AgentBackend 模式）
├── ArtifactGate           # 4 核心 artifact 完整性校验
├── CoordinatorObserver    # watchdog：quiescence → gatekeeper/stagnation，周期 drift
└── 配置常量（OBSERVER_TICK_SEC / DRIFT_EVERY_N_TICK / STAGNATION_MAX_RETRY / DRIFT_RECENT_K）

harness.py 扩展
├── HarnessState.inflight_steps: int
├── HarnessState.bootstrapped: bool
├── HarnessState.observer: CoordinatorObserver | None
├── HarnessState.start_observer() / stop_observer()
├── AgentWorker.handle_v2_event  # SPEAK 分支：emit confirm → 真跑 _run_unlocked（work-driver）
├── Coordinator.on_handoff/on_failed/on_needs_help/on_needs_retry  # is_v2 short-circuit
└── run_harness  # is_v2：bootstrap material + start_observer；末尾 stop_observer

pipeline.py 调整
└── execute 的 v2 收尾：gatekeeper 接管后，_emit_v2_finalization 在 v2 收窄/跳过（避免双 gate）
```

所有新类型:stdlib + dataclass(与 P2 风格一致);无新外部依赖。

---

## 1. DriftVerdict（dataclass）

```python
@dataclass(frozen=True)
class DriftVerdict:
    drifted: bool          # 是否偏离原始目标
    restate_text: str = "" # drifted=True 时的复诵文案（业务化中文）
```

---

## 2. DriftJudge（抽象 + 默认实现 + 注入点）

```python
class DriftJudge(ABC):
    @abstractmethod
    async def judge(self, *, goal: str, recent_speaks: list[str]) -> DriftVerdict:
        """判断 recent_speaks 是否偏离 goal。纯只读，无副作用。"""


class NoDriftJudge(DriftJudge):
    """默认实现（mock-first）：永远未跑题。不调 LLM，不违宪（FR-016/017）。"""
    async def judge(self, *, goal, recent_speaks) -> DriftVerdict:
        return DriftVerdict(drifted=False)


# 注入点（同 agent_backend.get/set_default_backend 模式）
_default_drift_judge: DriftJudge = NoDriftJudge()
def get_default_drift_judge() -> DriftJudge: ...
def set_default_drift_judge(j: DriftJudge) -> None: ...  # 测试注入 mock
```

**真 LLM 实现 `LLMDriftJudge`** 留扩展点(受 Phase 0 宪章 + Windows issue 阻塞,本期不交付)。

**字段验证**:`recent_speaks` 取最近 `DRIFT_RECENT_K`(默认 5)条 `agent.speak.text`;`goal` 由 `TaskRun` 派生(report_type/audience/raw_text 摘要)。

---

## 3. ArtifactGate（核心 artifact 完整性校验）

```python
@dataclass
class GateResult:
    passed: bool
    missing: tuple[str, ...]   # 缺失的核心 artifact id

class ArtifactGate:
    CORE = ("MaterialPool", "ReportCore", "Outline", "Script")

    def check(self, task_id: str) -> GateResult:
        """4 核心 artifact latest 版本都 ≥1 → passed；否则列 missing。
        复用 artifacts_v2.next_version（latest = next_version - 1）。降级：异常算缺失。
        """
```

---

## 4. CoordinatorObserver（watchdog）

```python
@dataclass
class CoordinatorObserver:
    """v2 路径的 observer + gatekeeper watchdog。is_v2 时由 run_harness 构造启动。"""
    state: "HarnessState"
    workers: dict[str, "AgentWorker"]
    goal: str                              # 原始目标（drift 用）
    gate: ArtifactGate = field(default_factory=ArtifactGate)
    drift_judge: DriftJudge = field(default_factory=get_default_drift_judge)
    _task: asyncio.Task | None = None
    _tick: int = 0
    _stagnation_retries: int = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        # cancel + await（同 P2 consume_task 清理）

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(OBSERVER_TICK_SEC)        # 默认 0.5s
            self._tick += 1
            if self._is_quiescent():
                await self._on_quiescence()               # gatekeeper / stagnation
            if self._tick % DRIFT_EVERY_N_TICK == 0:      # 周期 drift
                await self._check_drift()

    def _is_quiescent(self) -> bool:
        return (
            self.state.bootstrapped
            and self.state.inflight_steps == 0
            and self.state.done is not None and not self.state.done.done()
            and all(w.inbox is None or w.inbox.empty() for w in self.workers.values())
        )

    async def _on_quiescence(self) -> None:
        result = self.gate.check(self.state.run.task_id)
        if result.passed:
            await self._emit_intervene("gate_pass", "全部产物齐了，收尾。")
            self.state.done.set_result("done")
            return
        # 不齐：先 stagnation 激活依赖就绪却静默的 worker
        activated = await self._activate_ready_silent_workers()
        if activated:
            self._stagnation_retries = 0
            await self._emit_intervene("stagnation", "流程卡住了，我来推进一下。")
            return
        # 无可激活：累计无解
        self._stagnation_retries += 1
        if self._stagnation_retries >= STAGNATION_MAX_RETRY:    # 默认 3
            miss = "、".join(self._display(m) for m in result.missing)
            await self._emit_intervene("gate_reject", f"还缺 {miss}，先按现有内容交付。")
            self.state.done.set_result("partial")

    async def _check_drift(self) -> None:
        try:
            recent = self._recent_speaks(DRIFT_RECENT_K)
            verdict = await self.drift_judge.judge(goal=self.goal, recent_speaks=recent)
            if verdict.drifted:
                await self._emit_intervene("drift", verdict.restate_text or self.goal)
        except Exception as e:  # noqa: BLE001 — FR-017 降级
            log.warning("drift judge crashed: %s", e)
```

**职责对应 spec**:
- `_on_quiescence` → gatekeeper(FR-018/019/020)+ stagnation(FR-009/010/011/012)
- `_check_drift` → drift(FR-013/014/016/017)
- `_emit_intervene` → emit `CoordinatorIntervene(kind, text)`(脱敏中文,FR-023)

---

## 5. HarnessState 扩展字段

```python
@dataclass
class HarnessState:
    # ... P1/P2 现有字段
    # P3 新增（仅 is_v2 路径使用；v1 全部默认值，零开销）
    inflight_steps: int = 0
    bootstrapped: bool = False
    observer: "CoordinatorObserver | None" = None

    async def start_observer(self, workers, goal) -> None:
        if not self.is_v2:
            return
        self.observer = CoordinatorObserver(state=self, workers=workers, goal=goal)
        await self.observer.start()

    async def stop_observer(self) -> None:
        if self.observer is not None:
            await self.observer.stop()
            self.observer = None
```

---

## 6. AgentWorker.handle_v2_event（work-driver 转换）

P3 的核心改动:SPEAK 分支由"emit confirm"改为"真跑 step"。

```python
# handle_v2_event 内，lock 已 acquire 之后：
try:
    self.state.subscriptions.mention_counter.bump(self.agent_id)
    self.state.subscriptions.reply_to_registry.mark(self.agent_id, msg_id)

    if decision == DecisionResult.SPEAK:
        # P3：work-driver —— 真跑 step（lock 已持有，_run_unlocked 不再抢锁）
        self.state.inflight_steps += 1
        try:
            await self._run_unlocked()       # 跑 _run_step + gate_review +
                                             # _emit_v2_step_overlay(emit mentions=[下一棒])
        finally:
            self.state.inflight_steps -= 1
    else:  # SILENT —— 不变（P2 行为）
        await self.state.emit_v2(AgentSilent(...))
finally:
    lock.release()
```

**不变量**:
- `_run_unlocked` 末尾 emit `agent.handoff` → v2 路径 Coordinator.on_handoff short-circuit(决策 0),不双驱动
- SILENT / IGNORE / 锁超时降级分支:全部保留 P2 行为
- v1 路径:handle_v2_event 不被调用(inbox 仅 v2 构造),零影响

---

## 7. Coordinator chain short-circuit（v2 路径）

```python
# Coordinator.on_handoff / on_failed / on_needs_help / on_needs_retry 各自第一行：
async def on_handoff(self, event: AgentEvent) -> None:
    if self.state.is_v2:
        return   # P3：v2 路径不 chain 路由，驱动交给 subscription + observer
    # ... v1 原逻辑完全不动
```

**关键**:这是**唯一**对 Coordinator class 的改动 —— 4 个 handler 各加一行 short-circuit。Coordinator 的 chain routing / 兜底逻辑(v1 路径)字段级不变(FR-021,符合"不改 Coordinator 内部行为"的最小侵入)。

---

## 8. run_harness 集成（is_v2 分支）

```python
async def run_harness(*, ..., is_v2=False) -> dict:
    # ... 构造 state / workers / Coordinator（不变）
    if is_v2:
        # P2：构造 subscriptions + start_v2_consumer（不变）
        ...
        # P3：起点 bootstrap + 启动 observer
        goal = _derive_goal(run)                          # report_type/audience/raw_text 摘要
        await state.start_observer(workers, goal)
        await _bootstrap_first_step(state, workers, steps_meta[0][1])
    else:
        await state.emit("task.start", ...)               # v1 原样
        await state.emit("agent.handoff", "coordinator", None, {...})  # v1 chain 起点

    result_reason = await asyncio.wait_for(state.done, timeout=timeout_sec)

    if is_v2:
        await state.stop_observer()                       # P3 清理 watchdog
        # P2：cancel consume_tasks（不变）
    return {...}


async def _bootstrap_first_step(state, workers, first_agent) -> None:
    """触发 material 第一棒（FR-005/006，只一次）。"""
    if state.bootstrapped:
        return
    state.bootstrapped = True
    await state.emit("task.start", "coordinator", None, {...})  # task.start 仍发
    ev = AgentSpeak(task_id=state.run.task_id, **{"from": "coordinator"},
                    text="开始整理这次的汇报材料。", intent="propose",
                    mentions=[first_agent])
    await state.emit_v2(ev)   # emit_v2 → dispatch → material.enqueue_v2 → 唤醒跑 step
```

---

## 状态转移

### v2 任务驱动视角（P3）

```
run_harness(is_v2=True)
  ├─ 构造 subscriptions + observer + start_v2_consumer
  ├─ start_observer（watchdog 起跑）
  └─ _bootstrap_first_step
        └─ emit_v2 bootstrap AgentSpeak(mentions=[material])
              └─ dispatch → material.enqueue_v2 → consume_loop → handle_v2_event
                    └─ decide_to_speak SPEAK（material requires=()）
                          └─ inflight+1 → _run_unlocked（跑 step + 产 MaterialPool）→ inflight-1
                                └─ _emit_v2_step_overlay: AgentSpeak(mentions=[point-extractor], artifact=MaterialPool)
                                      └─ point-extractor 唤醒 → SPEAK（MaterialPool 就绪）→ 跑 step → ...
                                            ... 链式 ...
                                                  └─ reviewer 跑完 → overlay mentions=[]（DONE）→ 无下游

  并行 observer watchdog（每 0.5s）：
    ├─ 非 quiescent（有 inflight / inbox 非空）→ 跳过
    ├─ quiescent + artifact 齐 → gate_pass + done
    ├─ quiescent + 缺失 + 有就绪静默 worker → stagnation 激活
    ├─ quiescent + 缺失 + 无解 ×STAGNATION_MAX_RETRY → gate_reject + partial
    └─ 每 DRIFT_EVERY_N_TICK 拍 → drift judge（默认 NoDrift → 不 intervene）
```

### v1 任务（不变）

```
run_harness(is_v2=False)
  └─ emit task.start + emit agent.handoff to first_agent
        └─ Coordinator.on_handoff（is_v2=False，不 short-circuit）→ worker.run() → ...
              └─ chain 推进（_resolve_target / 默认链 / 必经步骤保护，全保留）
                    └─ handoff DONE → Coordinator._end → state.done
  observer / subscriptions / inflight_steps：全不构造（零开销）
```

---

## 配置常量（coordinator_observer.py 顶部，env override）

| 常量 | 默认 | 用途 |
|---|---|---|
| `OBSERVER_TICK_SEC` | 0.5 | watchdog 轮询周期(秒) |
| `DRIFT_EVERY_N_TICK` | 10 | 每几拍跑一次 drift 判断 |
| `DRIFT_RECENT_K` | 5 | drift 喂最近几条 speak |
| `STAGNATION_MAX_RETRY` | 3 | stagnation 无解兜底上限 → 转 partial 收尾 |

---

## 验证规则汇总

| 来源 | 规则 |
|---|---|
| FR-001 | SPEAK 分支调 `_run_unlocked` 真跑 step（inflight 计数包裹） |
| FR-005/006 | `_bootstrap_first_step` 触发 material 一次（bootstrapped flag 去重） |
| FR-007 | v2 路径 Coordinator on_handoff/... short-circuit |
| FR-008 | 必经步骤保护由 worker `requires`（P2）实现，Coordinator 不硬拦 |
| FR-009/010/011 | observer `_is_quiescent` + `_on_quiescence` stagnation 激活就绪静默 worker，不指定 next-speaker |
| FR-012/020 | `STAGNATION_MAX_RETRY` 兜底 → partial，必终止 |
| FR-013~017 | `DriftJudge` 注入抽象 + minimal context + 异常降级 + 仅发声 |
| FR-018/019 | `ArtifactGate.check` → gate_pass/gate_reject |
| FR-021/022 | 全部 `is_v2` 守卫；v1 零回归 |
| FR-023 | `_emit_intervene` 文案业务化中文，无技术词 |
| FR-024 | observer `_loop` / drift / gate 全 try/except 降级 |

---

## 已知未决（留给后续）

- **LLMDriftJudge 真实现**:Phase 0 宪章 + Windows issue 双重前置,本期交付 NoDriftJudge + 接口
- **observer watchdog 轮询 vs 事件驱动**:本期轮询(简单鲁棒);P6 并发期视性能再评估
- **_emit_v2_finalization 在 v2 的取舍**(F3):gatekeeper 接管收尾后,该函数 v2 路径收窄,tasks 阶段定
