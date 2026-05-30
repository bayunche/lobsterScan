# Implementation Plan: Coordinator 转型(observer + gatekeeper)+ subscription work-driver（P3）

**Branch**: `003-coordinator-transform` | **Date**: 2026-05-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-coordinator-transform/spec.md`

---

## Summary

在 v2 路径把"驱动真实 step"的职责从 **Coordinator chain**(`_resolve_target` / 默认链 / 必经步骤保护)**转移**到 **subscription**:`handle_v2_event` 的 SPEAK 分支从 P2 的"emit 收到气泡"升级为"真跑 `_run_unlocked()`";起点由 bootstrap 触发 material(不经 chain);Coordinator 的 4 个 chain handler 在 `is_v2` 时 short-circuit,退为一个 **observer watchdog**(新模块 `coordinator_observer.py`)统一承载 stagnation(规则,liveness)/ drift(可注入 LLM,minimal context)/ 收尾 gatekeeper(核心 artifact 完整性 → done/partial)。v1 路径整套字段级零回归。

**架构定位**(详 research.md):P2 的 v2 任务实际是"Coordinator chain 驱动真实 step + subscription chat overlay 并存";P3 断开 v2 路径的 chain 驱动,让 subscription 成为真 work-driver。链式推进复用 P2 已落地的 `_emit_v2_step_overlay`(同一份 per-step emit,语义从"群聊呈现"升级为"驱动信号")。

技术路线:
- **零新外部依赖**:复用 stdlib `asyncio`(watchdog = 周期 task);复用 P1 `InterveneKind` 枚举(无新事件 schema)
- **v1 路径零开销 / 零回归**:所有改动 `is_v2` 守卫;Coordinator class 仅加 4 行 short-circuit
- **drift 可注入 + mock-first**:`DriftJudge` 抽象 + `NoDriftJudge` 默认(不调 LLM,不违宪);`LLMDriftJudge` 真实现受 Phase 0 宪章 + Windows issue 阻塞,本期不交付
- **验收基线 = ScriptedBackend 测试级闭环**;真 LLM 闭环挂 Windows issue 后人工补

---

## Technical Context

| 项 | 选择 |
|---|---|
| **Language/Version** | Python 3.11+ |
| **Primary Dependencies** | stdlib `asyncio` only;复用 P1 `events_v2`/`artifacts_v2`/`ids` + P2 `subscription` |
| **Storage** | 无新存储;observer / inflight / bootstrapped 状态在 `HarnessState`(任务级内存) |
| **Testing** | pytest + pytest-asyncio(P1 已配);复用 `ScriptedBackend`/`mock_backend`/`stub_state_v2` fixtures;新增 `mock_drift` fixture |
| **Target Platform** | Linux server(含 WSL2 dev);Python 进程内 asyncio。**真 LLM 路径在 Windows dev 受 subprocess issue 阻塞** |
| **Project Type** | web-service(`apps/web-backend`,monorepo 单后端) |
| **Performance Goals** | v1 路径 0 性能回归(baseline = P2 之后 main);v2 observer watchdog 轮询开销 < 1ms/拍(in-memory inbox/inflight 查询);tick 0.5s 不显著占 CPU |
| **Constraints** | 100% 向后兼容(v1 + P1/P2 v2 现有行为保留);100% 无外部新依赖;Coordinator class 仅加 short-circuit,内部逻辑不动;drift 真实现受宪章 + 环境双前置 |
| **Scale/Scope** | 新增 `coordinator_observer.py` ~310 行;扩展 `harness.py` ~150 行(handle_v2_event work-driver + step-success 去重 + force_run_v2 + Coordinator short-circuit + run_harness bootstrap/observer);调整 `pipeline.py`(v2 收尾改 observer 接管);新增单测 ~400 行(81 tests 全绿) |

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 是否通过 | 证据 |
|---|---|---|
| **I. 用户可见层脱敏** | ✅ PASS | FR-023 禁 `_resolve_target/stagnation/drift/gatekeeper/bootstrap/quiescence` 出现在用户可见层;`coordinator.intervene` 经现有翻译层渲染中文气泡;SC-008 grep 兜底 |
| **II. 中文为产品语言** | ✅ PASS | P3 不动 UI;observer emit 的 intervene text 业务化中文("全部产物齐了,收尾"/"流程卡住了,我来推进一下") |
| **III. 降级而非崩溃** | ✅ PASS | FR-024:work-driver / stagnation / drift / gate 异常全 try/except 降级;drift LLM 失败跳过(FR-017);gatekeeper 必给确定终止状态(FR-020) |
| **IV. Coordinator / Reviewer 边界** | ✅ PASS(宪章 1.1.0 已落地) | drift 让 Coordinator 调一次 LLM,突破"纯规则引擎"(§9.4.7 决策 1)。**Phase 0 宪章修订已完成**(commit `edf1621`,原则 IV 新增 drift 受限 LLM 例外,1.0.0→1.1.0);放宽后其余红线(不路由 next-speaker / 不审质量 / 不改产物)仍守(FR-011/016,`test_drift_does_not_route_or_mutate` 验证)。Reviewer 不动(P4 才动) |
| **V. Agent 自治与隔离** | ✅ PASS | per-agent lock 沿用 P2;work-driver 跑 step 仍走同一把锁;agentDir 不共享红线不变 |

**Gate 结论**:**通过**。原则 IV 的 drift 部分以 Phase 0 宪章修订为前置 —— **该修订已落地**(commit `edf1621`,宪章 1.0.0→1.1.0)。这是"按宪章 §Governance 流程先升级宪章再实施"的合规路径,非"未 justify 的违规"。(历史:本表初版写于宪章修订前,标 ⚠️ 待修订;现已更新为 PASS。)

Complexity Tracking:见下节(1 项 justified)。

---

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Coordinator observer 引入一次 LLM 调用(drift),突破"纯规则引擎" | 群聊自由度提高后,agent 间自由 @ 可能逐渐偏离汇报主题;跑题纠偏需要语义理解,确定性规则无法判断"是否偏题" | 规则式退化 drift(brainstorm B′ 选项)与 stagnation 高度重叠、价值有限(已在 brainstorm 评估排除);完全不做 drift 则丢失质量护栏。**经宪章修订合规引入**,且严格限定(只发声/不路由/不审质量/minimal context),非全面 LLM 化 Coordinator |

---

## Project Structure

### Documentation (this feature)

```text
specs/003-coordinator-transform/
├── plan.md              # 本文件(/speckit-plan 输出)
├── spec.md              # /speckit-specify 已就位
├── research.md          # Phase 0 输出(本次产出,9 项决策 + 4 派生发现)
├── data-model.md        # Phase 1 输出(本次产出)
├── quickstart.md        # Phase 1 输出(本次产出)
├── checklists/requirements.md  # /speckit-specify 已就位
└── tasks.md             # Phase 2 输出(由 /speckit-tasks 生成,本命令不产)
```

**contracts/ 目录跳过** —— P3 是内部架构演进(无新对外 API;复用 P1 `InterveneKind` 枚举,无新事件 schema),按 skill 指引"Skip if project is purely internal"。

### Source Code (repository root)

只动 `apps/web-backend/`,前端零改动。

```text
apps/web-backend/
├── app/
│   ├── orchestrator/
│   │   ├── coordinator_observer.py  # 【新增】observer watchdog + DriftJudge + ArtifactGate (~280 行)
│   │   ├── harness.py               # 扩 ~120 行：
│   │   │                            #   - HarnessState: inflight_steps / bootstrapped / observer + start/stop_observer
│   │   │                            #   - AgentWorker.handle_v2_event: SPEAK → 真跑 _run_unlocked(work-driver)
│   │   │                            #   - Coordinator.on_handoff/on_failed/on_needs_help/on_needs_retry: is_v2 short-circuit
│   │   │                            #   - run_harness: is_v2 → bootstrap material + start/stop observer
│   │   ├── pipeline.py              # 调 ~30 行：v2 收尾 _emit_v2_finalization 收窄(gatekeeper 接管,避免双 gate)
│   │   ├── subscription.py          # 不动(P2 落地)
│   │   ├── events_v2.py             # 不动(P1;复用 InterveneKind)
│   │   └── artifacts_v2.py          # 不动(P1;ArtifactGate 复用 next_version)
│   └── api/                         # 不动
└── tests/
    └── orchestrator/
        ├── test_v2_workdriver.py        # 【新增】work-driver 转换 + bootstrap + 链式闭环 (~120 行)
        ├── test_observer.py             # 【新增】stagnation + gatekeeper (~120 行)
        ├── test_drift.py                # 【新增】drift 4 分支 + mock 注入 (~80 行)
        ├── test_v1_regression.py        # 扩 ~30 行：v2-flag-off 路径 observer/inflight 不构造
        └── conftest.py                  # 扩 ~20 行：mock_drift fixture
