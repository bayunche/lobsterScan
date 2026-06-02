# Implementation Plan: P6 — EventBus fan-out 并发 + html/video 真并行

**Branch**: `006-concurrency-fanout` | **Date**: 2026-06-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/006-concurrency-fanout/spec.md`
设计文档:`docs/superpowers/specs/2026-06-01-p6-concurrency-design.md`

## Summary

让 html-designer + video-producer 真并行(copywriting 完成时同时 @ 二者),并把
`EventBus.emit` 从串行改为 `asyncio.gather` 并发,降低总耗时。全部由 `V2_FANOUT`
(默认 off)flag 控制,off 时完全短路 = P5 现状(零回归)。去重靠 per-agent lock +
step.status==success 跳过(非 EventBus 串行),收尾靠现有 quiescence(inflight 归 0)。

## Technical Context

**Language/Version**: Python 3.12(web-backend)

**Primary Dependencies**: 既有 asyncio。无新增三方依赖。

**Storage**: N/A(不改产物存储;html/video 各写各自 HTML/视频,不写 4 核心 artifact)

**Testing**: pytest(`apps/web-backend/tests/orchestrator/`),ScriptedBackend + monkeypatch

**Target Platform**: Windows 开发机(plain uvicorn + ProactorEventLoop;真 LLM deepseek)

**Project Type**: web-service 后端单模块(`apps/web-backend/app/orchestrator/`)

**Performance Goals**: Script 就绪→收尾段总耗时 ≤ 串行(并行不更慢,通常更快,SC-003)

**Constraints**:
- 不改 observer / reviewer / gatekeeper 决策逻辑
- 不破坏 P3/P4 顺序去重(per-agent lock + success 跳过)与 P5 契约
- 单一异步事件循环(无多线程),inflight 计数增减无竞争
- 默认 off 字段级零回归(灰度策略)

**Scale/Scope**: 极局部 —— EventBus.emit(harness.py)+ _emit_v2_step_overlay 的
copywriting mentions(pipeline.py)+ V2_FANOUT flag(subscription.py)。

## Constitution Check

*GATE: 通过。P6 无需宪章修订。*

| 原则 | 影响 | 判定 |
|---|---|---|
| I 用户可见脱敏 | 不新增用户可见串 | ✅ |
| II 中文产品语言 | 不涉及 | ✅ |
| III 降级而非崩溃 | FR-004/007/013:并行一支失败→另一支照常+partial;gather return_exceptions 隔离 | ✅ 强化 |
| IV Coordinator/Reviewer 边界 | **不改** observer/reviewer/gatekeeper 决策;并发是执行优化 | ✅ |
| V Agent 自治与隔离 | html/video **不同 agentDir**(per-agent lock 隔离)→ 并行不共享 agentDir | ✅ 已核实 |

工程约束:不简化 extract_json(不碰);双开关可回退(灰度)。全满足 → 无 Complexity Tracking。

## Project Structure

### Documentation (this feature)

```text
specs/006-concurrency-fanout/
├── plan.md / research.md / data-model.md / quickstart.md / spec.md / tasks.md
```
contracts/ 跳过:复用 P1 v2 事件 schema;P6 不新增对外接口,纯执行行为变化。

### Source Code(只动 web-backend orchestrator)

```text
apps/web-backend/app/orchestrator/
├── harness.py
│   └── EventBus.emit:按 _fanout_enabled() 选串行(off)/ asyncio.gather(on)  # FR-006/007/009
├── pipeline.py
│   ├── 新增 COPYWRITING_FANOUT = ("html-designer", "video-producer")          # FR-001
│   └── _emit_v2_step_overlay:copywriting + fanout on → mentions 双目标       # FR-001/005
├── subscription.py
│   └── 新增 V2_FANOUT flag(默认 off)+ __all__                               # FR-010
└── pipeline.py 新增 _fanout_enabled()(每次读 V2_FANOUT)                     # FR-010/012

apps/web-backend/tests/orchestrator/
├── test_fanout_emit.py       # US2 EventBus 并发 + 异常隔离 + flag off 串行
├── test_p6_parallel.py       # US1 copywriting 双 mention + ScriptedBackend 并行触发
└── test_v1_regression.py(扩)# US3 fanout off 零回归
```

**Structure Decision**: 单后端模块极局部改造。去重不动(per-agent lock 已隔离 html/video);
收尾不动(observer quiescence 天然覆盖 inflight 归 0)。flag 默认 off,P6 全短路 = P5。

## Phases

- **Phase 0**(research.md):4 技术决策(并发去重为何安全、gather 异常隔离、收尾不改、
  copywriter 真 LLM 单 mention 兜底)。
- **Phase 1**(data-model.md / quickstart.md):V2_FANOUT / COPYWRITING_FANOUT / inflight 峰值
  三实体 + 开/关耗时对比 quickstart。
- **Phase 2**(tasks.md):/speckit-tasks,按 US1→US2→US3 拆。

## Complexity Tracking

> 无违规,无需填写。
