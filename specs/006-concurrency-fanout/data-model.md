# Data Model: P6 — EventBus fan-out 并发 + html/video 真并行

本期不新增持久化实体、不改 typed artifact schema。以下是配置态/过程态实体。

## 1. V2_FANOUT(并发开关 · 配置态)

| 名称 | 类型 | 默认 | 取值 | 作用 |
|---|---|---|---|---|
| V2_FANOUT | env str | `off` | `off` \| `on` | off=串行 emit + copywriting 单 mention(=今天);on=EventBus gather 并发 + copywriting 双 @ |

- `_fanout_enabled()` 每次读 `subscription.V2_FANOUT`(支持测试 monkeypatch,同 V2_PROMPT_MODE 模式)。
- 与 `V2_PROMPT_MODE`(P5)正交:可任意组合(legacy+fanout / envelope+fanout / …)。
- off 时 P6 全短路 → 零回归(FR-011)。

## 2. COPYWRITING_FANOUT(并行触发目标 · 常量)

| 名称 | 值 | 用途 |
|---|---|---|
| COPYWRITING_FANOUT | `("html-designer", "video-producer")` | fanout on 时,copywriting 完成 overlay 的 mentions |

- 二者均依赖 copywriting(`STEP_DEPENDS` 已核实)、不同 agentDir、不写核心 artifact。
- 被同时 @ → 各自 per-agent lock → 并行 `_run_unlocked`。

## 3. inflight_steps 峰值(过程态)

| 维度 | off(串行) | on(并行) |
|---|---|---|
| copywriting 后 inflight 峰值 | 1(html 完→video) | **2**(html+video 同时) |
| 收尾条件 | inflight==0(不变) | inflight==0(不变,两者都完成才归 0) |

- asyncio 单线程,`+= / -=` 无竞争(research 决策 3)。
- SC-004 确证并行:两个 agent.start 在对方 agent.done 前都出现。

## EventBus.emit 行为(off vs on)

```
off(默认):
  for h in wildcard: await h(event)      # 串行,逐个 await
  for h in kind_subs: await h(event)

on(fanout):
  await gather(*[safe(h, event) for h in wildcard + kind_subs],
               return_exceptions=True)   # 并发,异常隔离(FR-007)
```

## 去重不变量(并发下仍成立 · research 决策 1)

```
同 step 只跑一次:
  ① per-agent lock(get_agent_lock(agent_id)):同 agent 多触发串行
  ② step.status=="success" 跳过:拿锁后已 success 直接返回
  —— 二者均与 EventBus 是否串行无关;html/video 不同 agent 本就不同 lock
```

## 收尾不变量(observer 不改 · research 决策 3)

```
quiescence = bootstrapped + inflight==0 + inbox 全空 + 未完成
  并行只改 inflight 峰值(1→2),归 0 条件不变
  review 依赖 copywriting(并行前 done),不依赖 html/video
```
