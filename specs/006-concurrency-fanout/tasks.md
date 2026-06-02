# Tasks: P6 — EventBus fan-out 并发 + html/video 真并行

**Feature**: `006-concurrency-fanout` | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

验收基线(spec SC + 用户敲定):ScriptedBackend 测试级全绿 + 字段级零回归(SC-001)
+ fanout 测试绿(SC-002)+ 1 次真 LLM 对比 on/off 耗时(SC-003/004)。
改动集中在 `apps/web-backend/app/orchestrator/`;flag `V2_FANOUT` 默认 off 短路。

测试请求:spec 验收基线明确要求测试(SC-001~005),故本清单**含测试任务**(沿用 P2-P5 惯例)。

---

## Phase 1: Setup(配置 + 常量)

- [X] T001 在 `apps/web-backend/app/orchestrator/subscription.py` env 常量区(V2_PROMPT_MODE 旁)新增 `V2_FANOUT = os.environ.get("V2_FANOUT", "off")`,并加入 `__all__`(data-model §1)
- [X] T002 在 `apps/web-backend/app/orchestrator/pipeline.py` 新增 `_fanout_enabled() -> bool`(每次读 `subscription.V2_FANOUT`,支持测试 monkeypatch,同 `_envelope_enabled` 模式),返回是否 == "on"
- [X] T003 在 `pipeline.py` 新增 `COPYWRITING_FANOUT = ("html-designer", "video-producer")` 常量(data-model §2)

---

## Phase 2: Foundational(无 — 改动直接落在各 US)

> P6 无独立 Foundational 阶段:flag/常量(Setup)就绪后,US1/US2 各自改一处函数,互不阻塞。

---

## Phase 3: User Story 1 — html/video 真并行(Priority: P1)🎯 MVP

**Goal**: fanout on 时 copywriting 完成后同时唤醒 html-designer + video-producer。
**Independent Test**: ScriptedBackend 跑到 copywriting done,断言 overlay mentions 含两者;off 时单目标。

- [X] T004 [US1] 改 `pipeline.py` 的 `_emit_v2_step_overlay`(②段 mentions 合成处):step==copywriting 且 `_fanout_enabled()` 为真时,legacy 分支 mentions 取 `list(COPYWRITING_FANOUT)`(替代 DEFAULT_NEXT_STEP 单目标);envelope 分支若信封 mentions 已含两者则尊重,否则补齐 COPYWRITING_FANOUT(FR-001/005)
- [X] T005 [US1] 确认 fanout off 时 copywriting mentions 仍走原单目标(DEFAULT_NEXT_STEP["copywriting"]="html_design"),与 P5 一致(FR-005/011)
- [X] T006 [P] [US1] 新增 `apps/web-backend/tests/orchestrator/test_p6_parallel.py`:ScriptedBackend 跑到 copywriting done,断言 fanout on → emit 的 AgentSpeak.mentions 含 html-designer + video-producer(US1-AC1);off → 仅单目标(US1-AC4)
- [X] T007 [P] [US1] 在 test_p6_parallel.py 加并发触发断言:两个 worker 注册 + copywriting 双 mention dispatch 后,html_design 与 video_production 两个 step 都被触发(run_step_fn 各调一次);inflight 峰值 ≥2(US1-AC2,SC-004)

**Checkpoint**: US1 可独立验收 —— html/video 并行触发,EventBus 仍可串行。

---

## Phase 4: User Story 2 — EventBus 事件分发并发(Priority: P2)

**Goal**: fanout on 时 emit 的多 handler 并发 + 异常隔离;off 串行。
**Independent Test**: 多 handler 注册,fanout on emit → 并发执行(总耗时≈最慢单个);一个 handler 抛错不影响其他。

- [X] T008 [US2] 改 `apps/web-backend/app/orchestrator/harness.py` 的 `EventBus.emit`(line ~79):`_fanout_enabled()` 为真时,wildcard + kind handlers 用 `asyncio.gather(*[安全包装(h) for h], return_exceptions=True)` 并发;为假时走原串行 for-await(FR-006/007/009,research 决策 2)
- [X] T009 [US2] 确认 T008 的并发分支异常隔离:单 handler 抛错仅 log,不影响其他 handler 完成(沿用现有 try/except 语义,FR-007/013)
- [X] T010 [P] [US2] 新增 `apps/web-backend/tests/orchestrator/test_fanout_emit.py`:注册多个 async handler,fanout on emit → 全部被调用(并发);一个 handler raise → 其余仍被调用 + 不抛出(US2-AC1/2);off → 串行顺序保持(US2-AC3)
- [X] T011 [P] [US2] 在 test_fanout_emit.py 加去重断言:同 step 经 per-agent lock + success 跳过,fanout on 下仍只跑一次(US2-AC4,SC-005)—— 复用/参考 test_per_agent_lock 风格

