# Feature Specification: v2 群聊协议 + 状态模型层（P1）

**Feature Branch**: `001-v2-chat-protocol-state`

**Created**: 2026-05-28

**Status**: Draft

**Input**: User description: "P1 — v2 群聊协议 + 状态模型层（最小可用版，零行为改动）。把 v1「单目标 handoff + Coordinator 规则路由」管线扩出 v2 群聊化协作所需的协议与状态原语，v1/v2 双轨并存。详 `docs/开发文档.md` §3.5 / §9.4。"

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 现有 v1 用户感受零变化 (Priority: P1)

正在使用「会汇报」生成汇报材料的现有用户，**完全感知不到**后端在进行架构演进 ——
任务创建流程、所见的群聊气泡、生成的汇报包、所需时长、错误降级行为，都与当前一致。
这条故事保护「不破不立」：v2 基础设施先就位，但默认走 v1 路径。

**Why this priority**: 这是改造的最低底线，违反即产品事故。任何引入 v2 字段的工作都必须先保证
v1 行为完全不变；这条不通过，后续 P2-P8 都没法在生产灰度。

**Independent Test**: 在 main 分支跑一个 demo task（pnpm dev + 任意预置素材），在 P1 分支
不开 v2 flag 跑同一个 demo task；比对两边的 `events.jsonl`（顺序与字段一致）、最终产物（`script.md` /
`web-presentation/index.html`）、`task.json` 状态字段、SSE 推回前端的 chat 序列。要求**逐行 / 逐字段**等同。

**Acceptance Scenarios**:

1. **Given** 用户在前端 `/feat/report` 上传素材并提交一个 3 分钟项目进度汇报任务，**When** 后端运行
   在 P1 分支且未传 `harness_version` 字段，**Then** 任务行为与 main 分支跑同样输入完全一致（事件流、产物、耗时、状态码）。
2. **Given** 同上场景，**When** 用户主动通过 5 个 refine chip 之一（如「再短一点」）触发改写，
   **Then** 改写流程与 main 分支一致，refine 重跑的 step 与最终 status 不变。
3. **Given** 视频生成 provider 配额不足导致 `video-producer` 失败，**When** 任务降级走 partial，
   **Then** 用户可见提示（"视频生成中 / 失败，可稍后重试"）与 main 分支一致；reviewer 仍出审校建议。

---

### User Story 2 - 实验任务可走 v2 路径并落出 5 类新事件 (Priority: P2)

开 v2 feature flag（任务级显式选择）的实验性任务，**能在 `events.jsonl` 里出现 5 类新事件**：
`agent.speak` / `agent.silent` / `coordinator.intervene` / `reviewer.verdict` / `artifact.update`，
每条事件有稳定 `message_id`、合规 schema、可被 replay 工具消费。

**Why this priority**: 是 v2 后续阶段（P2 worker 订阅 / P3 Coordinator 转型 / P4 Reviewer 双轨 / P5 prompt
重写）的事件总线契约源头；契约不就位，下游阶段没法增量推进。

**Independent Test**: 写一个最小测试 fixture（mock 4 个 agent 各发 1 条 `agent.speak` + reviewer 发 1 条
`reviewer.verdict` + coordinator 发 1 条 `coordinator.intervene` + 1 条 `agent.silent`），运行 v2 路径，
读取 `events.jsonl` 校验：5 类事件至少各出现 1 条；每条事件 `message_id` 全任务内唯一；可通过 schema 校验。

**Acceptance Scenarios**:

1. **Given** 创建任务时传 `harness_version: "v2"`，**When** 任务跑完，**Then** `events.jsonl`
   内出现至少 1 条 `agent.speak`、`agent.silent`（可为 0 条，若无 agent 选择沉默则不强制）、
   `coordinator.intervene`（如有流程纠偏触发）、`reviewer.verdict`、`artifact.update`。
2. **Given** 一条 `agent.speak` 事件被发出，**When** 后续 agent 通过 `reply_to: <message_id>` 引用，
   **Then** replay 工具能根据 `message_id` 索引完整线程链。
3. **Given** 任意 v2 事件，**When** 通过 JSON-Schema 校验，**Then** 必填字段齐全、字段类型正确、
   `intent` 取值在 `{ask, propose, challenge, confirm, yield, done}` 枚举内。

