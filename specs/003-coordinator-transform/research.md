# Phase 0 — Research & Technical Decisions

**Feature**: Coordinator 转型(observer + gatekeeper)+ subscription 升级为 work-driver（P3）

**Branch**: `003-coordinator-transform` | **Date**: 2026-05-30

---

## 现状基线（P2 之后,本期改造起点）

读 `harness.py` 确认 P2 的 v2 任务实际跑法:

- `run_harness` 末尾(line 764)emit `agent.handoff` to first_agent —— **v1/v2 同一套起点**
- `Coordinator.on_handoff`(line 555)路由 → `worker.run()` → `_run_unlocked()`(line 415)跑真 `_run_step`
- subscription 是额外挂在 `emit_v2` 末尾的 overlay;`handle_v2_event`(line 289)SPEAK 分支只 emit confirm `AgentSpeak`(line 367),**不跑 step**

**结论**:P2 的 v2 任务 = Coordinator chain 驱动真实 step(v1 那套)+ subscription chat overlay。
P3 要在 **v2 路径**把"驱动真实 step"的职责从 Coordinator chain **转移**到 subscription,Coordinator 退为 observer + gatekeeper。v1 路径整套不动。

---

## 决策汇总

| # | 决策点 | 选择 |
|---|---|---|
| 0 | v2 路径怎么断开 Coordinator chain 驱动 | `Coordinator.on_handoff` 在 `is_v2` 时**不 dispatch worker.run()**(short-circuit);保留 observer/gatekeeper |
| 1 | 起点 bootstrap 怎么实现 | `run_harness` `is_v2` 时**不**emit chain 起点 handoff,改为 enqueue 一条 bootstrap 事件到 material inbox |
| 2 | work-driver 转换怎么做 | `handle_v2_event` SPEAK 分支从 emit confirm 改为调 `_run_unlocked()` 真跑 step(lock 已持有) |
| 3 | 链式推进怎么闭环 | 复用 P2 `_emit_v2_step_overlay`(step 完成 emit `AgentSpeak(mentions=[下一棒])`)→ 下游订阅唤醒 → SPEAK → 跑 step |
| 4 | stagnation + drift + gatekeeper 放哪 | 统一为一个 **Coordinator observer watchdog**(asyncio 周期 task),quiescence 时依次跑 gatekeeper/stagnation,周期性跑 drift |
| 5 | in-flight step 怎么追踪 | `HarnessState` 加 `inflight_steps: int` 计数(worker 跑 step 前 +1,完成 -1);quiescence = inbox 全空 + inflight==0 |
| 6 | drift LLM 调用通道 | 注入式 `DriftJudge` 抽象(同 `AgentBackend` 模式);默认实现 mock-first(返回"未跑题"),真 LLM 实现留扩展点 |
| 7 | gatekeeper 怎么收尾 | quiescence + 核心 artifact 齐 → `gate_pass` + `state.done("done")`;不齐先 stagnation 激活,无解 → `gate_reject` + `state.done("partial")` |
| 8 | v1 零开销 + 零回归 | 所有 P3 改动 `if is_v2:` 守卫;v1 路径 line 764 起点 + Coordinator chain 字段级不变 |

---

## 0. v2 路径断开 Coordinator chain 驱动

**Decision**: `Coordinator.on_handoff` 在 `self.state.is_v2` 时 short-circuit —— **不**`asyncio.create_task(worker.run())`(line 638 附近),直接 return;保留 observer watchdog 与 gatekeeper。

**Rationale**:
- v2 路径下 worker 跑完 step 仍会 emit `agent.handoff`(`_run_unlocked` line 485);若 Coordinator 照旧路由,会和 subscription **双驱动**同一 step(浪费 LLM + 产物覆盖)。
- 最小改动:on_handoff 第一行 `if self.state.is_v2: return`(类比 emit_v2 短路)。v1 路径不受影响。
- Coordinator 的 `on_failed` / `on_needs_help` / `on_needs_retry` 同样在 v2 路径 short-circuit(这些是 chain 兜底,v2 由 stagnation 兜底)。

**Alternatives considered**:
- v2 路径不注册 Coordinator chain handlers(改注册 observer):更彻底但改动大,且 Coordinator 构造逻辑要分叉。否。
- 让 worker.run() 在 v2 不 emit handoff:破坏 v1/v2 共用 `_run_unlocked`,需复制代码。否。short-circuit 在 Coordinator 侧最集中。

