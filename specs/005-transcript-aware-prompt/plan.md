# Implementation Plan: P5 — Transcript-Aware Prompt + speak/silent/done 输出契约

**Branch**: `005-transcript-aware-prompt` | **Date**: 2026-05-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/005-transcript-aware-prompt/spec.md`
设计文档:`docs/superpowers/specs/2026-05-31-p5-transcript-aware-prompt-design.md`

## Summary

把 8 个 step 的 prompt 升级为「群聊感知」(注入 transcript_tail = 最近 K 条发言 +
artifact 摘要),并把 agent 输出从 v1「prose + typed JSON(含 handoff)」改为群聊信封
`{action(speak|silent|done), mentions, intent, reason, artifact}`,其中 artifact 子对象
沿用各 step 现有业务产物 schema(零改动)。技术路径:新增共享 prompt block + 信封解析层,
全部由 `V2_PROMPT_MODE`(默认 legacy)flag 控制,关闭时完全短路 = P4 现状(零回归)。

## Technical Context

**Language/Version**: Python 3.12(web-backend)

**Primary Dependencies**: 既有 —— FastAPI / asyncio / pydantic(events_v2)。无新增三方依赖。

**Storage**: N/A(transcript_tail 不持久化,每次构造 prompt 实时渲染;artifact 仍走
现有 `data/outputs/<task_id>/` + `write_versioned`)

**Testing**: pytest(`apps/web-backend/tests/orchestrator/`),ScriptedBackend mock + monkeypatch

**Target Platform**: Windows 开发机(plain uvicorn + ProactorEventLoop;真 LLM 走 deepseek)

**Project Type**: web-service 后端单模块(只动 `apps/web-backend/app/orchestrator/`)

**Performance Goals**: 无新增性能目标。transcript 注入由 K(默认 8)上限控 token;
压缩留 P8。

**Constraints**:
- 不动 typed artifact schema(MaterialPool / ReportCore / Outline / Script 及 slides/narrations)
- 不动 observer / reviewer / subscription 的决策逻辑
- LLM JSON 不可靠 → 信封解析必须复用 `extract_json` 容错链 + 自身降级
- v1/v2 双轨:legacy 模式字段级零回归(宪章 Governance 灰度策略)

**Scale/Scope**: 局部改造,集中在 `pipeline.py`(prompt 构造 + `_run_step` 解析 +
`_emit_v2_step_overlay`)+ 少量 harness/observer 复用点。8 个 step 共享同一套
transcript block + 信封 rule,非逐个重写业务 prompt。

## Constitution Check

*GATE: 通过。P5 无需宪章修订。*

| 原则 | 影响 | 判定 |
|---|---|---|
| I 用户可见脱敏 | transcript_tail 注入的是 agent 间 prompt(非用户可见);信封 action/mentions 不进用户气泡 | ✅ 不涉及新用户可见串 |
| II 中文产品语言 | transcript block 渲染用中文;信封字段是内部协议(英文 key 可) | ✅ |
| III 降级而非崩溃 | FR-016:transcript 渲染 / 信封解析异常 MUST 降级,不挂任务 | ✅ 显式要求 |
| IV Coordinator/Reviewer 边界 | **不动** observer/reviewer 决策;transcript 是 prompt 工程,不引入新 LLM 决策权;Coordinator 仍规则引擎 | ✅ 红线不触 |
| V Agent 自治与隔离 | 仍走 4 个 typed versioned artifact 通信(信封只是外壳,artifact schema 不变);agentDir 不共享 | ✅ |

**工程约束**:复用 `extract_json`(不简化解析器);双轨可回退(灰度策略)。全部满足。
→ **无 Complexity Tracking 条目**。

## Project Structure

### Documentation (this feature)

```text
specs/005-transcript-aware-prompt/
├── plan.md              # 本文件
├── research.md          # Phase 0(本次)
├── data-model.md        # Phase 1(本次)
├── quickstart.md        # Phase 1(本次)
├── spec.md              # /speckit-specify 产出
└── tasks.md             # /speckit-tasks 产出(下一步)
```
contracts/ 跳过:复用 P1 已定的 v2 事件 schema(AgentSpeak/AgentSilent/ArtifactUpdate),
P5 不新增对外接口,信封是 prompt↔解析的内部约定(在 data-model 描述即可)。

### Source Code(只动 web-backend orchestrator)

```text
apps/web-backend/app/orchestrator/
├── pipeline.py          # 主战场:
│   ├── 新增 _transcript_block(state, k)        # FR-001/002/003/004/005
│   ├── 新增 _ENVELOPE_RULE(信封版输出契约)     # FR-006
│   ├── 新增 _unwrap_envelope(parsed)           # FR-007/008/012
│   ├── 改 _step_prompt:按 flag 选 JSON_RULE / _ENVELOPE_RULE + 注入 transcript  # FR-013/014
│   ├── 改 _run_step ~line 2379:extract_json 后 _unwrap_envelope             # FR-008/012/016
│   └── 改 _emit_v2_step_overlay:mentions/intent 从信封读(action 分支)        # FR-009/010/011
├── subscription.py 或新增模块  # V2_PROMPT_MODE flag 常量(env 读取,与既有 V2_* 同风格)
└── (复用)coordinator_observer.py 的 _recent / _artifact_log 作 transcript 数据源  # FR-005

apps/web-backend/tests/orchestrator/
├── test_transcript_block.py     # US1
├── test_envelope_parse.py       # US2(_unwrap_envelope 双格式 + 畸形降级)
├── test_p5_e2e.py               # US2/US3 ScriptedBackend envelope 链式闭环
└── test_v1_regression.py(扩)   # US3 legacy 零回归
```

**Structure Decision**: 单后端模块局部改造,无新增包/服务。transcript 数据源复用
observer 已收集的 `_recent`(发言)+ `_artifact_log`(artifact 时序),不新建订阅源(FR-005)。
flag 默认 legacy,P5 全部新代码在 legacy 下短路(零回归)。

## Phases

- **Phase 0**(research.md):敲定 5 个技术决策(数据源复用、信封解析降级策略、flag 读取位置、
  transcript 渲染格式、done 语义落点)。
- **Phase 1**(data-model.md / quickstart.md):定义 transcript_tail / envelope / flag 三实体的
  字段与状态,给真 LLM 验证 quickstart。
- **Phase 2**(tasks.md):/speckit-tasks 产出,按 US1→US2→US3 优先级拆任务。

## Complexity Tracking

> 无违规,无需填写。