---

### User Story 3 - 4 个核心产物支持版本化引用 (Priority: P2)

未来的审校 / 重做 / 比对场景需要能引用产物的"某一版"（如「请回退到 ReportCore v2 重新生成 Script」），
而不是只看 latest 覆盖。P1 为 `MaterialPool` / `ReportCore` / `Outline` / `Script` 4 个核心 artifact
引入显式版本号字段：`(id, version, producer, base_version, delta_summary)`。

**Why this priority**: 是 P4（Reviewer 双轨：流程逻辑验证依赖"版本一致性"判断）和
P6（并发 artifact 乐观并发）的前置数据契约。HTML / 视频不参与版本化（落地复杂度大、收益小）。

**Independent Test**: 让 v2 demo task 在跑的过程中至少更新 2 次 `ReportCore`（一次首版、一次 reviewer
触发重做），读取 events.jsonl，校验 2 条 `artifact.update` 事件，第二条的 `base_version` 等于第一条的 `version`。

**Acceptance Scenarios**:

1. **Given** v2 任务首次写入 `MaterialPool`，**When** `material` agent 完成，**Then** 一条 `artifact.update` 事件
   带 `{id: "MaterialPool", version: 1, producer: "material", base_version: null, delta_summary: "..."}`。
2. **Given** `point-extractor` 修订 `ReportCore`，**When** 完成第二次写入，**Then** `version: 2`，
   `base_version: 1`，`delta_summary` 简述本次差异（≤60 字）。
3. **Given** 任何下游 agent 引用 artifact，**When** 它要看历史版本，**Then** 通过 `(id, version)`
   能从持久化层（文件路径约定）取到对应内容。

---

### Edge Cases

- **同一 message_id 重复出现**：系统 MUST 检测并拒写（防止 replay 时事件双发），写入失败时事件本身降级为
  `agent.failed` 但不阻塞任务。
- **artifact 写入时 base_version 不存在或非整数**：系统记录 warn，仍写入但 `base_version` 字段置 null
  （不破任务，由 P6 的乐观并发去处理冲突）。
- **v1 任务被错误地附加了 v2 字段**：系统 MUST 忽略 v2 字段、按 v1 行为运行（向后兼容兜底）。
- **events.jsonl 中混合 v1 / v2 行**：replay 工具 MUST 通过 `kind` 字段第一字符或 `msg_type` 区分，
  无法识别的行写入 `unknown.event` bucket 并继续。
- **`mentions` 列表包含不存在的 agent_id**：系统记录 warn，跳过该 mention，不阻塞 speak 事件发出。
- **`reply_to` 引用的 message_id 在本任务中找不到**：保留原值不校验（外部线程可能引用），下游 replay
  工具自行处理悬空引用。

---

## Requirements *(mandatory)*

### Functional Requirements

**双轨与回归保护**

- **FR-001**: 系统 MUST 在任务创建 API 接受可选 `harness_version` 字段，取值 `"v1"`（默认）或 `"v2"`。
- **FR-002**: 当 `harness_version="v1"` 或字段缺省时，系统 MUST 保持与 P1 改造前完全一致的运行时行为
  （事件流、产物、状态码、错误降级路径、SSE 推回内容均不变）。
- **FR-003**: 当 `harness_version="v2"` 时，系统 MUST 启用本次新增的事件协议与 artifact 版本化字段，
  其余基础设施（worker 调度、Coordinator 规则路由、agent prompt）保持现状（这些是 P2-P5 的事）。

**新事件协议**

- **FR-004**: 系统 MUST 支持发出 5 类新事件：`agent.speak` / `agent.silent` / `coordinator.intervene` /
  `reviewer.verdict` / `artifact.update`，schema 详见 `docs/开发文档.md` §3.5。
- **FR-005**: 每条新事件 MUST 携带稳定 `message_id`（系统生成，全任务内唯一，建议格式 `msg_<8 位>`）。
- **FR-006**: `agent.speak` / `agent.silent` MUST 支持 `reply_to: <message_id>` 字段（可空），用于线程引用。
- **FR-007**: `agent.speak` MUST 支持 `mentions: [agent-id]` 与 `cc: [agent-id]` 列表字段（可空数组）。
- **FR-008**: `agent.speak` MUST 支持 `intent` 字段，取值在 `{ask, propose, challenge, confirm, yield, done}`。
- **FR-009**: `agent.speak` MUST 支持 `artifact_updates: [{id, version, base_version, delta_summary}]` 数组字段。

