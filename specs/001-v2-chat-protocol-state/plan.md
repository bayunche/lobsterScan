# Implementation Plan: v2 群聊协议 + 状态模型层（P1）

**Branch**: `001-v2-chat-protocol-state` | **Date**: 2026-05-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-v2-chat-protocol-state/spec.md`

---

## Summary

把 `apps/web-backend/app/orchestrator/{harness.py, pipeline.py}` 扩出 v2 群聊化协作所需的**协议层**
（5 类新事件 + message_id + reply_to）与**状态层**（4 核心 artifact 显式版本号），通过 task 级
`harness_version` flag 让 v1/v2 双轨并存。

技术路线：
- **不引入新外部依赖**：复用现有 Pydantic v2（FastAPI 栈自带）+ stdlib `secrets` 模块
- **不动现有 worker / coordinator 行为**：P2-P5 的事；P1 只埋数据契约 + 事件类型
- **分叉点单一**：`pipeline.execute()` 入口判 `run.harness_version`；分支只影响新事件发出与
  artifact 包装函数，共用 worker 调度路径
- **向后兼容靠"只扩不改"**：`AgentEvent.kind` 与现有 v1 事件类型保持不变；新增 v2 事件用
  `kind` 形如 `agent.speak` / `artifact.update` 等命名空间隔开
- **artifact 文件命名 `<name>_v<N>.json` 并保留 `<name>.json` 副本**：v1 读取代码完全不动

---

## Technical Context

| 项 | 选择 |
|---|---|
| **Language/Version** | Python 3.11+（`uv venv`） |
| **Primary Dependencies** | Pydantic v2（已在 FastAPI 栈中）；stdlib `secrets`、`uuid`、`json`、`pathlib` |
| **Storage** | 文件系统：`data/outputs/<task_id>/`（既有约定不动）；artifact 版本文件追加；`events.jsonl` 既有 append-only |
| **Testing** | pytest + pytest-asyncio（如未配置则新增到 `apps/web-backend/pyproject.toml`）；mock LLM 走 `set_default_backend()` |
| **Target Platform** | Linux server（含 WSL2 dev）；Python 进程内 asyncio |
| **Project Type** | web-service（monorepo 下 `apps/web-backend` 单后端） |
| **Performance Goals** | v1 路径无性能回归（baseline = 当前主干）；v2 路径事件序列化开销 < 5ms/事件（in-memory + 1 行 append） |
| **Constraints** | 100% 向后兼容（所有 v1 字段与文件保留）；100% 无外部新依赖；100% 不改 agent prompt |
| **Scale/Scope** | 受影响代码：`harness.py`（~485 行 → 估 +200 行）；`pipeline.py`（~2900 行 → 估 +150 行）；新增 3-4 个小 module（合计 ~400 行）；新增单测 ~200 行 |

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

按 `.specify/memory/constitution.md` 5 大核心原则逐条核：

| 原则 | 是否通过 | 证据 |
|---|---|---|
| **I. 用户可见层脱敏** | ✅ PASS | spec FR-018 / FR-019 明确禁止 `message_id` / `artifact_version` / `harness_version` 出现在 chat 气泡 / 错误提示 / 导出文件；这些仅入 `events.jsonl` / `data/.logs/` / admin 控制台。SSE 推回前端的 `chat.message` schema 不变（FR-019）。|
| **II. 中文为产品语言** | ✅ PASS | P1 不动任何 UI / 前端文案 / 导出文件名（spec Out of Scope 列在 P7）。 |
| **III. 降级而非崩溃** | ✅ PASS | spec FR-020 明确：任何 v2 协议层错误（schema 校验失败 / message_id 冲突 / artifact 写入异常）不允许导致整任务 `failed`，按现有降级路径走。 |
| **IV. Coordinator / Reviewer 职责边界** | ✅ PASS | P1 **不改** Coordinator 行为（`_resolve_target` / 必经步骤保护原样保留，是 P3 的事），**不改** Reviewer 行为（即时质量门是 P4 的事）。本期只为它们准备未来需要的事件类型与字段。 |
| **V. Agent 自治与隔离** | ✅ PASS | 不改 `OpenClawSubprocessBackend` 子进程模型；不动 `agentDir` 路径；4 核心 artifact 版本化也只在写入侧加包装，agent 读取契约不变。 |

**Gate 结论：通过 0 violation，可进 Phase 0。**

Complexity Tracking：N/A（无需 justify 复杂度违规）。

---

## Project Structure

### Documentation (this feature)

```text
specs/001-v2-chat-protocol-state/
├── plan.md              # 本文件（/speckit-plan 输出）
├── spec.md              # /speckit-specify 已就位
├── research.md          # Phase 0 输出（本次产出）
├── data-model.md        # Phase 1 输出（本次产出）
├── quickstart.md        # Phase 1 输出（本次产出）
├── contracts/           # Phase 1 输出（本次产出）
│   ├── agent.speak.schema.json
│   ├── agent.silent.schema.json
│   ├── coordinator.intervene.schema.json
│   ├── reviewer.verdict.schema.json
│   └── artifact.update.schema.json
├── checklists/
│   └── requirements.md  # /speckit-specify 已就位
└── tasks.md             # Phase 2 输出（由 /speckit-tasks 生成，本命令不产）
```

### Source Code (repository root)

只动 `apps/web-backend/`，前端零改动。

```text
apps/web-backend/
├── app/
│   ├── orchestrator/
│   │   ├── harness.py          # 扩 +200 行：新事件 kind 常量、emit_v2 辅助、events.jsonl 写入兼容
│   │   ├── pipeline.py         # 扩 +150 行：TaskRun 增 harness_version、execute() 分叉、artifact 写入包装
│   │   ├── events_v2.py        # 【新增】Pydantic models：Message / Verdict / Intervene + 5 类事件 schema
│   │   ├── artifacts_v2.py     # 【新增】4 核心 artifact 版本化写入器 + 版本目录管理
│   │   ├── ids.py              # 【新增】message_id 生成 + 校验（msg_<8 位 hex>）
│   │   └── replay_check.py     # 【新增】最小回放校验工具（CLI script，FR-015）
│   ├── api/
│   │   └── tasks.py            # 微调：任务创建 API 接受可选 harness_version 字段
│   └── ...                     # 其余文件不动
└── tests/                      # 【新建目录】
    ├── conftest.py             # 共享 fixtures（mock_backend 走 set_default_backend）
    └── orchestrator/
        ├── test_events_v2_schema.py    # 5 类事件 schema 校验单测（happy + invalid）
        ├── test_artifacts_v2.py        # artifact 版本号递增 / base_version 链 / 文件命名
        ├── test_v1_regression.py       # v1 路径事件流与 baseline 逐字段比对
        └── test_v2_integration.py      # v2 集成测试（用 mocked agent backend 跑全链）
