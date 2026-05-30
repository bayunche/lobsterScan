# Quickstart — Reviewer 双轨 + verdict.fail 修复闭环（P4）

> 适用对象:本仓库后端 / harness 开发者。读完后你能理解 Reviewer 双轨流程、
> 用 ScriptedBackend 脚本化 reviewer turn 测质量轨、单测流程逻辑轨 + 修复闭环。
>
> 前置:装好依赖、main 已含 P1+P2+P3、读过 [research.md](./research.md) 与 [data-model.md](./data-model.md)。

---

## 1. Reviewer 双轨流程一图（P4 后）

```text
v2 任务链式推进
  │
  ├─ 每产出核心 artifact → emit artifact.update
  │     └─ reviewer 订阅唤醒 → handle_v2_event(reviewer 特化)
  │           ├─ ArtifactUpdate → 质量审(_quick_review)→ ReviewerVerdict(quality)
  │           │     ├─ pass → 无事
  │           │     └─ fail → bus → observer._on_verdict
  │           │                 → 转写 intervene 点名 producer + 重置 step needs_fix
  │           │                 → force_run_v2 修复(上限 REVIEW_FIX_MAX_RETRY=2)
  │           │                     → 重产 artifact → 质量轨重审(闭环)
  │           └─ 被 mention(非 artifact)→ silent(reviewer 不跑链式 step)
  │
  └─ 链式终止 → quiescence → observer._on_quiescence
        ① 流程逻辑审 ProcessReviewer(版本一致/依赖图/参与度)→ ReviewerVerdict(process_logic)
              └─ fail → bus → _on_verdict 触发修复(若可定位 fix_agent)
        ② 综合决策:产物齐 + 无未解决 fail → done;否则 stagnation/修复/partial

  v1 任务:reviewer 走 _gate_review(REVIEW_GATES + _quick_review,fail 重做 1 次),无双轨(零回归)
```

**P3 → P4 关键差异**:reviewer 从"链式 work-driver 最后一棒"变为"全程订阅审校者";`ReviewerVerdict`
从 P3 `_emit_v2_finalization` 的**示例**变为**真实双轨 emit**;新增 verdict.fail → 修复闭环。

---

## 2. ScriptedBackend 测质量轨

质量轨真 LLM(report-reviewer)走 subprocess(Windows 阻塞)。测试用 ScriptedBackend 脚本化
reviewer 的 `_quick_review` turn:

```python
@pytest.mark.asyncio
async def test_quality_track_fail_triggers_fix(mock_backend, tmp_outputs_dir):
    # reviewer 的 _quick_review turn 被脚本化为 reject
    backend = mock_backend([
        {"json_payload": {"accept": False, "comment": "套话偏多",
                          "reason_if_reject": "建议把客户回访写具体"}},
    ])
    # ... 构造 state + reviewer worker + producer worker
    # emit artifact.update(producer=material)→ reviewer 质量审 → verdict(fail)
    # → observer._on_verdict → 重置 material step + force_run_v2
    # 断言:emit ReviewerVerdict(quality, fail);material step.status=="needs_fix";material 被重激活
```

---

## 3. 单测流程逻辑轨（纯规则,无 LLM）

```python
# test_process_review.py
from app.orchestrator.process_review import ProcessReviewer

def test_version_consistency_fail(tmp_outputs_dir):
    # 写 ReportCore base_version 指向不存在的 MaterialPool 版本
    result = ProcessReviewer().check("tsk_x", events=[])
    assert not result.passed
    assert any("MaterialPool" in f.what or "ReportCore" in f.what for f in result.findings)

def test_dependency_order_fail():
    # events 里 Outline 的 artifact.update 早于 ReportCore
    events = [_artifact_event("Outline", ts=1), _artifact_event("ReportCore", ts=2)]
    result = ProcessReviewer().check("tsk_x", events)
    assert not result.passed   # 依赖违例

def test_participation_fail(tmp_outputs_dir):
    # 只写 MaterialPool,缺 ReportCore/Outline/Script
    result = ProcessReviewer().check("tsk_x", events=[])
    assert not result.passed
    assert result.fix_targets  # 缺失 producer 定位

def test_all_pass(tmp_outputs_dir):
    # 4 核心 artifact 齐、版本链一致、顺序对
    result = ProcessReviewer().check("tsk_x", events=_ordered_events())
    assert result.passed
```

---

