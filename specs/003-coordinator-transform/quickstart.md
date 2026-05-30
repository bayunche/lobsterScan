# Quickstart — Coordinator 转型 + subscription work-driver（P3）

> 适用对象:本仓库后端 / harness 开发者。读完后你能理解 v2 任务的新驱动流程、
> 注入 mock DriftJudge 写测试、用 ScriptedBackend 跑 v2 闭环。
>
> 前置:装好依赖、main 已含 P1+P2、读过 [research.md](./research.md) 与 [data-model.md](./data-model.md)。
> **实施前置**:drift(US4)受 Phase 0 宪章修订阻塞,必须先 `/speckit-constitution`。

---

## 1. v2 驱动流程一图（P3 后）

```text
run_harness(is_v2=True)
   │
   ├─ 构造 subscriptions（P2）+ observer watchdog（P3）
   ├─ _bootstrap_first_step → emit_v2 AgentSpeak(mentions=[material])
   │                              │
   │                              ▼
   │                    material 订阅唤醒 → handle_v2_event
   │                              │
   │                       decide_to_speak == SPEAK
   │                              │
   │                    inflight+1 → _run_unlocked（真跑 step）→ inflight-1
   │                              │
   │                    _emit_v2_step_overlay: AgentSpeak(mentions=[下一棒])
   │                              │
   │                              ▼  链式推进 ... 直到 reviewer（mentions=[]）
   │
   └─ observer watchdog（每 0.5s）
         ├─ 非 quiescent → 跳过
         ├─ quiescent + artifact 齐 → gate_pass + done
         ├─ quiescent + 缺 + 有就绪静默 worker → stagnation 激活
         ├─ quiescent + 缺 + 无解 ×3 → gate_reject + partial
         └─ 每 10 拍 → drift judge（默认 NoDrift）

  v1 任务（is_v2=False）：Coordinator chain 驱动，observer/subscriptions 不构造（零开销）
```

**P2 → P3 的关键差异**:`handle_v2_event` 的 SPEAK 从"emit 收到气泡"变成"真跑 step";
起点从"Coordinator chain handoff"变成"bootstrap material";收尾从"Coordinator chain DONE"
变成"observer gatekeeper"。

---

## 2. 注入 mock DriftJudge 写测试

drift 判断走可注入抽象,测试不依赖真 LLM:

```python
# tests/orchestrator/test_drift.py
from app.orchestrator.coordinator_observer import (
    DriftJudge, DriftVerdict, set_default_drift_judge, get_default_drift_judge,
)

class StubDriftJudge(DriftJudge):
    def __init__(self, drifted: bool, text: str = "请回到本周工作汇报主题"):
        self._v = DriftVerdict(drifted=drifted, restate_text=text)
    async def judge(self, *, goal, recent_speaks):
        return self._v

@pytest.fixture
def mock_drift():
    original = get_default_drift_judge()
    def _make(drifted, text="请回到主题"):
        j = StubDriftJudge(drifted, text)
        set_default_drift_judge(j)
        return j
    yield _make
    set_default_drift_judge(original)


@pytest.mark.asyncio
async def test_drift_intervene_when_drifted(mock_drift, stub_state_v2, tmp_outputs_dir):
    mock_drift(drifted=True, text="请回到本周项目进度")
    # ... 构造 observer，跑 _check_drift → 断言 emit coordinator.intervene(kind=drift)

@pytest.mark.asyncio
async def test_drift_silent_when_not_drifted(mock_drift, ...):
    mock_drift(drifted=False)
    # ... 断言 不 emit drift 事件

@pytest.mark.asyncio
async def test_drift_degrades_on_judge_crash(...):
    # 注入一个 judge.judge 抛异常的 stub → 断言仅 log warn，不挂任务
```

---

## 3. 用 ScriptedBackend 跑 v2 闭环（SC-002）

复用 P1/P2 的 `ScriptedBackend`（conftest `mock_backend`）喂 8 个 step 的脚本化输出:

```python
@pytest.mark.asyncio
async def test_v2_subscription_drives_closed_loop(mock_backend, tmp_outputs_dir):
    backend = mock_backend([
        {"json_payload": {"payload": {...}, "handoff": {"to": "point-extractor"}}},  # material
        {"json_payload": {"summary": "...", "handoff": {"to": "structure"}}},        # point
        {"json_payload": {"chapters": [...], "handoff": {"to": "upward-opt"}}},       # structure
        # ... 8 个 step
    ])
    run = create_task(..., harness_version="v2")
    result = await run_harness(..., is_v2=True, run_step_fn=_run_step, ...)

    # 断言：8 个 step 全由 subscription 触发并产出，task 闭环
    assert result["reason"] == "done"
    # 断言：Coordinator 全程没 chain 路由（_resolve_target 0 次）—— 用 spy 或断言 on_handoff short-circuit
    assert backend.i == 8   # 8 step 都被跑
```