```

**Structure Decision**: 新增独立 module `coordinator_observer.py` 把 observer/gatekeeper/drift 逻辑聚拢一处(同 P2 `subscription.py` 风格),避免再撑大 `harness.py`。Coordinator class 本身**几乎不改**(只加 4 行 short-circuit)—— observer 是与之并存的独立组件,符合"不改 Coordinator 内部行为"的最小侵入(research.md F1)。

---

## Phase 0 Output

详见 [research.md](./research.md) —— **9 项关键决策 + 4 派生发现已拍板**:

| # | 决策 | 选择 |
|---|---|---|
| 0 | v2 断开 chain 驱动 | Coordinator on_handoff/... `is_v2` short-circuit |
| 1 | 起点 bootstrap | run_harness is_v2 enqueue bootstrap 事件到 material inbox |
| 2 | work-driver 转换 | handle_v2_event SPEAK → 调 `_run_unlocked` 真跑 step |
| 3 | 链式闭环 | 复用 P2 `_emit_v2_step_overlay`(per-step mention) |
| 4 | observer 统一 | 一个 watchdog 承载 stagnation/gatekeeper/drift |
| 5 | quiescence 检测 | `inflight_steps==0 + inbox 全空 + bootstrapped + 未完成` |
| 6 | drift 通道 | 注入式 `DriftJudge`;`NoDriftJudge` 默认 mock-first |
| 7 | gatekeeper 收尾 | 齐→gate_pass+done;无解→gate_reject+partial |
| 8 | v1 零回归 | 全 `is_v2` 守卫 |

## Phase 1 Output

- **数据模型**:[data-model.md](./data-model.md) —— 新模块 8 个类型 + harness 扩展 + 状态转移
- **快速上手**:[quickstart.md](./quickstart.md) —— v2 驱动流程图 / mock DriftJudge / ScriptedBackend 闭环 / 单测清单 / 调试 / 红线自检

## Post-Design Constitution Re-Check

Phase 1 设计完成后回核 5 大原则:

| 原则 | Post-Design 结论 |
|---|---|
| I 脱敏 | ✅ 新字段(inflight_steps/bootstrapped/observer)+ DriftJudge/ArtifactGate 全在内部;intervene 文案中文;不进 SSE/导出层 |
| II 中文 | ✅ observer emit 的 4 类 intervene text 业务化中文 |
| III 降级 | ✅ observer `_loop`/drift/gate 全 try/except;drift 失败跳过;gatekeeper 必给 done/partial |
| IV 边界 | ✅ drift 的 Phase 0 宪章修订已落地(1.1.0);放宽后限定严格(不路由/不审质量/minimal context);Coordinator class 仅 4 行 short-circuit;Reviewer 不动 |
| V 隔离 | ✅ work-driver 跑 step 走 P2 per-agent lock;agentDir 不共享不变 |

**Re-check 结论**:通过(原则 IV 的 Phase 0 宪章前置已明确为实施第一步)。可进 `/speckit-tasks`。

**提示**:`/speckit-tasks` 拆解时,应把 **Phase 0 宪章修订** 作为 US4(drift)的前置阻塞任务显式列出;US1/US2/US3/US5 不阻塞,可先排。
