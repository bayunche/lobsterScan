# P6 — EventBus fan-out 并发 + html/video 真并行 · 设计

> 设计日期:2026-06-01
> 状态:已与用户敲定 6 项决策,待 specify→plan→tasks→implement
> 前置:P1-P5 均已合 main;真 LLM 链路已跑通(envelope/legacy 双向)
> roadmap 出处:`docs/开发文档.md` §9.4.5 P6 行

## 1. 目标

降低任务总耗时,两件可分离但本期一起做的事:
- **A · html/video 真并行**:html-designer + video-producer 都只依赖 copywriting
  (`STEP_DEPENDS` line 75/76 均为 `("copywriting",)`),不同 agentDir、不写核心 artifact,
  天然可并行。当前真 LLM 跑里它们串行(copywriter→html→video)。
- **B · EventBus.emit fan-out**:`emit` 当前串行 `await h(event)` 逐 handler;改为
  `asyncio.gather` 并发,降低事件分发延迟。

非目标(留后续):rolling summary(P8)、UX(P7)、artifact 真乐观并发 merge
(html/video 不写核心 artifact,本期无冲突,故 base_version retry/merge 不做)。

## 2. 已敲定决策(与用户确认)

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 范围 | A + B 都做 |
| 2 | 并行驱动 | copywriting 完成时同时 @ html-designer + video-producer |
| 3 | 去重保证 | per-agent lock + step.status==success 跳过已保证;fan-out 只加并发 |
| 4 | 收尾机制 | 保持现有 quiescence 收尾(observer 不改) |
| 5 | 验收基线 | 测试级绿 + 1 次真 LLM 对比串行/并行耗时 |
| 6 | flag/回退 | 双 flag,默认 off = 今天行为(同 P5 双轨先例) |

## 3. 现状勘查(已核实)

- `EventBus.emit`(harness.py:79-90):串行 dispatch,注释明说"确保 visited/route 顺序确定"。
  wildcard handlers + kind 订阅都是 `for h: await h(event)`,各自 try/except 降级。
- `get_agent_lock(agent_id)`(harness.py:123):**per-agent** lock,html-designer 与
  video-producer 不同 lock → 并行不互锁,不违宪章 V(agentDir 独立)。
- `inflight_steps += 1/-= 1`(harness.py:423/427/522):asyncio 单线程,无真线程竞争,
  并行 +2 后归 0 安全。
- `STEP_DEPENDS`:html_design / video_production / review 都只依赖 copywriting →
  三者互不依赖;review 依赖 copywriting(并行前已 done),不依赖 html/video。
- artifact:§9.4.7 决策 3 仅 4 核心 artifact 版本化(html/video latest-wins,不写核心)
  → html/video 并行无 artifact 冲突。
- `_emit_v2_step_overlay`(pipeline.py):copywriting 的 mentions 当前取
  `DEFAULT_NEXT_STEP["copywriting"]="html_design"`(单目标)。

## 4. 架构改动点(全部 flag 守红线)

### 4.1 html/video 并行触发(A)
- 新增 `COPYWRITING_FANOUT = ("html-designer", "video-producer")` 常量(pipeline.py)。
- `_emit_v2_step_overlay`:step==copywriting 且 fanout 开启时,mentions 取双目标
  (legacy);envelope 模式由 _ENVELOPE_RULE 提示 copywriter 在信封 mentions 给两个,
  overlay 直读信封 mentions(P5 已支持多 mention)。
- 两个 worker 各被 mention 唤醒 → 各自 per-agent lock → 并行 `_run_unlocked`。
  inflight_steps 自然 +2。

### 4.2 EventBus.emit fan-out(B)
- `emit` 按 flag 选:off → 原串行(零回归);on → `asyncio.gather(*tasks, return_exceptions=True)`
  并发 dispatch wildcard + kind handlers。
- 异常隔离:gather return_exceptions=True,单 handler 崩只 log,不影响其他(保持现有降级语义)。
- 去重不受影响(决策 3):同 step 只跑一次靠 per-agent lock + step.status==success 跳过
  (harness:406),非 EventBus 串行。fan-out 后同 agent 两次触发仍被 lock 串行、第二次跳过。

### 4.3 收尾(C)
- **不改 observer**。html+video 并行 → 各自 inflight +1/-1 → 都完成后 inflight 归 0 →
  quiescence 检测 → gatekeeper 综合 → done/partial。review 依赖 copywriting(已 done)
  不受并行影响。

### 4.4 flag
- `V2_FANOUT = os.environ.get("V2_FANOUT", "off")`(subscription.py,同 V2_* 风格);
  helper `_fanout_enabled()`。控制 EventBus 并发 + copywriting 双 mention。
- 默认 off → P6 全部新逻辑短路 = P5 行为(零回归)。

## 5. 回归策略

双 flag 默认 off(同 P5 双轨):
- `V2_FANOUT=off`(默认):EventBus 串行 + copywriting 单 mention = 今天行为。
- `V2_FANOUT=on`:EventBus gather 并发 + copywriting 双 @ html/video 并行。
- 回退 = 改 1 个 env。

## 6. 测试计划(验收 = 测试级绿 + 1 次真 LLM 对比耗时)

- 单元:fan-out emit 并发(多 handler 同时跑 + 异常隔离不互相影响)、copywriting fanout
  双 mention、flag off 时单 mention/串行。
- e2e:ScriptedBackend 验 fanout on 时 html+video 同时 start(calls 顺序交错)、inflight 峰值 ≥2、
  收尾 gatekeeper done/partial、flag off 零回归。
- v1/P5 零回归:flag off 时全量 tests 全绿 + 字段级一致。
- 真 LLM:1 次对比 `V2_FANOUT` on vs off 的 copywriting→收尾段总耗时(并行应更短)。

## 7. 风险

| 风险 | 对策 |
|---|---|
| fan-out 改坏 work-driver 顺序去重 | 去重靠 per-agent lock + success 跳过,非 EventBus 串行(已核实);flag 默认 off |
| 并行 step 写盘/emit 竞争 | asyncio 单线程无真竞争;artifact 用各自 write_versioned;events.jsonl 追加写各自 try/except |
| 收尾过早(一个并行 step 没完就 quiescence) | inflight==0 才 quiescence;两个 step 各 +1,都完成才归 0,天然同步 |
| 真 LLM 下 copywriter 信封只给一个 mention | overlay legacy fanout 常量兜底双目标;envelope 模式 prompt 提示 + 单 mention 也不挂(另一个靠 observer stagnation 激活) |

## 8. 不做 / 边界

- 不改 observer / reviewer / gatekeeper 决策逻辑。
- 不做 artifact 乐观并发 merge(html/video 不写核心 artifact,无冲突)。
- 不动 typed artifact schema、4 核心 artifact 通信、导出。
- 不做 UX/压缩(P7/P8)。
- 宪章:无需修订(并发是执行优化,不改 Coordinator/Reviewer 职责,不引入 LLM 决策权)。
