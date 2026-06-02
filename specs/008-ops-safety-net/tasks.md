# Tasks: P8 — 运营兜底(operational safety net)

**Feature**: `008-ops-safety-net` | **Date**: 2026-06-02
**Input**: plan.md / spec.md / research.md / data-model.md / quickstart.md(均已就位)
**Tests**: 已请求(验收基线 = 测试级全绿 + 1 次真 LLM 端到端)

**改动文件**(全部既有,无新模块):
`apps/web-backend/app/orchestrator/{subscription,harness,coordinator_observer,pipeline}.py`
+ 测试 `apps/web-backend/tests/orchestrator/test_p8_{budget,rolling,yesman}.py` + `test_v1_regression.py` 扩。

---

## Phase 1: Setup(基线确认)

- [ ] T001 跑全量 orchestrator 测试确认 P7 绿基线:`uv run --directory apps/web-backend python -m pytest tests/orchestrator/ -q`,记录通过数(应 141 backend),作为零回归对照基准

---

## Phase 2: Foundational(阻塞所有 US 的共享前置)

**目的**:4 个 flag(SSOT)+ 3 个 enabled helper 是三条 US 的共同开关基座;默认全关 = 零回归。

- [ ] T002 在 `apps/web-backend/app/orchestrator/subscription.py` 新增 4 个模块级 flag:`V2_BUDGET_CAP:int=int(os.environ.get("V2_BUDGET_CAP","0"))`、`V2_ROLLING_SUMMARY:str=os.environ.get(...,"off")`、`V2_SUMMARY_THRESHOLD:int=int(os.environ.get(...,"20"))`、`V2_YESMAN_DEFENSE:str=os.environ.get(...,"off")`,并加入 `__all__`(沿用 `V2_FANOUT` 注释范式,标注默认关→零回归 + 与 P5/P6 正交)
- [ ] T003 [P] 在 `apps/web-backend/app/orchestrator/pipeline.py` 新增 `_rolling_enabled()` 与 `_yesman_enabled()` helper(每次读 `subscription.V2_ROLLING_SUMMARY`/`V2_YESMAN_DEFENSE`,try/except→False 降级,沿用 `_envelope_enabled`/`_fanout_enabled` 范式)

---

## Phase 3: User Story 1 — 预算硬上限,触顶软着陆(Priority: P1)🎯 MVP

**Goal**:任务级累计 token,触顶后停启新环节 + 复用 gatekeeper 软着陆(done/partial,产物保留),只一次。

**Independent Test**:设低 `V2_BUDGET_CAP` → `spent_tokens>=cap` 触发 `_on_budget_exceeded` → 软着陆 partial/done、已完成产物保留、`budget_exceeded` 后 `force_run_v2` 短路、只软着陆一次、emit `intervene(kind=budget)` 脱敏。

### 实现

- [ ] T004 [US1] 在 `apps/web-backend/app/orchestrator/harness.py` 的 `HarnessState` 新增 `spent_tokens:int=0` 与 `budget_exceeded:bool=False`(注释:v1 路径默认值不受影响)
- [ ] T005 [US1] 在 `harness.py` 的 `_run_step` 拿到 `TurnResult`/设完 `s.total_tokens` 后累计 `state.spent_tokens += getattr(s,"total_tokens",0)`(state 经 `prev["__state__"]` 取得,缺失/异常按 0,不抛)
- [ ] T006 [US1] 在 `apps/web-backend/app/orchestrator/pipeline.py` 的 `_quick_review` turn 完成后,把该 turn 的 token 也补计进 `state.spent_tokens`(覆盖 `_gate_review` 与 harness 质量审两条 review 路径的消耗;state 不可达时安全跳过)
- [ ] T007 [US1] 在 `coordinator_observer.py` 的 `_loop` 每拍增加 budget 检测:就近读 `subscription.V2_BUDGET_CAP`,`if cap>0 and not state.budget_exceeded and state.spent_tokens>=cap: await self._on_budget_exceeded()`(放既有 per-tick try/except 内)
- [ ] T008 [US1] 在 `coordinator_observer.py` 新增 `_on_budget_exceeded()`:① `state.budget_exceeded=True`;② `await self._emit_intervene("budget","为控制生成开销,我先用现有内容收尾。")`;③ `result=self.gate.check(self._task_id())`;④ `self._set_done("done" if result.passed else "partial")`(复用既有 gatekeeper,纯规则,守原则 IV)
- [ ] T009 [US1] 在 `harness.py` 的 `AgentWorker.force_run_v2` 开头加短路:`if self.state.budget_exceeded: return`(注意保持既有 inflight_steps 配平 —— 若调用方已 +1,需在此 finally/早返回处 -1,避免 quiescence 计数泄漏)
- [ ] T010 [US1] 在 `harness.py` 的 `handle_v2_event` SPEAK 真跑分支(`_run_unlocked` 调用前)加短路:`budget_exceeded` 为真时不启动新 step(与 T009 一致处理 inflight 配平)