**核心 artifact 版本化**

- **FR-010**: 当 artifact 是 `MaterialPool` / `ReportCore` / `Outline` / `Script` 之一时，写入 MUST 同时发出
  `artifact.update` 事件，带字段 `(id, version, producer, base_version, delta_summary, ref)`。
- **FR-011**: `version` MUST 是单调递增整数（每次写入 +1，从 1 开始）；首次写入 `base_version=null`。
- **FR-012**: `ref` MUST 指向该版本在 `data/outputs/<task_id>/` 下的具体文件路径，便于下游按 `(id, version)` 取内容。
- **FR-013**: HTML / 视频等其余 artifact 维持 latest-wins（不强制发 `artifact.update`，P1 不引入版本化）。

**持久化与回放**

- **FR-014**: `events.jsonl` MUST 同时承载 v1 与 v2 schema 行；新行直接 append，不破坏既有顺序约定。
- **FR-015**: 提供一个最小回放校验工具（脚本即可），输入 `events.jsonl` 路径，输出每类事件计数 + schema
  校验通过率。

**测试**

- **FR-016**: 提供单元测试覆盖 5 类新事件的 schema 校验（每类至少 1 个 happy path + 1 个 invalid case）。
- **FR-017**: 提供集成测试：v2 路径跑一个 mocked demo task，校验 5 类新事件至少各出现 1 条
  且 4 个核心 artifact 都至少有 1 次带版本号写入。

**用户可见层（宪章原则 I 红线）**

- **FR-018**: 任何用户可见层（chat 气泡 / 错误提示 / 导出文件 / API 错误码翻译）MUST NOT 出现
  `message_id` / `artifact version` / `harness_version` 这些技术标识；它们只允许进 `events.jsonl`
  / `data/.logs/` / admin 控制台。
- **FR-019**: SSE 推送给前端的 `chat.message` / `chat.message.update` 事件 MUST 与 v1 视觉一致
  （前端样式 / 字段不需要为 P1 变更）；v2 新事件先不暴露给前端，UI 改造留到 P7。

**降级（宪章原则 III）**

- **FR-020**: 任何 v2 协议层错误（schema 校验失败 / message_id 冲突 / artifact 写入异常）MUST NOT
  导致整任务 `failed`；记录到 `events.jsonl` 作为 `agent.failed` 或 warn，任务继续按现有降级路径走。

### Key Entities

- **Message**：群聊里的一条发言，由 agent 或 coordinator 产生。属性：`message_id`（任务内唯一）、
  `from`（发言者 agent_id）、`intent`（发言意图枚举）、`mentions` / `cc`（@ 名单）、`reply_to`
  （回复哪条消息）、`text`（人可读内容）、`artifact_updates`（顺带做了哪些 artifact 写入）。

- **Artifact**（核心 4 个）：`MaterialPool` / `ReportCore` / `Outline` / `Script`。属性：`id`（类型名）、
  `version`（单调整数）、`producer`（写入者 agent_id）、`base_version`（基于哪个版本写入）、
  `delta_summary`（≤60 字差异概述）、`ref`（持久化文件路径）、`payload`（实际数据，通过 ref 取）。

- **HarnessVersion**：task 级枚举，取值 `v1` / `v2`。决定本次任务运行时走旧串行管线还是新群聊协议。
  默认 `v1`。

- **Verdict**：reviewer 给出的双轨结论。属性：`verdict`（`pass` / `fail`）、`dimension`
  （`quality` / `process_logic` / `both`）、`findings`（问题列表）、`suggested_fix_agent`
  （建议哪个 agent 接手；为 null 表示 pass）、`suggestions`（≥3 条改进意见，pass 也给）。

