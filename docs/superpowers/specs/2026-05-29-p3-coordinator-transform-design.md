# P3 设计 — Coordinator 转型(observer + gatekeeper)+ subscription 升级为 work-driver

**日期**: 2026-05-29 · **阶段**: P3(v2 群聊化路线图,见 `docs/开发文档.md` §9.4.5)
**前序**: P1(v2 协议+状态,已合 main)· P2(worker 订阅化,已合 main)
**产出去向**: 本 design doc → `/speckit-specify` 产出 `specs/003-coordinator-transform/spec.md`

---

## 1. 背景与目标

P2 把 subscription 做成了 **chat overlay**:被 @ 的 worker 只 emit `agent.speak`/`agent.silent`,
**不**跑 `_run_step`;Coordinator 仍是唯一的 LLM work-driver(通过 chain handoff 派单)。

P3 完成"群聊化"的核心翻转:

> **subscription 升级为真 work-driver**(被 @ + 依赖就绪 → 真跑 `_run_step` 链式推进),
> **Coordinator 退为 observer + gatekeeper**(删 chain routing,只守 liveness / 跑题 / 收尾)。

里程碑(路线图原文):*v2 路径单跑 demo 任务能闭环*。

### 验收基线(brainstorm 决策)

**ScriptedBackend 测试级闭环**。不依赖真 LLM 管线(当前 Windows dev 机 subprocess + sh-shim
双重阻塞,见 `docs/issues/windows-real-pipeline-runnability.md`)。用 `ScriptedBackend` mock
在 pytest 里证明"subscription 驱动 `_run_step` → 8 step 串起来 → task 闭环"。真 LLM 闭环作为
Windows issue 解决后的人工验收项(类比 P2 的 T038/T040)。

---

## 2. Phase 0(前置,阻塞 P3 实施)— 宪章修订

P3 的 drift 监控需要 Coordinator 调一次 LLM,这突破了宪章原则 IV / `docs/开发文档.md` §9.4.7
决策 1 的"Coordinator 是**纯**规则引擎"红线。按宪章 §Governance,改五条核心决策**必须先显式
升级宪章**(单独 PR + 版本号)。

**实施第一步必须走 `/speckit-constitution`**,把约束放宽为:

> Coordinator 主体仍是规则引擎(路由/兜底/收尾逻辑无 LLM);**唯 observer 的 drift 判断允许
> 一次轻量 LLM 调用**,输入限定"原始目标 + 最近 K 条发言",输出仅 `coordinator.intervene(kind=drift)`
> ——**不路由 next-speaker、不审内容质量、不改产物**(原则 IV 其余红线全部保留)。

版本 1.0.0 → 1.1.0,commit 标 `constitution: 1.0.0 → 1.1.0` + 理由。**此条不过,P3 不开工。**

---

## 3. 驱动模型(brainstorm 决策:A — 链式 mention 自驱 + Coordinator 最小兜底)

```
任务启动
  └─ [起点 bootstrap] 触发 material 跑第一棒(material requires=() 无依赖)
        └─ material._run_step → 产出 MaterialPool
              └─ _emit_v2_step_overlay: AgentSpeak(mentions=[point-extractor], artifact=MaterialPool)
                    └─ point-extractor 订阅唤醒 → decide_to_speak
                          ├─ SPEAK(MaterialPool 就绪)→ 真跑 _run_step → 产出 ReportCore → emit 驱动下游
                          └─ SILENT(依赖缺)→ 等待
                                ... 链式推进 ...
                                      └─ reviewer 跑完 → 自然终止
                                            └─ [收尾 gatekeeper] 校验 artifact 完整性 → task.end

  并行:[observer] Coordinator 全程监听总线
        ├─ stagnation:链断/全员 silent → intervene 重新激活"依赖就绪却静默"的 worker
        └─ drift:每 N step / stagnation 时,LLM 判跑题 → intervene 复诵原始目标
```

Coordinator **删 chain routing**(`_resolve_target` / 默认链 / 必经步骤保护),保留三件最小职责:
① 起点 bootstrap ② observer(stagnation 规则 + drift LLM)③ 收尾 gatekeeper。
"停滞重启"是 observer 纠偏(intervene),**不是** chain routing —— 激活的是"依赖满足却静默"的
worker,不是 Coordinator 替它选下一棒,不违反宪章 IV"不路由 next-speaker"。

---

## 4. 核心组件(5 个)

| # | 组件 | 改动 | 红线 |
|---|---|---|---|
| 1 | **work-driver 转换** | `AgentWorker.handle_v2_event` 里 `decide_to_speak==SPEAK` 从"emit confirm AgentSpeak"改为**真跑 `_run_step`**(复用 `run()`/`_run_unlocked()`),跑完由现有 `_emit_v2_step_overlay` emit `AgentSpeak(mentions=[下一棒])` 驱动下游 | 仍走 per-agent lock(P2 已建);SILENT/IGNORE 行为不变 |
| 2 | **起点 bootstrap** | `run_harness` 在 `is_v2` 时删除 v1 的"emit `agent.handoff` to first_agent"起点,改为 bootstrap 触发 material 跑第一棒 | 不经 Coordinator chain |
| 3 | **stagnation observer** | Coordinator quiescence 检测(所有 worker inbox 空 + 无 in-flight `_run_step` + task 未完成 + 有未产出核心 artifact)→ emit `coordinator.intervene(kind=stagnation)` 重新激活"依赖就绪却静默"的 worker。纯规则,无 LLM | 不指定 next-speaker |
| 4 | **drift observer** | 每 N step / stagnation 时,用 minimal context(原始目标 + 最近 K 条 `agent.speak.text`)调一次 LLM 判跑题 → 偏离则 emit `coordinator.intervene(kind=drift)` 复诵目标 | 依赖 Phase 0 宪章;只发声不路由不改产物;LLM 失败则跳过(降级) |
| 5 | **收尾 gatekeeper** | 删 `_resolve_target`/默认链/必经步骤保护(v2 路径);Coordinator 在"链式自然终止 or stagnation 无解"时校验 4 核心 artifact 依赖图 → 缺失 emit `gate_reject` 点名上游,齐全 emit `gate_pass` + 决定 `task.end` 状态码 | 必经步骤保护**下沉**到 worker `requires`(P2 已做);Coordinator 只在收尾校验 |