**Checkpoint**: US2 可独立验收 —— EventBus 并发 + 异常隔离 + 去重不破。

---

## Phase 5: User Story 3 — 双开关可回退,零回归(Priority: P1)

**Goal**: V2_FANOUT 一键切换;off 字段级零回归;真 LLM on/off 耗时对比。
**Independent Test**: off 跑全量全绿+字段级一致;on 跑 P6 测试绿;切换仅改 env。

- [X] T012 [P] [US3] 扩 `apps/web-backend/tests/orchestrator/test_v1_regression.py`:显式断言 `V2_FANOUT` 未设/off 时,`_fanout_enabled()` False、EventBus.emit 走串行、copywriting mentions 单目标(FR-011,SC-001)
- [X] T013 [US3] 跑全量 `pytest apps/web-backend/tests -q` 确认 fanout off 零回归(原 123 + 新增全绿)
- [ ] T014 [US3] 真 LLM 端到端(quickstart §3):V2_FANOUT=on 起后端 + 提交 task,用真实 task_id 轮询,断言 status∈{done,partial}、8 step 全 success、html-designer 与 video-producer 的 agent.start 都在对方 agent.done 前出现(并行实证,SC-004);记录 copywriting.done→收尾墙钟(SC-003)
- [ ] T015 [US3] 对比基线 + 回退:V2_FANOUT off 重起跑同 task,确认 status 等价 + 串行(html done 才 video start);on 的 Script→收尾耗时 ≤ off(SC-003);unset 即回退(FR-012)

**Checkpoint**: US3 完成 = P6 全量验收达标。

---

## Phase 6: Polish & 收尾

- [ ] T016 [P] 更新 `docs/开发文档.md` §9.4.5 P6 行标注「已落地」+ 实现位置(EventBus.emit fanout / COPYWRITING_FANOUT / V2_FANOUT)
- [ ] T017 [P] 更新 CLAUDE.md SPECKIT 块:P6 Planning→Implemented + Code/Tests 位置 + 真 LLM 验证
- [ ] T018 [P] 在 `.env.example` 加 `V2_FANOUT` 注释说明(研发开关,默认 off;与 V2_PROMPT_MODE 正交)
- [ ] T019 终轮 `pytest apps/web-backend/tests -q` 全绿 + commit「P6 全栈落地」

---

## Dependencies(完成顺序)

```
Setup(T001-T003)              ← flag + 常量
  ├─> US1(T004-T007)  P1 🎯MVP  ← 改 _emit_v2_step_overlay(独立)
  ├─> US2(T008-T011)  P2        ← 改 EventBus.emit(独立,与 US1 不同文件/函数 → 可并行推进)
  └─> US3(T012-T015)  P1        ← 依赖 US1+US2 落地后才能验真 LLM + 零回归
        └─> Polish(T016-T019)
```

**关键点**:US1(pipeline.py _emit_v2_step_overlay)与 US2(harness.py EventBus.emit)改**不同文件不同函数**,落地阶段可并行推进各自分支(测试也独立文件)。US3 真 LLM 验证需两者都在。

## Parallel 机会

- US1 与 US2 改不同文件 → 实现可并行;测试 T006/T007、T010/T011、T012 独立文件 → [P]。
- Polish T016/T017/T018 不同文件 → [P]。

## Implementation Strategy

- **MVP = US1**(T001-T007):html/video 并行触发即交付主要耗时收益,EventBus 可仍串行。
- **增量 2 = US2**(T008-T011):叠加 EventBus 并发分发。
- **增量 3 = US3**(T012-T015):真 LLM 验证耗时下降 + 零回归确认。
- 全程 flag 默认 off → 任何时点 main 都安全(fanout 是 opt-in)。

## 总计

- 任务数:19(Setup 3 + US1 4 + US2 4 + US3 4 + Polish 4)
- 测试任务:5(T006/T007/T010/T011/T012)
- 真 LLM 验收:T014(on 并行+耗时)+ T015(off 基线对比+回退)