- **Intervene**：coordinator 在群里发的纠偏话。属性：`kind`（`loop_detected` / `stagnation` /
  `drift` / `budget` / `gate_pass` / `gate_reject`）、`text`（业务化措辞的发言）、`hint_agent`
  （可选暗示某 agent 接话；agent 仍可不接）。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 现有用户在 v1 默认路径跑 5 个 demo task（覆盖 4 种报告类型 × 3 种时长 × 4 种风格之中
  任取 5 组合），所产生的 `events.jsonl`、`script.md`、`web-presentation/index.html`、`task.json`
  状态字段，与 P1 改造前的 baseline **逐字节相同**（视频/音频 binary 排除）。
- **SC-002**: v2 路径跑 1 个完整 demo task（开 `harness_version="v2"`），`events.jsonl` 中
  5 类新事件 `agent.speak` / `agent.silent` / `coordinator.intervene` / `reviewer.verdict` /
  `artifact.update` **至少各 1 条**（`agent.silent` 例外，允许 0 条 —— 若整任务无 agent 选择沉默）。
- **SC-003**: v2 路径同一 demo task 中，4 个核心 artifact（MaterialPool / ReportCore / Outline / Script）
  **每个至少 1 次** 带版本号写入，且最后一次 `version ≥ 1`。
- **SC-004**: 5 类新事件的 schema 校验单测 **通过率 100%**。
- **SC-005**: 现有 web-backend 单测套件（如有）+ 本次新增单测，整体 **0 regression**。
- **SC-006**: 跑一遍 `pnpm --filter web-frontend build` + `pnpm --filter admin-frontend build`
  全绿（P1 不动 UI，理论上必绿；作为防御性 check）。
- **SC-007**: 任意用户可见层（chat 气泡、API 错误提示、导出文件名）grep 不到
  `message_id` / `artifact_version` / `harness_version` 字面量，保证宪章原则 I 不被破坏。

---

## Assumptions

- **`harness_version` 字段在任务创建 API 中显式传**，没有自动迁移老任务，没有基于 user 的灰度路由
  （灰度策略由后续阶段或运营决定，P1 只埋开关）。
- **现有 `data/outputs/<task_id>/material_pool.json` / `report_core.json` 等文件路径约定保留**，
  v2 路径在文件名后追加 `_v<N>` 后缀（如 `material_pool_v1.json`、`material_pool_v2.json`），
  latest 仍保留无后缀文件以兼容 v1 读取。
- **5 类新事件 schema 的"骨架"在 P1 就位**，但 agent prompt 不改写（P5 才改），所以 v2 demo
  task 在 P1 阶段需要靠 mock 或最小手工触发来产生新事件（功能演示意义 > 真实业务流）。
- **events.jsonl 的行级 JSON 一行一事件格式保留**，不引入二进制 / 多行格式，replay 工具语义不变。
- **v1 / v2 共存期不设过期时间**，由 P5（prompt 全部改写完）作为完整切换里程碑。
- **不引入新的外部依赖**（如新的消息总线 / 状态存储）— 现有 EventBus + events.jsonl + JSON 文件
  落盘就够 P1 用了。
- **测试用例使用 `set_default_backend()` 注入 mock**（`agent_backend.py` 的现有 hook 点），
  不依赖真实 LLM 调用，使单测可重复可断言。

---

## Dependencies

- `docs/开发文档.md` §3.5（v2 事件 schema 定义）— P1 是这一节的代码落地。
- `docs/开发文档.md` §9.4.7（5 条架构定调）— P1 严格遵守，不超出"协议+状态"边界。
- `.specify/memory/constitution.md` 原则 I / III / IV / V — 改造不可破坏。
- `apps/web-backend/app/orchestrator/harness.py` 与 `pipeline.py` — P1 改造的主战场，但只扩不改。

## Out of Scope (P1 明确不做)

- Worker 订阅化 / decide-to-speak 闸门 → P2
- Coordinator 行为改造 / `_resolve_target` 拆解 / 必经步骤保护下沉 → P3
- Reviewer 即时质量门 / 流程逻辑校验扩展 → P4
- 8 个 step prompt 改写为 transcript-aware → P5
- EventBus fan-out / artifact 乐观并发冲突解决 / agent 真并行 → P6
- 前端 @ 高亮 / silent 气泡 / artifact diff / 用户 @ 进群 → P7
- Rolling summary / yes-man 防御 / 预算硬上限 → P8