---

## 4. 单测清单（对应 spec user story）

| User Story | 测试文件 | 关键断言 |
|---|---|---|
| US1 v1 零回归 | `test_v1_regression.py`（扩） | v1 run_harness：observer is None / inflight_steps==0 / Coordinator 仍 chain 路由 / events.jsonl 无 P3 事件 |
| US2 work-driver 闭环 | `test_v2_workdriver.py`（新） | SPEAK→真跑 step；bootstrap 触发 material；8 step 链式闭环；on_handoff is_v2 short-circuit |
| US3 stagnation | `test_observer.py`（新） | 死锁场景 → quiescence 检测 → 激活就绪静默 worker → recover；无解 → partial |
| US4 drift | `test_drift.py`（新） | 4 分支：drifted→intervene / not→silent / crash→降级 / 不路由 |
| US5 gatekeeper | `test_observer.py`（新） | artifact 齐→gate_pass+done；缺→gate_reject+partial+点名 |

---

## 5. 调试

| 现象 | 检查 |
|---|---|
| v2 任务起点不动 | 1. `harness_version=="v2"`? 2. `_bootstrap_first_step` 是否 emit；3. material 的 mention_includes 命中? 4. observer 是否 start |
| step 不链式推进 | `_emit_v2_step_overlay` 是否 emit mentions=[下一棒]；下游 `requires` 是否满足(不满足会 silent) |
| 任务挂起不收尾 | observer watchdog 是否 start；`_is_quiescent` 条件(inflight/inbox/bootstrapped)；`STAGNATION_MAX_RETRY` |
| Coordinator 还在 chain 路由 | `on_handoff` 的 `if self.state.is_v2: return` 是否生效；is_v2 是否真为 True |
| drift 不触发 | 默认 `NoDriftJudge` 永远未跑题;要测 drift 必须 `set_default_drift_judge(mock)` |
| 双 gate 事件 | `_emit_v2_finalization`(P1 收尾示例)在 v2 是否与 observer gatekeeper 重复(F3,需收窄) |

---

## 6. 配置项

| 环境变量 | 默认 | 用途 |
|---|---|---|
| `OBSERVER_TICK_SEC` | 0.5 | watchdog 轮询周期 |
| `DRIFT_EVERY_N_TICK` | 10 | 每几拍判 drift |
| `DRIFT_RECENT_K` | 5 | drift 喂最近几条 speak |
| `STAGNATION_MAX_RETRY` | 3 | stagnation 无解兜底 → partial |

测试可 `monkeypatch.setattr` 这些常量(同 P2 `V2_LOCK_WAIT_SEC` 模式),如把 tick 调到 0.01s 加速。

---

## 7. 红线自检（提 PR 前）

- [ ] 任意用户可见层 grep 不到 `_resolve_target`/`stagnation`/`drift`/`gatekeeper`/`bootstrap`/`quiescence`/`inflight`
- [ ] v1 demo task 的 events.jsonl 与 main baseline 逐字段相同(diff)
- [ ] v1 路径:`state.observer is None`、`inflight_steps==0`、Coordinator 仍 chain 路由
- [ ] Coordinator class 只加了 4 个 handler 的 `is_v2` short-circuit,内部逻辑没动
- [ ] drift 真实现(LLMDriftJudge)**未**在本期落地(等 Phase 0 宪章 + Windows issue)
- [ ] 不动 Reviewer 现有审校(P4 才动)
- [ ] `pnpm --filter web-frontend build` + admin 全绿

---

## 8. 后续阶段衔接

| 阶段 | 哪里需要这次产出 |
|---|---|
| **P4 Reviewer 双轨** | reviewer 跑完后 emit ReviewerVerdict;gatekeeper 校验时纳入 verdict |
| **P5 prompt 重写** | drift 的 minimal context 升级为真 transcript-aware;LLMDriftJudge 落地 |
| **P6 并发** | observer watchdog 轮询 → 视性能改事件驱动;artifact 乐观并发 |
| **P7 UX** | 前端渲染 coordinator.intervene(stagnation/drift/gate)气泡 |
| **P8 运营** | budget 监控接入 observer watchdog(本期占位的扩展点) |
