---

description: "Task list — v2 群聊协议 + 状态模型层（P1）"
---

# Tasks: v2 群聊协议 + 状态模型层（P1）

**Input**: Design documents from `/specs/001-v2-chat-protocol-state/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: 单测 / 集成测在范围内 — spec FR-016 / FR-017 显式要求，SC-004 / SC-005 用通过率度量

**Organization**: 按 user story 分阶段；每个 story 完成后即可独立 demo / 验证。

**Branch**: `001-v2-chat-protocol-state`（已切，stacked 于 `chore/speckit-init-and-v2-roadmap`）

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3) — Setup / Foundational / Polish 不带 Story 标签
- Include exact file paths in descriptions
- **Acceptance** 写在每个 task 描述下方一行

## Path Conventions

`apps/web-backend/` 是本期所有改动的根；前端零改动。测试位于 `apps/web-backend/tests/`（本期新建）。

---

## Phase 1: Setup（Shared Infrastructure · 项目脚手架）

**Purpose**: 为后续所有 task 准备 testing 与 Python module 基础设施。

- [X] **T001** Scaffold test directory tree at `apps/web-backend/tests/`
  - 新建 `apps/web-backend/tests/__init__.py`、`apps/web-backend/tests/orchestrator/__init__.py`、`apps/web-backend/tests/fixtures/__init__.py`
  - **Acceptance**: `tree apps/web-backend/tests` 显示 3 个 `__init__.py`；`uv run --directory apps/web-backend python -c "import tests"` 不报错

- [X] **T002** [P] Add `pytest` + `pytest-asyncio` dev dependency to `apps/web-backend/pyproject.toml`
  - 在 `[project.optional-dependencies]` 下加 `test = ["pytest>=8", "pytest-asyncio>=0.23"]`
  - 加 `[tool.pytest.ini_options]` 配置：`asyncio_mode = "auto"`、`testpaths = ["tests"]`
  - **Acceptance**: `uv pip install -e "apps/web-backend[test]"` 成功；`uv run --directory apps/web-backend pytest --version` 输出 ≥ 8

- [X] **T003** [P] Create empty module placeholders so import paths exist
  - 新建 `apps/web-backend/app/orchestrator/events_v2.py`（占位 `"""v2 events. Filled by T010."""`）
  - 新建 `apps/web-backend/app/orchestrator/artifacts_v2.py`（占位）
  - 新建 `apps/web-backend/app/orchestrator/ids.py`（占位）
  - 新建 `apps/web-backend/app/orchestrator/replay_check.py`（占位 `if __name__ == "__main__": pass`）
  - **Acceptance**: `uv run --directory apps/web-backend python -c "from app.orchestrator import events_v2, artifacts_v2, ids, replay_check"` 不报错

---

## Phase 2: Foundational（Blocking Prerequisites · 必须先全部完成）

**Purpose**: 公共基础设施 —— `message_id` 生成、`HarnessState.is_v2` 字段、`TaskRun.harness_version` 字段、`emit_v2` 短路逻辑。任一 user story 都依赖这些。

**⚠️ CRITICAL**: T004-T009 必须全部 ✅ 后才能开 Phase 3。

- [X] **T004** Implement `message_id` generator + validator in `apps/web-backend/app/orchestrator/ids.py`
  - 暴露 `new_message_id() -> str`（用 `secrets.token_hex(4)` 拼 `msg_<8 位 hex>`）
  - 暴露 `is_message_id(s: str) -> bool`（正则 `^msg_[a-f0-9]{8}$`）
  - 暴露 `MessageIdRegistry` class（任务级去重表，提供 `add_or_reject(message_id) -> bool`）
  - **Acceptance**: 100 次调用 `new_message_id()` 都满足 `is_message_id(...)`；同一 task 内重复 `add_or_reject` 第二次返回 False

- [X] **T005** Add `harness_version` field to `TaskRun` and `create_task()` in `apps/web-backend/app/orchestrator/pipeline.py`
  - `TaskRun` dataclass 新增 `harness_version: str = "v1"`
  - `create_task(..., harness_version: str = "v1")` 透传；非 `"v1"` / `"v2"` 一律降级为 `"v1"`（FR-002 防御）
  - `TaskRun.to_dict()` 输出包含 `harness_version` 字段（admin 控制台需要看）
  - **Acceptance**: 现有 v1 单测 / 跑 task 行为不变；调用 `create_task(..., harness_version="v2")` 写出的 `task.json` 内含 `"harness_version": "v2"`

- [X] **T006** Extend API to accept `harness_version` in `apps/web-backend/app/api/tasks.py`（或对应 endpoint 模块）
  - `POST /api/cluster/feat/report` body 新增可选字段 `harness_version: Literal["v1", "v2"] | None = None`
  - 不传 / 非法值 → 透传给 `create_task(harness_version="v1")`
  - **Acceptance**: `curl ... -d '{...,"harness_version":"v2"}'` 创建的任务 `task.json` 含 `"harness_version": "v2"`；不传字段时默认 v1

- [X] **T007** Add `is_v2: bool = False` to `HarnessState` in `apps/web-backend/app/orchestrator/harness.py`
  - `HarnessState` dataclass 新增 `is_v2: bool = False`
  - `HarnessState` 新增 `message_id_registry: MessageIdRegistry = field(default_factory=MessageIdRegistry)`
  - `run_harness(..., is_v2: bool = False)` 参数 + 透传到 `HarnessState` 构造
  - **Acceptance**: v1 路径 `is_v2=False` 时 `HarnessState` 行为与现状一致；类型注解通过 `mypy --strict` 在 harness.py（如未配 mypy 则忽略）

- [X] **T008** Wire v1/v2 branching at `pipeline.execute()` entry in `apps/web-backend/app/orchestrator/pipeline.py`
  - `execute(run)` 开头：`is_v2 = run.harness_version == "v2"`
  - 调用 `run_harness(..., is_v2=is_v2)` 透传
  - **Acceptance**: 默认 task 仍是 v1 行为；`harness_version="v2"` 的 task 在 events.jsonl 第 1 行 `task.start` 事件之后能看到 `HarnessState.is_v2=True` 在内部生效（用 log debug 验证或后续单测）

- [X] **T009** Implement `HarnessState.emit_v2()` short-circuit helper in `apps/web-backend/app/orchestrator/harness.py`
  - 方法签名：`async def emit_v2(self, event: V2EventBase) -> None`
  - 第一行：`if not self.is_v2: return`（v1 零开销）
  - 内部：`message_id_registry.add_or_reject(event.message_id)` → False 则 log warn 并 emit `agent.failed`（schema 校验失败降级，FR-020）
  - 序列化：`row = event.model_dump(mode="json")` → append 到 events.jsonl（复用现有 `_append_jsonl` 路径，必要时新建）
  - 投递到 EventBus：`await self.bus.emit(AgentEvent(kind=event.msg_type, ...))`（让现有 wildcard handlers 能看到）
  - **Acceptance**: 单测构造一个 fake `V2EventBase` 调 `emit_v2`，is_v2=False 时不产生任何 IO；is_v2=True 时 events.jsonl 多一行带 `msg_type` 字段；重复 message_id 第二次只写一条 `agent.failed`

**Checkpoint**: T004-T009 全部 ✅ 后，Phase 3-5 可并行启动（US1 / US2 / US3 互相不依赖）。

---

## Phase 3: User Story 1 - 现有 v1 用户感受零变化（Priority: P1）🎯 MVP

**Goal**: 守住宪章原则 III 与 spec FR-002 —— v1 路径行为字节级不变。

**Independent Test**: 在 main 分支跑一个 demo task（pnpm dev + 预置素材），在 P1 分支不传 `harness_version` 跑同一个 demo task；比对两边的 `events.jsonl` 顺序与字段一致、`script.md` 一致、`task.json` 状态一致。

### Tests for User Story 1

- [X] **T010** [P] [US1] Create v1 golden baseline fixture at `apps/web-backend/tests/fixtures/v1_baseline/`
  - 跑 1 个固定 demo task（report_type=project_progress / duration=3分钟 / 简洁正式 / mock material）一遍主干代码
  - 把结果 `events.jsonl` + `script.md` + `task.json` 复制到 `tests/fixtures/v1_baseline/`
  - 文件应**完全可重放**：mock 材料 + mock backend script 都固定，无随机性
  - **Acceptance**: 同一台机器同一 commit 重跑两次，fixture 内 3 文件逐字节相同（用 `sha256sum` 校验）

- [X] **T011** [US1] Write `tests/conftest.py` with `ScriptedBackend` fixture
  - 实现 `ScriptedBackend(AgentBackend)`：按声明的 step 顺序返回固定 JSON 输出（不调 LLM）
  - 提供 `mock_backend` pytest fixture：teardown 时通过 `set_default_backend(original)` 还原
  - 提供 `tmp_outputs_dir` fixture：每个 test 一个独立 `data/outputs/<test_task_id>/` 临时目录
  - 提供 `read_events_jsonl(path) -> list[dict]` 辅助函数
  - **Acceptance**: `pytest tests/conftest.py -v --collect-only` 能列出 3 个 fixture；空测试用例使用 fixture 不报错

- [X] **T012** [US1] Write `apps/web-backend/tests/orchestrator/test_v1_regression.py`
  - Test 1：用 baseline 的同一 script 跑 v1 路径，比对生成的 `events.jsonl` 与 baseline **逐行相同**（按 ts 之外字段比对，ts 浮点宽容）
  - Test 2：比对 `script.md` 内容相同
  - Test 3：比对 `task.json` 关键字段（status / steps[].status / steps[].output_json keys）相同
  - Test 4：跑 v1 路径时，events.jsonl 中**不出现** `msg_type` 字段（即没有 v2 事件泄漏到 v1）
  - **Acceptance**: 4 个 test 全 pass；用 `pytest tests/orchestrator/test_v1_regression.py -v` 输出 `4 passed`

### Implementation for User Story 1（实现 / 护栏）

- [X] **T013** [US1] Add `assert not state.is_v2` guard inside any new emit_v2 call site in `apps/web-backend/app/orchestrator/pipeline.py`
  - 这是占位的红线：P1 阶段任何 `emit_v2(...)` 调用之前必须先 `if state.is_v2:` 包裹；reviewer 在 PR 时按此规则审查
  - 文档说明：在 `pipeline.py` 顶部 docstring 加 v1/v2 分叉约定描述
  - **Acceptance**: grep `emit_v2(` 找到的每一处调用，前面 5 行内必有 `if .*is_v2` 或 `if .*== "v2"`；CI / reviewer 把关

- [X] **T014** [US1] Smoke test: run 1 v1 task end-to-end via `pnpm dev` + mock backend
  - 启动 web-backend（注入 ScriptedBackend）
  - 调 `POST /api/cluster/feat/report` 不传 `harness_version`
  - 等 SSE `task.done` 事件
  - 验证 `data/outputs/<task>/{events.jsonl, script.md, task.json}` 与 baseline 比对相同
  - **Acceptance**: 手动跑通；输出步骤记录到 PR 描述（这是端到端的 manual smoke，不写成 CI test）

**Checkpoint**: At this point, **User Story 1 is fully shipped** —— v1 路径有了自动化回归保护网。MVP 达成（保护现有用户的零变化）。

---

## Phase 4: User Story 2 - v2 路径能落出 5 类新事件（Priority: P2）

**Goal**: 5 类新事件 schema 落地 + 在 v2 path 能被 emit + replay 工具能消费。

**Independent Test**: 跑一个 `harness_version="v2"` demo task，校验 `events.jsonl` 出现 5 类新事件至少各 1 条；`replay_check.py` 输出 schema invalid=0。

### Tests for User Story 2（先写 schema 测试）

- [X] **T015** [P] [US2] Write `apps/web-backend/tests/orchestrator/test_events_v2_schema.py`
  - 5 类事件每类至少 1 个 happy path + 1 个 invalid case：
    - `agent.speak`: happy（含 mentions + cc + reply_to + intent + artifact_updates）；invalid（intent 不在枚举内）
    - `agent.silent`: happy；invalid（reason 超 30 字）
    - `coordinator.intervene`: happy（含 hint_agent）；invalid（kind 不在枚举内）
    - `reviewer.verdict`: happy（含 3 条 suggestions + 2 条 findings）；invalid（suggestions < 3 条）
    - `artifact.update`: happy（含 base_version）；invalid（ref 路径不匹配正则）
  - 用 Pydantic 自带 `ValidationError` 断言 invalid case 抛错
  - **Acceptance**: `pytest tests/orchestrator/test_events_v2_schema.py -v` 输出 `10 passed`；每个 invalid case 抛 `ValidationError`

### Implementation for User Story 2

- [X] **T016** [P] [US2] Implement all 5 Pydantic models in `apps/web-backend/app/orchestrator/events_v2.py`
  - `V2EventBase` 抽象基类（`msg_type` / `message_id` / `task_id` / `ts`）
  - `AgentSpeak`、`AgentSilent`、`CoordinatorIntervene`、`ReviewerVerdict`、`ArtifactUpdate`
  - `ArtifactRef` / `Finding` 嵌套模型
  - `Intent` / `InterveneKind` / `Verdict` / `VerdictDimension` 类型别名（Literal）
  - 严格按 `data-model.md` + `contracts/*.schema.json` 字段对齐
  - `from_` 字段用 `Field(alias="from")` 避开 Python 关键字
  - **Acceptance**: T015 测试全部 pass；模型生成的 JSON Schema（`AgentSpeak.model_json_schema()`）与 `contracts/agent.speak.schema.json` 字段集合一致

- [X] **T017** [US2] Implement minimal `emit_v2` test integration in `apps/web-backend/app/orchestrator/pipeline.py`
  - 在 `execute(run)` 内，若 `is_v2`：在 coordinator 开场白之后 emit 1 条 `CoordinatorIntervene(kind="gate_pass", text="任务受理...", ...)`（占位发声，证明协议跑通）
  - 在最终 status 落地前：emit 1 条 `ReviewerVerdict(verdict="pass", dimension="quality", suggestions=[...3 条...])`（占位）
  - 在每个 step 完成后：若 step.output_json 含某 v2 字段（暂不约束 prompt 真改）→ emit `AgentSpeak`；否则不 emit（P5 才让 prompt 真改输出）
  - 注意：这些 emit 都用 `if state.is_v2:` 包裹（守红线，参照 T013）
  - **Acceptance**: v2 demo task 跑完后，`events.jsonl` 至少出现 `coordinator.intervene` 1 条 + `reviewer.verdict` 1 条；v1 路径绝不出现这些（由 T012 守护）

- [X] **T018** [US2] Implement `apps/web-backend/app/orchestrator/replay_check.py` CLI
  - 入参：events.jsonl 路径
  - 行为：
    - 逐行解析；行有 `msg_type` → v2 events 计数；有 `kind` → v1 events 计数
    - 对 v2 行用 Pydantic models 校验 schema；失败计数到 `schema invalid`
    - 输出每类 v2 事件计数（5 类）
  - 跑方式：`uv run python -m app.orchestrator.replay_check <path>`
  - **Acceptance**: 喂入 v1 baseline events.jsonl 输出 `v1 events: N, v2 events: 0`；喂入 v2 demo events.jsonl 输出 5 类计数 + `schema invalid: 0`

- [X] **T019** [US2] Write `apps/web-backend/tests/orchestrator/test_v2_integration.py` (events part)
  - 用 ScriptedBackend mock 跑一个 v2 task
  - 断言 events.jsonl 中至少出现 `coordinator.intervene` ≥ 1、`reviewer.verdict` ≥ 1（`agent.speak/silent` 暂不强制，因 prompt 未改写）
  - 断言每条 v2 事件 `message_id` 全任务唯一
  - 断言 `replay_check.py` 跑同一文件输出 `schema invalid: 0`
  - **Acceptance**: `pytest tests/orchestrator/test_v2_integration.py::test_events_emitted -v` 输出 `1 passed`

**Checkpoint**: User Story 2 完成 —— v2 协议层"骨架"全就位，能被 schema 校验、能落入 events.jsonl、能被 replay 工具消费。

---

## Phase 5: User Story 3 - 4 个核心 artifact 支持版本化引用（Priority: P2）

**Goal**: `write_versioned()` 实现 + 文件命名约定 + `__meta__` 注入 + emit `ArtifactUpdate`。

**Independent Test**: v2 demo task 跑完后，`data/outputs/<task>/` 内 4 核心 artifact 每个有 ≥ 1 个 `_v<N>` 版本文件 + 1 个无后缀 latest 副本；版本文件含 `__meta__`，latest 副本不含。

### Tests for User Story 3

- [X] **T020** [P] [US3] Write `apps/web-backend/tests/orchestrator/test_artifacts_v2.py`
  - Test A：首次写 MaterialPool → version=1 / base_version=null；`material_pool_v1.json` + `material_pool.json` 都存在；前者含 `__meta__`，后者不含
  - Test B：再写一次基于 v1 的 → version=2 / base_version=1；`material_pool_v2.json` 出现；`material_pool.json` 内容更新为 v2 的 payload
  - Test C：写 Script（Markdown）→ `script_v1.md` 顶部含 `<!--__meta__: {...}-->` 注释；`script.md` 不含
  - Test D：emit `ArtifactUpdate` 事件 schema 通过校验，`ref` 指向带 v 后缀的文件
  - Test E：试图写 HTML / 视频等非 4 核心 artifact → `write_versioned` 抛 ValueError（或调用方根本不该 call）
  - **Acceptance**: `pytest tests/orchestrator/test_artifacts_v2.py -v` 输出 `5 passed`

### Implementation for User Story 3

- [X] **T021** [P] [US3] Implement `write_versioned()` in `apps/web-backend/app/orchestrator/artifacts_v2.py`
  - 签名：`async def write_versioned(*, state: HarnessState, artifact_id: str, payload: dict | str, producer: str, base_version: int | None = None, delta_summary: str = "") -> int`（返回 new version）
  - 步骤：
    1. 验证 `artifact_id` 在 4 核心列表内（否则 raise ValueError）
    2. 扫描 `data/outputs/<task_id>/` 找现有最大 vN → new_version = max + 1
    3. 构造 `ArtifactMeta`（含 message_id、created_at 等）
    4. 写带版本号文件：
       - JSON 类（MaterialPool/ReportCore/Outline）：payload 顶层加 `__meta__` 键 → dump 到 `<name>_v<N>.json`
       - MD 类（Script）：在 payload 最顶部插入 `<!--__meta__: {...}-->` 注释 → 写到 `<name>_v<N>.md`
    5. 复写 latest 副本（**不带 `__meta__`**）：JSON 直接 dump payload；MD 直接写 payload
    6. emit `ArtifactUpdate(id=..., version=new_version, base_version=base_version, ...)` 通过 `state.emit_v2(...)`
  - 提供常量 `CORE_ARTIFACTS: set[str] = {"MaterialPool", "ReportCore", "Outline", "Script"}`
  - 提供常量 `ARTIFACT_EXT: dict[str, str] = {"MaterialPool": "json", "ReportCore": "json", "Outline": "json", "Script": "md"}`
  - 提供常量 `ARTIFACT_FILENAME: dict[str, str] = {"MaterialPool": "material_pool", "ReportCore": "report_core", "Outline": "outline", "Script": "script"}`
  - **Acceptance**: T020 测试全部 pass

- [X] **T022** [US3] Wire `write_versioned()` into pipeline at 4 artifact write sites in `apps/web-backend/app/orchestrator/pipeline.py`
  - 找 v1 现有写 4 核心 artifact 的位置（grep `material_pool.json` / `report_core.json` / `outline.json` / `script.md`）
  - 在每处 `if state.is_v2:` 调 `write_versioned(...)`；v1 路径**保留原写法不动**（latest 文件由 v1 写）
  - v2 路径下，原 v1 写法的代码也必须保留 —— 因为 `write_versioned` 会同步覆写 latest 副本，但 v1 reader 还在用原 latest，两条写入路径需协调（v2 时只走 `write_versioned`，由它写 latest）
  - **Acceptance**: v2 demo task 跑完，4 核心 artifact 每个都有 `_v<N>` 文件 + latest 副本；v1 demo task 跑完，文件结构与 main 完全一致（无 `_v<N>` 文件）

- [X] **T023** [US3] Extend `test_v2_integration.py` with artifact assertions（in same file as T019）
  - 断言 v2 demo task 跑完后，4 核心 artifact 每个 ≥ 1 个 `_v<N>` 文件
  - 断言 `events.jsonl` 内 `artifact.update` 事件 ≥ 4 条（4 个 artifact 各至少 1 条）
  - 断言每条 `artifact.update.ref` 字段指向真实存在的文件
  - **Acceptance**: `pytest tests/orchestrator/test_v2_integration.py::test_artifacts_versioned -v` 输出 `1 passed`

**Checkpoint**: User Story 3 完成 —— 4 核心 artifact 进入版本化阶段，P4/P6 可以引用 `(id, version)` 取任意版。

---

## Phase 6: Polish & Cross-Cutting（最终验证 + 文档 + 红线扫描）

**Purpose**: 集成验证 + 文档对齐 + 红线自动扫描 + 构建烟测。

- [X] **T024** [P] Run full test suite + collect coverage
  - 跑 `uv run --directory apps/web-backend pytest tests/ -v`
  - 期望：全绿（每个 test 文件的所有 case 都 passed）
  - **Acceptance**: pytest 输出 `<N> passed in <T>s`，N ≥ 20（按上面拆分加总）

- [X] **T025** [P] Smoke build both frontends to ensure no UI drift
  - `cd apps/web-frontend && ./node_modules/.bin/next build`
  - `cd apps/admin-frontend && ./node_modules/.bin/next build`
  - **Acceptance**: 两边都 ✓ Compiled successfully，路由数与 main 上次的相同（web 6/6、admin 18/18）

- [X] **T026** [P] Red-line scan: 确保用户可见层不暴露技术标识符（宪章原则 I）
  - grep `message_id` / `artifact_version` / `harness_version` 在以下位置不应出现：
    - `apps/web-frontend/app/**/*.tsx`
    - `apps/web-backend/app/api/**/*.py` 的 response body / error message 字面量
    - SSE event payload（如 `chat.message`）
  - 写一个 `tests/orchestrator/test_redaction.py` 自动跑此扫描
  - **Acceptance**: grep 命中 0 处；test_redaction.py 通过

- [X] **T027** Run v1 regression smoke (manual)
  - 启动 main 分支 + 跑 demo task → 保存 `events.jsonl` / `script.md` 作 baseline
  - 切回本分支 + 跑同 demo task（不传 harness_version）
  - `diff` 两个 events.jsonl（容忍 ts 浮点差异）；`diff` 两个 script.md
  - **Acceptance**: diff 为空（或仅 ts 字段差异）；过程截图记到 PR 描述

- [X] **T028** Update `docs/开发文档.md` to mark P1 as IMPLEMENTED
  - 在 §9.4.5 路线图 P1 行末尾加 `(✅ 已落地 commit <SHA>)`
  - 在 §3.5 / §4.1 / §4.9 顶部加链接：「实现位置：`apps/web-backend/app/orchestrator/events_v2.py` / `artifacts_v2.py`」
  - **Acceptance**: doc grep `P1.*已落地` 命中 1 处

- [X] **T029** Sync `specs/001-v2-chat-protocol-state/quickstart.md` with actual implementation
  - 跑通完整 quickstart 流程一次，校对每个命令 / 文件路径 / 输出格式与实现一致
  - 修补任何 drift（如真实 `replay_check` 输出与 quickstart 示例不一致）
  - **Acceptance**: 按 quickstart 逐步执行能跑通；任何 drift 都已修补

- [X] **T030** Update `CLAUDE.md` SPECKIT block to point at completed P1（保留链接，状态改 Implemented）
  - 在 P1 plan link 行末标注 `(✅ Implemented)`
  - **Acceptance**: CLAUDE.md SPECKIT 块仍含 plan/spec 链接，多一个 Implemented 状态标识

---

## Dependencies & Order

### Phase 依赖图

```
Phase 1 Setup (T001-T003)
   │
   ▼
Phase 2 Foundational (T004 → T005 → T006 → T007 → T008 → T009)
   │           ↑↑↑ 严格串行：每个 task 写完跑通现有测试再下一个
   ▼
Phase 3 US1 (T010 → T011 → T012 → T013 → T014) ─┐
Phase 4 US2 (T015 → T016 → T017 → T018 → T019)  ├── 可并行（不同文件）
Phase 5 US3 (T020 → T021 → T022 → T023) ────────┘
   │
   ▼
Phase 6 Polish (T024-T030)
```

### Task 间细粒度依赖（关键路径）

- T002 → T001 之后任何引 pytest 的 task
- T003 → 后续任何 import `events_v2` / `artifacts_v2` / `ids` / `replay_check` 的 task
- T004 → T009（emit_v2 用 MessageIdRegistry）→ T017（emit_v2 调用）→ T021（write_versioned 调用 emit_v2）
- T005 → T006（API 透传依赖字段存在）→ T008（execute 读取字段）
- T007 → T009（emit_v2 是 HarnessState 方法）
- T010 → T012（test_v1_regression 依赖 baseline fixture）
- T011 → T012 / T015 / T019 / T020 / T023（所有 test 用 conftest 的 fixture）
- T016 → T015（schema test 调 Pydantic 模型）→ T017（pipeline emit_v2 用模型）→ T018（replay 用模型校验）
- T021 → T020（test 调 write_versioned）→ T022（pipeline 调 write_versioned）
- T024 / T025 / T026 → 所有 Phase 3-5 完成后
- T027 → T012 / T022 完成后（手动 smoke）
- T028-T030 → 所有代码 task 完成后

### 并行机会（同 phase 内）

| Phase | 可并行 | 不能并行 |
|---|---|---|
| Phase 1 | T002 / T003 [P] | T001 必须先做（建目录） |
| Phase 2 | （无） | T004-T009 严格串行 |
| Phase 3 | T010 [P]（不依赖代码） | T011 → T012 → T013 → T014 串行 |
| Phase 4 | T015 / T016 [P]（不同文件） | T017 → T018 → T019 串行 |
| Phase 5 | T020 / T021 [P]（不同文件） | T022 → T023 串行 |
| Phase 6 | T024 / T025 / T026 [P] | T027 / T028 / T029 / T030 顺序无关，但 T028 / T030 写后必须重 build |

**Story-level 并行**：Phase 3 / 4 / 5 之间互不依赖（Foundational 完成后），多人协作可同时开工 US1 + US2 + US3。

---

## Implementation Strategy

### MVP First（Phase 1-3）

最小可发布版 = **Setup + Foundational + US1**。完成 T001-T014 即达成 MVP：
- 项目脚手架就位
- v1/v2 字段与 emit_v2 短路逻辑就位（v2 path 还没被任何代码 emit 任何事件，但开关已经在）
- **v1 路径有了自动化回归保护网**（最重要！spec 的 P1 优先级 story）

此时即可开 PR、合 main，给团队 demo："v1 完全没变，v2 开关已埋"。

### Incremental Delivery（Phase 4-5）

MVP 合并后，US2 与 US3 可并行启动：
- US2 先合（事件协议是 US3 的依赖 —— `ArtifactUpdate` 也是 v2 事件之一）
- US3 紧随其后（用 US2 的 events_v2.ArtifactUpdate 模型）

### Final Polish（Phase 6）

所有代码合完后跑 T024-T030 一次性验证，准备进 P2 阶段。

### 建议 commit 粒度

每个 Tnnn 一个 commit。commit message 格式：

```
[P1.<phase>] <task subject>

<short description>

T<id>; spec: §<related FR>; closes part of spec story <US>
```

例：

```
[P1.foundational] message_id 生成器 + 任务级去重表

T004; spec: FR-005; closes part of foundational T004
```

---

## Validation Checklist（Format & Coverage）

- [x] **All tasks follow checklist format**: `- [ ] T<id> [P?] [Story?] <desc with file path>`
- [x] **All user stories have tasks**: US1 → T010-T014；US2 → T015-T019；US3 → T020-T023
- [x] **Each user story has Independent Test**: 写在 Phase 3/4/5 顶部
- [x] **Each story has Acceptance per task**: 每个 task 描述下一行
- [x] **Dependencies documented**: 上方 Phase 依赖图 + 细粒度依赖列表 + 并行机会表
- [x] **MVP scope defined**: T001-T014（Setup + Foundational + US1）
- [x] **Polish cross-cutting last**: Phase 6 在 Phase 3-5 后
- [x] **Constitution principles guarded**: T013 红线 / T026 自动扫描 / T012 v1 回归 / T020 降级

---

**Total tasks**: 30
- Phase 1 Setup: 3 (T001-T003)
- Phase 2 Foundational: 6 (T004-T009)
- Phase 3 US1: 5 (T010-T014)
- Phase 4 US2: 5 (T015-T019)
- Phase 5 US3: 4 (T020-T023)
- Phase 6 Polish: 7 (T024-T030)