### 测试

- [ ] T011 [P] [US1] 新建 `apps/web-backend/tests/orchestrator/test_p8_budget.py`:用 ScriptedBackend/monkeypatch 覆盖 —— (a) `spent_tokens` 随 step 累计;(b) `cap=0` 时 observer 检测短路、`budget_exceeded` 恒 False;(c) `spent_tokens>=cap` 触发 `_on_budget_exceeded`;(d) gate 齐→done / 不齐→partial;(e) 已完成 step 产物保留;(f) `budget_exceeded` 后 `force_run_v2` 短路不跑新 turn;(g) 重复进 `_on_budget_exceeded` 只软着陆一次;(h) emit 的 intervene `kind=="budget"` 且 text 无 token 数/task_id/agent_id

**Checkpoint**:US1 可独立验证 —— 预算硬上限闭环成立(MVP)。

---

## Phase 4: User Story 2 — rolling summary,上下文有界(Priority: P2)

**Goal**:`_transcript_block` 在 `_recent` 超阈值时折叠较早发言为一行确定性摘要,注入条数 ≤ K+1。

**Independent Test**:构造超阈值发言 → 渲染上下文为「尾部 K 行 + 1 行摘要」;未超→原样;observer 缺失→空串;`T=1` 边界不崩。

### 实现

- [ ] T012 [US2] 在 `pipeline.py` 的 `_transcript_block` 改造发言取数:先取**全量** `recent_all=list(obs._recent or [])`;`if _rolling_enabled() and len(recent_all)>V2_SUMMARY_THRESHOLD`:`tail=recent_all[-K:]` 逐条 + 一行 `（前 N 条发言已折叠：<头部拼接截断~200字>）`;否则维持 `recent_all[-K:]` 现状(K=`V2_TRANSCRIPT_K`)。就近读 `subscription.V2_SUMMARY_THRESHOLD`;摘要仅含发言文本(脱敏),不含 id;保持既有 try/except→空串降级(FR-016)

### 测试

- [ ] T013 [P] [US2] 新建 `apps/web-backend/tests/orchestrator/test_p8_rolling.py`:覆盖 —— (a) `off`(默认)走 `recent_all[-K:]` 与 P5 一致;(b) `on` 且未超阈值→全保留不折叠;(c) `on` 且超阈值→尾部 K 行 + 恰 1 行「前 N 条已折叠」,总注入条数 ≤ K+1,摘要无 id;(d) observer=None / 异常→返回空串;(e) `T=1`/`K≥len` 边界不抛错

**Checkpoint**:US2 可独立验证 —— 上下文有界,与 budget 互补减 token。

---

## Phase 5: User Story 3 — yes-man 防御,审校不橡皮图章(Priority: P3)

**Goal**:开启时给审校路径注入「对立质疑」prompt 段,一处覆盖 `_gate_review` + harness 质量审两轨。

**Independent Test**:`V2_YESMAN_DEFENSE=on` → reviewer prompt 含对立质疑段;`off`(默认)→ 不含。

### 实现

