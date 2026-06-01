# Tasks: P5 — Transcript-Aware Prompt + speak/silent/done 输出契约

**Feature**: `005-transcript-aware-prompt` | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

验收基线(spec SC + 用户敲定):ScriptedBackend 测试级全绿 + v1 字段级零回归(SC-001)
+ envelope 新契约测试绿(SC-002)+ 1 次真 LLM 端到端(SC-003)。
所有改动集中在 `apps/web-backend/app/orchestrator/`;flag `V2_PROMPT_MODE` 默认 legacy 短路。

测试请求:spec 验收基线明确要求测试(SC-001~005),故本清单**含测试任务**(沿用 P2-P4 惯例)。

---

## Phase 1: Setup(配置 + 常量)

- [X] T001 在 `apps/web-backend/app/orchestrator/subscription.py` 的 env 常量区(V2_MENTION_LIMIT 旁)新增 `V2_PROMPT_MODE = os.environ.get("V2_PROMPT_MODE", "legacy")` 与 `V2_TRANSCRIPT_K = int(os.environ.get("V2_TRANSCRIPT_K", "8"))`,并加入 `__all__`(research 决策 3)
- [X] T002 在 `apps/web-backend/app/orchestrator/pipeline.py` 新增 `_envelope_enabled() -> bool`(每次读 `subscription.V2_PROMPT_MODE`,支持测试 monkeypatch,同 V2_LOCK_WAIT_SEC 模式),返回是否 == "envelope"

---

## Phase 2: Foundational(US1/US2 共同前置 · 阻塞后续)

**目标**:transcript 数据源接入 + 信封 rule 常量就位,US1/US2 都依赖。

- [X] T003 [P] 在 `pipeline.py` 新增 `_transcript_block(state, k=None) -> str`:从 `state.observer._recent`(最近发言)+ `state.observer._artifact_log`(artifact 时序)读取,渲染中文「群聊上下文」段落(发言人用 AGENT_DISPLAY 中文名);k 缺省读 V2_TRANSCRIPT_K;observer 为 None / 异常 / 两者皆空 → 返回空串(FR-004/005/016,research 决策 1/4)
- [X] T004 [P] 在 `pipeline.py` 新增 `_ENVELOPE_RULE` 常量(信封版输出契约文案,替代 JSON_RULE):指示 agent 输出 `{action(speak|silent|done), mentions, intent, reason, artifact}`,artifact 内放原 typed 产物;含 silent/done 何时用的说明(FR-006,data-model §2)
- [X] T005 在 `pipeline.py` 新增 `_unwrap_envelope(parsed) -> (action, mentions, intent, reason, artifact)`:有 action 键→信封(取 artifact,非 dict→{});无 action→旧格式(整体当 artifact、action=speak、mentions 取 handoff.to);action 非法→speak;parsed=None→(speak,[],propose,"",{})(FR-007/008/012,research 决策 2,data-model §2 解析校验)

---

## Phase 3: User Story 1 — Agent 感知群聊上下文(Priority: P1)🎯 MVP

**Goal**: transcript 模式下,每个 agent 的 prompt 含「群聊上下文」段落(最近 K 条发言 + artifact 摘要)。
**Independent Test**: 触发 agent 构造 prompt,断言含群聊上下文段;legacy 不含;超 K 截断;空起点不报错。

- [ ] T006 [US1] 改 `pipeline.py` 的 `_step_prompt`(line ~1689 区):`_envelope_enabled()` 为真时,在 `_build_global_ctx` 之后注入 `_transcript_block(state)`(需把 state 传入 _step_prompt / 各 _build_*_prompt 的调用链;若 state 不在作用域,从 _run_step 传入)(FR-001/002/013)
- [ ] T007 [US1] 确认 legacy 模式 `_step_prompt` 不注入 transcript(短路),与 P4 现状字段级一致(FR-014)
- [ ] T008 [P] [US1] 新增 `apps/web-backend/tests/orchestrator/test_transcript_block.py`:测 `_transcript_block` 渲染(有发言+artifact→含中文段)、超 K 截断(FR-003)、空起点→空串(FR-004)、observer None→空串(FR-016)、AGENT_DISPLAY 中文名映射
- [ ] T009 [P] [US1] 在 test_transcript_block.py 加 `_step_prompt` 集成断言:envelope 模式 prompt 含群聊上下文段(US1-AC1);legacy 模式不含(US1-AC2)

**Checkpoint**: US1 可独立验收 —— transcript 感知交付,输出契约仍可保持 legacy。

---

## Phase 4: User Story 2 — speak/silent/done 信封输出契约(Priority: P2)

**Goal**: envelope 模式 agent 输出信封;解析取出 artifact 回填;action 驱动 overlay。
**Independent Test**: 跑 agent 出信封,断言 artifact 正确 unwrap、speak 点名下游、silent/done 按语义、旧格式容错。