---

## 1. 起点 bootstrap

**Decision**: `run_harness` 在 `is_v2` 时**不**执行 line 764 的 chain 起点 handoff;改为给 material worker 的 inbox enqueue 一条 **bootstrap 事件**,触发 `material.handle_v2_event` → SPEAK(material `requires=()` 无依赖)→ 跑 step。

**Rationale**:
- material 是唯一无上游 @ 的起点;v2 需要一个"种子"事件让它的 decide-to-speak 走 SPEAK。
- 复用现有 inbox + consume_loop + handle_v2_event 链路,无需新执行路径。
- bootstrap 事件用一条最小 `AgentSpeak(from=coordinator, mentions=[material], intent=propose, text="开始")`;mention_includes("material") 命中 → 唤醒。

**实现要点**:
- `run_harness` `is_v2` 分支(line 749 区域)末尾,起点改为:
  ```python
  if is_v2:
      # 不 emit chain 起点 handoff;bootstrap material
      await state.start_observer()          # 见决策 4
      await _bootstrap_first_step(state, workers, first_agent)
  else:
      await state.emit("agent.handoff", "coordinator", None, {...})  # v1 原样
  ```
- `_bootstrap_first_step`:构造 bootstrap AgentSpeak → `material.enqueue_v2(ev)`;去重(FR-006)用一个 `state.bootstrapped: bool` flag。

**Alternatives considered**:
- 直接 `await material.run()`:绕过 decide-to-speak/lock 链路,起点和后续路径不一致,测试两套。否。
- emit_v2 一条 ArtifactUpdate 触发:material 不订阅 artifact,且语义错。否。

---

## 2. work-driver 转换

**Decision**: `handle_v2_event` 的 SPEAK 分支(harness.py line 367)从"emit confirm `AgentSpeak`"改为**调 `self._run_unlocked()` 真跑 step**。lock 已在 line 344-346 持有,`_run_unlocked` 不再自己抢锁(它本就是无锁主体)。

**Rationale**:
- `_run_unlocked` 是 v1/v2 共用的 step 执行主体(跑 `_run_step` + gate_review + emit agent.done/handoff + P2 的 `_emit_v2_step_overlay`)。复用它 = work-driver 转换零新执行路径。
- SPEAK 跑完后 `_emit_v2_step_overlay` 自动 emit `AgentSpeak(mentions=[下一棒])`,驱动下游(决策 3)。
- SILENT / IGNORE 分支不变(P2 行为保留)。

**关键防护**:
- `_run_unlocked` 末尾会 emit `agent.handoff`(line 485);v2 路径 Coordinator.on_handoff 已 short-circuit(决策 0),不会双驱动。
- by_key 必须有该 step 的 StepState —— run_harness 构造时已建(v1/v2 同源),OK。
- 跑 step 期间 `state.inflight_steps += 1` / `finally -= 1`(决策 5),供 quiescence 检测。

**Alternatives considered**:
- 新写一个 `_run_driven_step`:和 `_run_unlocked` 95% 重复,违 DRY。否。
- SPEAK 时 emit handoff 让 Coordinator 路由:等于没断开 chain,回到 P2。否。

---

## 3. 链式推进闭环

**Decision**: 完全复用 P2 的 `pipeline._emit_v2_step_overlay`(step 完成后 emit `AgentSpeak(mentions=[handoff.to 或默认链下一棒], artifact_updates=[...])`)。被 mention 的下游 worker 订阅唤醒 → handle_v2_event → SPEAK(依赖就绪)→ `_run_unlocked` 跑 step → 再 `_emit_v2_step_overlay`...链式推进。

**Rationale**: P2 已落地这个 per-step overlay,P3 不改;它原本只是"群聊呈现",P3 中它变成"驱动信号"——同一份 emit,语义随驱动模型升级而升级。零新增代码。

**终止条件**: 最后一棒 reviewer 的 `DEFAULT_NEXT_STEP["review"]=="DONE"` → overlay 的 mentions 为空 → 无下游唤醒 → 自然 quiescence → observer watchdog 收尾(决策 7)。

---

## 4. stagnation + drift + gatekeeper 统一为 Coordinator observer watchdog