- [ ] T014 [US3] 在 `pipeline.py` 新增 `_YESMAN_BLOCK` 常量(对立质疑:主动找瑕疵/假设作者夸大/不达标如实指出/禁「看上去 OK」式放行)+ 在 `QUICK_REVIEW_PROMPT` 构造处(`_quick_review` 内 `.format(...)` 前/拼接)按 `_yesman_enabled()` 注入该段;关闭时一字不拼(注:`_quick_review` 被 `_gate_review` 与 harness `_reviewer_quality_review` 共用 → 一处覆盖两轨)

### 测试

- [ ] T015 [P] [US3] 新建 `apps/web-backend/tests/orchestrator/test_p8_yesman.py`:monkeypatch `subscription.V2_YESMAN_DEFENSE` —— (a) `on` 时 `_quick_review` 实际发给 backend 的 message 含对立质疑关键句(用 ScriptedBackend 捕获 message);(b) `off`(默认)时不含;(c) 注入不破坏 `{accept,comment,reason}` 输出契约解析

**Checkpoint**:US3 可独立验证 —— 审校立场可切换,不影响收尾。

---

## Phase 6: Polish & 零回归 & 验收

- [ ] T016 在 `apps/web-backend/tests/orchestrator/test_v1_regression.py` 扩充:断言三 flag unset(默认)下 —— `_transcript_block` 输出与 P5 字段级一致、`QUICK_REVIEW_PROMPT` 注入后文本与 P7 一致、observer 无 budget 干预、`force_run_v2` 不短路(字段级零回归,FR-002/SC-001)
- [ ] T017 跑全量 `pytest tests/orchestrator/ -q` 确认零回归(≥ T001 基线数 + P8 新增)且三 P8 测试文件全绿
- [ ] T018 [P] 真 LLM 端到端(按 quickstart §2):开三 flag + 低 `V2_BUDGET_CAP` 跑一次真任务,用 POST 返回的**真实 task_id** 读 `data/outputs/<task_id>/{task.json,chat.jsonl,events.jsonl}` 核对 SC-002/003/004/005(触顶后新环节=0、partial/done、产物保留、budget 发声脱敏、折叠生效、yesman 生效)。环境网络受阻则 deferred 并如实标注,测试级三轨 + T016 为主证
- [ ] T019 [P] 更新 `CLAUDE.md` SPECKIT 块(P8 标 ✅ Implemented + 实现位置 + 测试数 + 诚实标注 T018 实测状态)与 `docs/开发文档.md` §9.4 路线图表 P8 行(标已落地)

---

## Dependencies & 执行顺序

```
Phase 1 (T001 基线)
  └─ Phase 2 (T002 flag → T003 helper)        ← 阻塞所有 US
       ├─ Phase 3 US1 (T004→T005→T006→T007→T008→T009→T010→T011)  [P1 MVP]
       ├─ Phase 4 US2 (T012→T013)              [P2;只依赖 Phase 2,与 US1 独立]
       └─ Phase 5 US3 (T014→T015)              [P3;只依赖 Phase 2,与 US1/US2 独立]
            └─ Phase 6 (T016→T017,然后 T018/T019 [P])
```

- **US1/US2/US3 三条彼此独立**(正交,FR-003):Phase 2 完成后可并行推进。
- US1 内部 T004→T010 多为同文件(harness/observer)顺序改;T011 测试 [P]。
- US2 仅 `pipeline._transcript_block`;US3 仅 `pipeline` yesman —— 同文件不同函数,谨慎并行(建议顺序避冲突)。

## Parallel 机会

- T011 / T013 / T015 三个测试文件互相独立 [P]。
- T018 / T019(真 LLM + 文档)互相独立 [P]。
- 跨 US 并行:Phase 2 后,一人 US1、一人 US2、一人 US3。

## MVP 范围

**US1(预算硬上限)= MVP**:三者中唯一直接防「失控烧钱」的安全网。仅交付 Phase 1+2+3 即得可用的预算兜底;
US2(上下文有界)/US3(审校防附和)为增量增强,可后续按需开启。

## 格式校验

所有任务均含 `- [ ]` + `Txxx` +(US 阶段)`[USx]` + 文件路径;Setup/Foundational/Polish 无 Story 标签(符合规范)。