```

**Structure Decision**: 单 web-service 内增量扩展。新增 4 个小 module（`events_v2.py` /
`artifacts_v2.py` / `ids.py` / `replay_check.py`）保持 SRP，避免把 `harness.py` / `pipeline.py`
撑得更大。tests 目录之前不存在（CLAUDE.md 已注："`tests/` is described in docs but no test files exist"），
本期新建，给 P2-P8 后续阶段铺路。

---

## Complexity Tracking

无 violations，本段不填。

---

## Phase 0 Output

详见 [research.md](./research.md) — 6 项技术决策已拍板（schema 库 / message_id 格式 / v1-v2 分叉 /
artifact 文件命名 / events.jsonl 承载 / 单测 mock 策略）。

## Phase 1 Output

- **数据模型**：[data-model.md](./data-model.md) — Pydantic 模型 + 字段约束 + 状态转移
- **契约**：[contracts/](./contracts/) — 5 类事件 JSON Schema 定义
- **快速上手**：[quickstart.md](./quickstart.md) — 开发者怎么开 v2、跑 demo、查 events.jsonl

## Post-Design Constitution Re-Check

Phase 1 设计完成后回头再核 5 大原则：

| 原则 | Post-Design 结论 |
|---|---|
| I. 用户可见层脱敏 | ✅ data-model.md 标注：`Message.message_id` / `Artifact.version` 仅服务端使用，序列化到 SSE 前必须 strip |
| II. 中文为产品语言 | ✅ 无 UI / 文案改动 |
| III. 降级而非崩溃 | ✅ `events_v2.py` 的 `emit_safe()` 包装：schema 校验失败 → log warn + 写入 `agent.failed`，不抛 |
| IV. Coordinator / Reviewer 边界 | ✅ contracts/ 里的 `reviewer.verdict.schema.json` 明确 reviewer **不能** 直接产生路由副作用；`suggested_fix_agent` 是建议，由 Coordinator 决定要不要转写（这是 P3-P4 的事，但 schema 已守住边界） |
| V. Agent 自治与隔离 | ✅ artifact 版本目录命名 `<name>_v<N>.json` 与 latest `<name>.json` 副本并存，agent 读取路径不变 |

**Re-check 结论：通过。可进 `/speckit-tasks`。**