---

## 5. v1 路径(零回归红线)

完全不动。所有 P3 改动守 `if is_v2:` / `if state.is_v2:`;v1 仍走 `Coordinator.on_handoff`
chain routing + `_resolve_target`(P3 只在 v2 路径绕过,不删 v1 用的代码)。沿用 P2 的 US1 红线
测试模式(字段级断言 v1 行为不变)。

---

## 6. 测试策略(ScriptedBackend 闭环)

| 测试面 | 怎么测 |
|---|---|
| v2 链式闭环 | `ScriptedBackend` 喂 8 step 输出 → 断言 subscription 驱动 `_run_step` 串完 → task `done`,全程 Coordinator 不调 `_resolve_target` |
| work-driver 转换 | `decide_to_speak==SPEAK` → 断言真跑 `_run_step`(produces artifact),非 emit confirm |
| 起点 bootstrap | is_v2 任务 → material 第一棒被触发(不经 chain handoff) |
| stagnation | 构造死锁 → 断言 emit `intervene(kind=stagnation)` 重新激活静默 worker → 任务 recover |
| drift | mock LLM 判断返回"跑题"→ 断言 emit `intervene(kind=drift)`;返回"未跑题"→ 不 intervene。LLM 判断函数注入 mock |
| 收尾 gatekeeper | artifact 缺失 → `gate_reject` 点名上游;齐全 → `gate_pass` + `task.end` 状态正确 |
| v1 零回归 | 沿用 P2 红线:v1 chain routing 字段级不变 |

---

## 7. 范围边界(Out of Scope)

- **budget 监控** → P8(brainstorm 决策:砍掉)
- **drift 全量 transcript** → P5;P3 只喂 minimal context(原始目标 + 最近 K 条 speak)
- **真 LLM 闭环验收** → 随 Windows issue 解决后人工补
- **P4 Reviewer 双轨 / P5 prompt 重写 / P6 并发 / P7 UX** → 路线图后续阶段
- **drift 误报自适应调参** → 本期固定阈值/频率,不做学习

---

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| **drift LLM 调用通道未定**(Coordinator 非 agent,无 agentDir/profile;Windows subprocess 阻塞) | plan 阶段定:**注入式 `DriftJudge` 抽象**(默认实现走轻量调用,测试注入 mock),与 `AgentBackend` 同款可注入模式,不绑死 openclaw CLI |
| **work-driver 与 v1 残留 race** | 严格 `if is_v2:` 隔离;v2 路径下 Coordinator `on_handoff` chain 分支整段不挂 |
| **stagnation 误判**(活跃判停滞 / 漏判死锁) | in-flight `_run_step` 计数器 + inbox 空检测双条件;超时作兜底非主判据 |
| **drift 假阳性**(误报打断流程) | `intervene(kind=drift)` 只复诵目标不路由/不改产物,温和;阈值保守 |
| **ScriptedBackend 闭环 ≠ 真闭环** | spec 明确标注:测试级通过不等于真 LLM 通过,真验收挂 Windows issue |

---

## 9. 宪章合规(Phase 0 改后)

放宽原则 IV 的 drift 一条后,其余原则仍守:I 脱敏(intervene 文案业务化中文,不漏 ID/技术词)、
II 中文、III 降级(drift LLM 失败 → 跳过判断,不挂任务)、V 隔离(per-agent lock 沿用 P2)。

---

## 10. Brainstorm 决策记录(可追溯)

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 闭环验收基线 | **ScriptedBackend 测试级**(真 LLM 挂 Windows issue) |
| 2 | 驱动模型 | **A — 链式 mention 自驱 + Coordinator 最小兜底**(起点 bootstrap + observer + 收尾 gate) |
| 3 | observer 监控范围 | **stagnation(规则)+ 完整 LLM drift**;budget 砍掉(→P8) |
| 4 | drift LLM 上下文 | **A — minimal context**(原始目标 + 最近 K 条 speak.text),不碰 8 step prompt(→P5) |
| 5 | drift 的宪章前置 | **Phase 0 先走 `/speckit-constitution`** 放宽"Coordinator 纯规则引擎",1.0.0→1.1.0 |

---

## 11. 下一步

本 design 经用户确认后 → `/speckit-specify` 产出 `specs/003-coordinator-transform/spec.md`
(会触发 `before_specify` git hook 从 main 创建 `003-coordinator-transform` 分支)。
**按用户要求:产出 spec 后停,不进 plan/tasks/implement。**