- [ ] T010 [US2] 改 `pipeline.py` 的 `_step_prompt`:envelope 模式用 `_ENVELOPE_RULE` 替代 `JSON_RULE`(line 1689 `return body + JSON_RULE`);legacy 仍用 JSON_RULE(FR-006/013/014)
- [ ] T011 [US2] 改 `pipeline.py` 的 `_run_step`(line ~2379 `s.output_json = extract_json(res.text)`):envelope 模式下 `extract_json` 后调 `_unwrap_envelope`,把 artifact 回填 `s.output_json`,把 (action, mentions, intent, reason) 暂存到 step(如 `s._envelope = {...}`)供 overlay 用(FR-008/012/016)
- [ ] T012 [US2] 确认 T011 后既有 needs_retry/needs_help 信号读取(line ~2396 读 s.output_json)无需改 —— unwrap 已把 artifact 回填 s.output_json(research 决策 2 派生发现);加注释说明
- [ ] T013 [US2] 改 `pipeline.py` 的 `_emit_v2_step_overlay`(line ~1782):envelope 模式从 step 暂存的信封读 mentions/intent/action;`action=speak`→现有 AgentSpeak(mentions,intent)+ArtifactUpdate;`action=silent`→emit AgentSilent(reason)、不产 artifact、不点名;`action=done`→mentions 空(走 quiescence→gatekeeper);legacy 模式保持从 handoff 合成(FR-009/010/011,research 决策 5)
- [ ] T014 [P] [US2] 新增 `apps/web-backend/tests/orchestrator/test_envelope_parse.py`:测 `_unwrap_envelope` 信封格式(取 artifact)、旧格式(整体当 artifact+action 推断 speak+handoff.to)、缺 action、artifact 非 dict、action 非法、parsed=None 六种(FR-007/008/012,US2-AC4)
- [ ] T015 [P] [US2] 新增 `apps/web-backend/tests/orchestrator/test_p5_e2e.py`:ScriptedBackend envelope 模式跑链式闭环(同 test_v2_workdriver 风格),断言 artifact 正确 unwrap、speak mentions 驱动下游、silent 不产 artifact、done→gatekeeper 收尾(US2-AC1/2/3,SC-002)
- [ ] T016 [US2] 在 test_p5_e2e.py 加 SC-005 断言:envelope 包裹再解出的业务产物(如 copywriting 的 script_md/slides)与 legacy 模式逐字段一致

**Checkpoint**: US2 可独立验收 —— 信封契约 + transcript 双双工作于 envelope 模式。

---

## Phase 5: User Story 3 — 双轨可回退,零回归(Priority: P1)

**Goal**: flag 一键切换;legacy 字段级零回归;真 LLM 端到端跑通。
**Independent Test**: legacy 跑回归套件全绿+字段级一致;envelope 跑新测试绿;切换仅改 env。

- [ ] T017 [P] [US3] 扩 `apps/web-backend/tests/orchestrator/test_v1_regression.py`:显式断言 `V2_PROMPT_MODE` 未设/legacy 时,`_step_prompt` 不含 transcript、用 JSON_RULE、`_unwrap_envelope` 对旧格式恒等(s.output_json 与 extract_json 直出一致)(FR-014,SC-001)
- [ ] T018 [US3] 跑全量 `pytest apps/web-backend/tests -q` 确认 legacy 零回归(原基线 + 新增全绿),记录数字到 tasks.md 收尾
- [ ] T019 [US3] 真 LLM 端到端(quickstart §3):`V2_PROMPT_MODE=envelope` 起后端 + 提交 1 个 v2 task,用真实 task_id 轮询,断言 task.json status∈{done,partial}、8 step 全 success、events 无解析失败/KeyError(SC-003);失败若因 deepseek 网络抖动则重试(spec 假设),非契约问题
- [ ] T020 [US3] 回退验证(quickstart §4):去 flag 重起,重跑 task 仍等价 P4 跑通(FR-015)

**Checkpoint**: US3 完成 = P5 全量验收达标。

---

## Phase 6: Polish & 收尾

- [ ] T021 [P] 更新 `docs/开发文档.md` §9.4.5 P5 行标注「已落地」+ 关键实现位置(_transcript_block/_unwrap_envelope/V2_PROMPT_MODE)
- [ ] T022 [P] 更新 CLAUDE.md SPECKIT 块:P5 状态 Planning→Implemented + Code/Tests 位置 + tasks 完成数
- [ ] T023 在 `.env.example` 加 `V2_PROMPT_MODE` / `V2_TRANSCRIPT_K` 注释说明(研发开关,默认 legacy)
- [ ] T024 终轮 `pytest apps/web-backend/tests -q` 全绿 + commit「P5 全栈落地」

---

## Dependencies(完成顺序)

```
Setup(T001-T002)
  └─> Foundational(T003-T005)         ← 阻塞所有 US
        ├─> US1(T006-T009)  P1 🎯MVP   ← transcript 感知,可独立交付
        ├─> US2(T010-T016)  P2         ← 依赖 Foundational(_ENVELOPE_RULE/_unwrap);与 US1 共享 _step_prompt 改动(T006/T010 同函数,需串行)
        └─> US3(T017-T020)  P1         ← 依赖 US1+US2 落地后才能验真 LLM + 零回归
              └─> Polish(T021-T024)
```

**关键串行点**:T006(US1 注入 transcript)与 T010(US2 换 rule)都改 `_step_prompt` 同一函数 → 必须串行,不可 [P]。T011/T013 改 _run_step/_emit_v2_step_overlay,与 T006 不同函数可在 T006 后并行推进各自分支。

## Parallel 机会

- Foundational:T003(_transcript_block)/ T004(_ENVELOPE_RULE)不同代码块 → [P]。
- 测试任务 T008/T009、T014/T015、T017 多为独立文件 → [P]。
- Polish T021/T022/T023 不同文件 → [P]。

## Implementation Strategy

- **MVP = US1**(T001-T009):光是 transcript 感知就交付"群聊协作"价值,且 legacy 输出契约不变、风险最低。可先单独 ship + 验收。
- **增量 2 = US2**(T010-T016):叠加信封契约,envelope 模式完整。
- **增量 3 = US3**(T017-T020):真 LLM 验证 + 零回归确认,达 spec 全量验收。
- 全程 flag 默认 legacy → 任何时点 main 都可安全(envelope 是 opt-in)。

## 总计

- 任务数:24(Setup 2 + Foundational 3 + US1 4 + US2 7 + US3 4 + Polish 4)
- 测试任务:6(T008/T009/T014/T015/T016/T017)
- 真 LLM 验收:T019(envelope)+ T020(回退)