**Decision**: 新增一个 **observer watchdog**(asyncio 周期 task,如每 0.5s 一拍),挂在 `HarnessState`,`is_v2` 时 `run_harness` 启动、结束 cancel。每拍:
1. 检测 quiescence(决策 5)
2. quiescence 时跑 **gatekeeper**(决策 7):齐 → done;不齐 → **stagnation** 激活依赖就绪却静默的 worker;无可激活 → 累计无解 → partial 收尾
3. 周期性(每 N 拍 / 或 quiescence 时)跑 **drift**(决策 6)

**Rationale**:
- stagnation / gatekeeper 都依赖"quiescence 检测",合一个 watchdog 避免重复检测逻辑。
- watchdog(轮询)比"每个 emit 后检查"更简单鲁棒(不和 emit 热路径耦合,不怕漏触发)。
- drift 顺带挂 watchdog,周期触发,天然限频。

**Alternatives considered**:
- 事件驱动 quiescence(每次 worker 处理完 inbox 检查全局):需要跨 worker 协调"我是不是最后一个空的",竞态复杂。否。
- 三个独立机制:重复 quiescence 检测 3 次,代码分散。否。

**实现要点**:
- `HarnessState.start_observer()` / `stop_observer()`;watchdog 是 `CoordinatorObserver` 类(新模块 `coordinator_observer.py` 或并入 harness)。倾向**新模块**(同 P2 subscription.py 独立),便于测试 + 不撑大 harness。

---

## 5. in-flight step 追踪 + quiescence 定义

**Decision**: `HarnessState.inflight_steps: int = 0`。worker 在 `handle_v2_event` SPEAK 跑 `_run_unlocked` 前 `+1`,`finally -1`。

**quiescence 定义**(全部满足):
- 所有 v2 worker 的 `inbox.empty()` 为 True
- `state.inflight_steps == 0`
- task 未完成(`state.done` 未 set)
- 已过启动态(`state.bootstrapped == True`,避免启动瞬间误判)

**Rationale**: 异步事件驱动系统的 quiescence(静默)= 没有待处理事件 + 没有正在跑的活。两条件缺一不可(只看 inbox 空会漏判正在跑 step 的;只看 inflight 会漏判 inbox 还有积压的)。

**Alternatives considered**:
- 只用 timeout(N 秒无新事件):简单但脆(慢 LLM step 会误判)。作为 watchdog 兜底,非主判据。
- 全局事件计数 delta:需要快照对比,不如直接查 inbox + inflight 实时态。否。

---

## 6. DriftJudge 注入抽象

**Decision**: `DriftJudge` 抽象(`coordinator_observer.py` 内),接口 `async def judge(goal: str, recent_speaks: list[str]) -> DriftVerdict`。默认实现 `NoDriftJudge`(永远返回"未跑题",mock-first,不调 LLM);真 LLM 实现 `LLMDriftJudge` 留扩展点(Windows subprocess 问题 + Coordinator 非 agent,真实现待环境/通道明朗)。注入点同 `get_default_backend()` / `set_default_backend()` 模式。

**Rationale**:
- spec FR-015 要求可注入(测试注入 mock);FR-017 要求异常降级。
- mock-first 让 US4 的测试(4 分支)能在不依赖真 LLM 下全绿;真 drift 判断作为 Windows issue / 后续的增量。
- 不绑死 openclaw CLI:Coordinator 不是 agent,没有 agentDir/profile;真实现倾向直连 LLM provider(轻量),但这是 default 实现细节,接口稳定。

**DriftVerdict**: `{drifted: bool, restate_text: str}`;drifted=True → emit `coordinator.intervene(kind=drift, text=restate_text)`。

**宪章前置**: 真 LLM 判断需 Phase 0 宪章修订放宽"Coordinator 纯规则引擎"。抽象本身(接口 + NoDriftJudge)不调 LLM,**不违宪**,可先落;`LLMDriftJudge` 真实现才受阻塞。

**Alternatives considered**:
- 走 openclaw agent CLI(给 coordinator 配 profile):Windows subprocess 阻塞 + 给 Coordinator agentDir 违背"Coordinator 非 agent"。否。
- 内联 lambda 不抽象:不可注入测试,违 FR-015。否。

---

## 7. gatekeeper 收尾