## 4. 单测修复闭环

```python
@pytest.mark.asyncio
async def test_verdict_fail_fix_cycle(tmp_outputs_dir, monkeypatch):
    from app.orchestrator import coordinator_observer
    monkeypatch.setattr(coordinator_observer, "REVIEW_FIX_MAX_RETRY", 2)
    # 构造 observer + producer worker(material)
    # emit ReviewerVerdict(fail, suggested_fix_agent=material) → bus → _on_verdict
    # 断言:material step.status=="needs_fix";material force_run_v2 被调度;_fix_retries[material]==1
    # 再 fail 一次 → retries==2;第三次 → 达上限不再修

@pytest.mark.asyncio
async def test_fix_agent_missing_no_op(...):
    # verdict(fail, suggested_fix_agent=None)→ 不触发修复(FR-013)
```

---

## 5. 单测清单（对应 spec user story）

| User Story | 测试文件 | 关键断言 |
|---|---|---|
| US1 v1 零回归 | `test_v1_regression.py`（扩） | v1 _gate_review 不变;v1 events 无 reviewer.verdict/needs_fix |
| US2 质量轨 | `test_reviewer_quality.py`（新） | artifact.update → verdict(quality);pass/fail;版本去重;被 mention silent |
| US3 流程逻辑轨 | `test_process_review.py`（新） | 3 规则各齐/缺;verdict(process_logic) |
| US4 修复闭环 | `test_fix_cycle.py`（新） | 转写/重置 needs_fix/force_run_v2/上限/fix_agent 缺失不修 |
| US5 收尾决策 | `test_reviewer_e2e.py`（新） | 双因子:齐+pass→done;有未解决 fail→partial |

---

## 6. 调试

| 现象 | 检查 |
|---|---|
| reviewer 不审 artifact | 1. is_v2? 2. reviewer 订阅 artifact_id_in(P2 WORKER_PROFILE)? 3. handle_v2_event reviewer 特化分支命中? 4. _reviewed 版本去重是否误拦 |
| 质量 fail 不触发修复 | observer bus.on("reviewer.verdict") 注册? _on_verdict 读 suggested_fix_agent? fix_agent 在 workers? 未达上限? |
| 修复重跑被去重拦 | step.status 是否重置为 needs_fix(解除 work-driver success 去重);force_run_v2 直接跑不查 status |
| 流程逻辑审不触发 | _process_reviewed flag;quiescence 是否到达;ProcessReviewer 异常被 catch |
| 任务不收尾 | _on_quiescence 双因子;未解决 fail 是否卡住;REVIEW_FIX_MAX_RETRY / STAGNATION_MAX_RETRY |
| 双 verdict | 流程逻辑审 _process_reviewed 去重;质量轨 _reviewed 版本去重 |

---

## 7. 配置项

| 环境变量 | 默认 | 用途 |
|---|---|---|
| `REVIEW_FIX_MAX_RETRY` | 2 | 同一 fix_agent 的 verdict.fail 修复次数上限 |

(沿用 P3 的 OBSERVER_TICK_SEC / STAGNATION_MAX_RETRY / DRIFT_* 等。)

---

## 8. 红线自检（提 PR 前）

- [ ] 用户可见层 grep 不到 `process_logic`/`verdict`/`suggested_fix_agent`/`needs_fix`/`quiescence`
- [ ] v1 demo 的 events.jsonl 与 main baseline 逐字段相同;v1 无 reviewer.verdict
- [ ] v1 路径 `_gate_review`/REVIEW_GATES 行为不变(reviewer 特化分支仅 v2)
- [ ] Reviewer 不直接 @(verdict.fail 由 observer 转写 intervene);不重写产物(修复由 producer 自跑)
- [ ] 质量轨真 LLM(report-reviewer)**未**在本期实跑(挂 Windows issue);ScriptedBackend 测试级覆盖
- [ ] `pnpm --filter web-frontend build` + admin 全绿

---

## 9. 后续阶段衔接

| 阶段 | 哪里需要这次产出 |
|---|---|
| **P5 prompt 重写** | 质量轨 _quick_review 升级为 transcript-aware;跨引用一致性可纳入 |
| **P6 并发** | 多 artifact 并行产出 → 质量轨并发审(per-agent lock 已守)|
| **P7 UX** | 前端渲染 reviewer.verdict(quality/process_logic)气泡 + 修复点名 |
