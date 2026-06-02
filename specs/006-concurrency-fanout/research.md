# Research: P6 — EventBus fan-out 并发 + html/video 真并行

Phase 0 技术决策。spec 无 NEEDS_CLARIFICATION(6 决策已 brainstorm 敲定);本文记录
实现层 4 决策 + 现状核实。

## 决策 1 · 去重为何在并发下仍安全(不依赖 EventBus 串行)

- **Decision**: fan-out 后"同一 step 只跑一次"靠两层,均与 EventBus 串行无关:
  ① per-agent lock(harness.py:123 `get_agent_lock(agent_id)`):同 agent 的多次触发串行;
  ② step.status=="success" 跳过(harness.py:406):拿锁后若已 success 直接返回。
- **Rationale**: html-designer / video-producer 是**不同 agent**,本就不同 lock、并行无冲突;
  同一 agent 被订阅 + mention 双触发时,两次进入仍被自己的 lock 串行,第二次见 success 跳过。
  EventBus 改并发只影响"事件多 handler 谁先收到",不影响上述两层。
- **Alternatives**: 在 EventBus 加去重集合 —— 冗余(已有两层),且 emit 是通用层不该知 step 语义。否。
- **现状核实**: P3 注释明说串行是为"visited/route 顺序确定";但 route(Coordinator on_handoff)
  在 v2 路径已 short-circuit(is_v2 分支),work-driver 走订阅 + lock,不靠 emit 顺序。

## 决策 2 · gather 异常隔离

- **Decision**: `emit` fanout 分支用 `await asyncio.gather(*[_safe(h, event) for h in handlers],
  return_exceptions=True)`,其中 `_safe` 包 try/except log(或直接 return_exceptions 后过滤)。
  单 handler 抛错不影响其他,与现状串行版的逐 handler try/except 语义一致。
- **Rationale**: FR-007/013。保持"单 handler 崩只 log"降级,不让事件流中断。
- **Alternatives**: gather 默认(首个异常即抛)—— 会中断其余 handler,违 FR-007。否。
- **顺序说明**: fanout 下 wildcard + kind handlers 可一起 gather,或分两批(先 wildcard 后 kind)。
  选**一起 gather**(最大并发);wildcard 多是日志/收集类,与 kind 订阅无序依赖。

## 决策 3 · 收尾不改 observer(quiescence 天然覆盖)

- **Decision**: 不动 coordinator_observer。html+video 并行 → 各自 `_run_unlocked` 内
  inflight +1/-1(harness 现有)→ 两者都完成才 inflight 归 0 → `_is_quiescent()` 通过
  → gatekeeper 综合 → done/partial。
- **Rationale**: FR-003。`_is_quiescent`(coordinator_observer.py:261)已要求 inflight==0;
  并行只是让计数峰值到 2,归 0 条件不变。review 依赖 copywriting(并行前已 done),不依赖
  html/video,不受影响。
- **现状核实**: handle_v2_event SPEAK 分支 inflight +=1 / finally -=1(harness:423/427);
  force_run_v2 同理(:522)。并行两个 worker 各走一遍,计数正确。

## 决策 4 · 真 LLM 下 copywriter 可能只给一个 mention 的兜底

- **Decision**: legacy 模式 overlay 用 `COPYWRITING_FANOUT` 常量兜底双目标(不依赖 LLM)。
  envelope 模式:_ENVELOPE_RULE 对 copywriting 提示"讲稿就绪请同时 @ html-designer 与
  video-producer";若 LLM 仍只给一个,缺失的那个由 observer stagnation 激活(P3 已实现
  `_activate_ready_silent_workers`:Script 就绪但未产出自己 artifact 的 worker 会被激活)。
- **Rationale**: FR-001 + edge case。双保险:常量兜底(legacy 确定性)+ stagnation 激活
  (envelope 真 LLM 兜底),不漏步。
- **Alternatives**: 强依赖 LLM 给双 mention —— 不可靠(LLM JSON 不保证),否。
- **派生发现**: P6 是否在 envelope 模式改 _ENVELOPE_RULE 文案给 copywriting 加双 @ 提示,
  属可选增强;核心兜底是 stagnation 激活(已存在),故 P6 最小实现可不改 rule,
  靠 COPYWRITING_FANOUT(legacy)+ stagnation(envelope)。本期取最小:不改 rule。

## 综合:改动面与零回归

- 改动:EventBus.emit(加 fanout 分支)+ _emit_v2_step_overlay(copywriting 双 mention)
  + V2_FANOUT flag + _fanout_enabled()。
- off 模式:emit 走原串行;copywriting mentions 走原单目标(DEFAULT_NEXT_STEP)。
  → 与 P5 字段级一致,SC-001 零回归。
- 不动:observer/reviewer/gatekeeper、per-agent lock、inflight 机制、artifact、导出、typed schema。