**Decision**: quiescence 时,observer watchdog 调 `ArtifactGate.check(task_id)`:
- 4 核心 artifact(MaterialPool/ReportCore/Outline/Script)latest 版本都 ≥1 → emit `coordinator.intervene(kind=gate_pass)` + `state.done.set_result("done")`
- 有缺失 → 先尝试 stagnation 激活"依赖就绪却静默"的上游 worker(emit `intervene(kind=stagnation)` + 重新 enqueue 触发事件);连续无解(无可激活 worker)达兜底上限 → emit `coordinator.intervene(kind=gate_reject)` 点名缺失上游 + `state.done.set_result("partial")`

**Rationale**:
- 替代 v1 的"必经步骤保护"(已下沉到 worker `requires`)+ Coordinator 收尾 gate。
- gatekeeper 必给确定终止状态(FR-020):齐→done,无解→partial,绝不无限挂起。
- 复用 `artifacts_v2.next_version`(latest 版本探测,P2 已用)。

**stagnation 激活逻辑**: 找"requires 全满足但本任务还没产出自己 artifact"的 worker → 给它 enqueue 一条 stagnation 触发事件(类似 bootstrap)。不指定 next-speaker(FR-011)—— 激活的是"该动却没动"的,不是 Coordinator 选的。

**Alternatives considered**:
- gatekeeper 用 pipeline 的 `_emit_v2_finalization`:那是 P1 的收尾示例 emit,职责不同(P3 要真校验 + set done)。新建 `ArtifactGate`。否。

---

## 8. v1 零开销 + 零回归

**Decision**: 所有 P3 改动 `if is_v2:` / `if self.state.is_v2:` 守卫:
- `run_harness`:`is_v2` 走 bootstrap + start_observer;else 走 line 764 chain 起点(原样)
- `Coordinator.on_handoff/on_failed/...`:`is_v2` 第一行 short-circuit
- `handle_v2_event` SPEAK:本就只在 v2 路径(inbox 仅 v2 构造)
- observer watchdog / inflight_steps / DriftJudge / ArtifactGate:仅 v2 构造/调用

**测试守护**(沿用 P2 US1 红线):
- v1 run_harness:state.subscriptions/observer 为 None;Coordinator 仍 chain 路由;events.jsonl 无 P3 新事件
- v1 demo 字段级 diff(SC-001)

---

## 派生发现（Derived Findings）

### F1 — Coordinator 构造需在 v2 路径附带 observer 引用

`Coordinator.__init__` 注册 bus handlers。v2 路径下这些 handlers 要 short-circuit;同时 observer watchdog 需要访问 workers + artifacts。倾向:observer watchdog 独立于 Coordinator class(新 `CoordinatorObserver`),由 run_harness 在 is_v2 时单独构造启动,与 Coordinator(短路后只剩空壳)并存。Coordinator class 本身**几乎不改**(只加 is_v2 short-circuit),符合"不改 Coordinator 内部行为"的最小侵入。

### F2 — gate_pass/gate_reject/stagnation/drift 复用 P1 InterveneKind 枚举

`events_v2.InterveneKind = Literal["loop_detected","stagnation","drift","budget","gate_pass","gate_reject"]`(P1 已定义)。P3 用其中 4 个(stagnation/drift/gate_pass/gate_reject),**无新 schema**。

### F3 — pipeline.execute 的 v2 收尾需调整

P2 的 `pipeline.execute` 在 run_harness 返回后调 `_emit_v2_finalization`(P1 收尾示例 emit)。P3 的 gatekeeper 已在 observer 内做真收尾;`_emit_v2_finalization` 在 v2 路径可能与 gatekeeper 的 gate_pass/reject 重复 emit。需评估:P3 让 gatekeeper 接管收尾后,v2 路径下 `_emit_v2_finalization` 的示例 emit 应**收窄或跳过**(避免双份 gate 事件)。plan/tasks 阶段定具体取舍。

### F4 — DriftJudge 真实现 = Windows issue 的下游

真 LLM drift 受 Windows subprocess 阻塞(同 T040)。P3 交付 `NoDriftJudge`(mock)+ 接口 + 测试注入;`LLMDriftJudge` 真实现挂在 Windows issue 解决之后,与真 LLM 闭环验收同批。

---

## 阶段产出

完成本研究 → Phase 1(data-model + quickstart),见 [data-model.md](./data-model.md) 与 [quickstart.md](./quickstart.md)。
**contracts/ 跳过 —— P3 是内部架构演进,无新对外 API/事件 schema(复用 P1 InterveneKind)。**

**所有 NEEDS CLARIFICATION 状态:✅ 0 项遗留。**
