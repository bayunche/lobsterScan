# Phase 0 — Research & Technical Decisions

**Feature**: v2 群聊协议 + 状态模型层（P1）

**Branch**: `001-v2-chat-protocol-state` | **Date**: 2026-05-28

---

## 决策汇总

| # | 决策点 | 选择 | 关键理由 |
|---|---|---|---|
| 1 | Schema 校验库 | **Pydantic v2** | FastAPI 已用，零新依赖；运行时 + 单测 + 文档生成都拿一套 |
| 2 | `message_id` 格式 | **`msg_<8 位 hex>`**（`secrets.token_hex(4)`） | 8 字符短可读，不像 uuid4 那么吓人；冲突概率 < 4.3M 分之一对单任务足够 |
| 3 | v1/v2 分叉点 | **`pipeline.execute()` 入口 if/else** | YAGNI，不上策略模式；分叉只在 emit 与 artifact 写入两个钩子 |
| 4 | artifact 文件命名 | **`<name>_v<N>.json` + latest 副本 `<name>.json`** | v1 读取代码完全不动；版本目录方案要改 read 路径，工作量翻倍 |
| 5 | events.jsonl 承载 | **直接 dump dict，v1/v2 通过 `kind` vs `msg_type` 区分** | 不引入 envelope；v1 行 schema 完全不变 |
| 6 | 单测 mock LLM | **`set_default_backend()` + `tests/conftest.py` 共享 fixture** | 复用现有可注入 hook，无需新框架；fixture 范围可控 |

---

## 1. Schema 校验库选型

**Decision**: 用 **Pydantic v2**。

**Rationale**:
- FastAPI 全栈已经在用 Pydantic v2（`apps/web-backend/app/api/*.py` 与各 schema 文件）。零新依赖。
- Pydantic v2 的性能足够（compile-once schema，事件序列化路径 < 1ms 量级）。
- 自带 `.model_dump()` / `.model_validate()` / `.model_json_schema()`，能一套搞定运行时校验 + JSON Schema 导出 +
  契约文档生成（直接喂 contracts/ 目录）。
- 与 stdlib `dataclass`（现 `AgentEvent` / `TaskRun` 用的）可以并存：v1 用 dataclass 不动，v2 新模型走 Pydantic。

**Alternatives considered**:
- **jsonschema 纯 schema 文件**：要手写 dict 校验代码，单测样板量大，IDE 类型推断丢失。否。
- **stdlib dataclass + 手写校验**：保持与现有 `AgentEvent` 风格一致，但每条事件加 ~20 行
  校验代码，开发体验差；P2-P5 引入更多事件类型时维护成本爆炸。否。
- **attrs**：第三方，引入新依赖；与 Pydantic 重叠，无独有优势。否。

**实现要点**:
- 新事件模型放 `apps/web-backend/app/orchestrator/events_v2.py`
- 基础 `BaseModel` 用 `ConfigDict(extra="forbid", frozen=True)`：禁止未声明字段（防 typo），冻结防误改
- 5 类事件继承 `V2EventBase`，公共字段 `task_id` / `message_id` / `ts` / `msg_type`

---

## 2. message_id 生成方式

**Decision**: **`msg_<8 位小写 hex>`**，生成用 `secrets.token_hex(4)`。

**Rationale**:
- 8 字符短易读：`msg_a3f7c901` 比 `msg_550e8400-e29b-41d4-a716-446655440000` 友好得多。
- `secrets.token_hex(4)` = 4 字节 = 8 hex 字符 = 4,294,967,296 个值；单任务最多产生几百条事件，
  生日悖论冲突概率 ~10⁻⁵，可忽略。极端情况 FR-002 已要求重复 message_id 拒写并降级，不会破任务。
- 字母数字混排易于在日志里 grep / 拷贝；不像 uuid 那么"看上去就是机器 ID"，弱化技术感（虽然
  message_id 不暴露给用户，但 admin 端可读性也重要）。
- 不依赖 task 上下文（不需要原子计数器/锁）→ 在 async 环境下天然安全。

**Alternatives considered**:
- **uuid4**：太长（36 字符），admin 控制台日志冗长；除身份场景外没必要。否。
- **任务内自增整数**：需要全局锁或 task scoped counter，跨 worker 并发场景麻烦（P6 引入并发时埋雷）。否。
- **`msg_<时间戳>_<rand>` 混合**：增加复杂度，没收益。否。
- **更短 6 字符**：碰撞概率上升 256 倍，无必要节约 2 个字符。否。

**实现要点**:
- `apps/web-backend/app/orchestrator/ids.py` 暴露 `new_message_id() -> str` 与
  `is_message_id(s: str) -> bool`
- 每条 v2 事件入 `events.jsonl` 前由 `HarnessState` 注入 `message_id`（即便上层没传也兜底）
- Pydantic 模型用 `Field(default_factory=new_message_id)` 自动填

---

## 3. v1/v2 路径分叉点

**Decision**: **`pipeline.execute(run)` 入口判断 `run.harness_version`，分叉只影响两个钩子点**：
emit 包装 + artifact 写入包装。共享 worker 调度路径。

**Rationale**:
- v1 与 v2 在 P1 阶段**几乎完全共用同一条 worker / Coordinator 路径**（P3 才动 Coordinator，
  P2 才动 worker 订阅）。如果用策略模式分离两套 `execute` 函数，会复制 2000 行代码，回归风险翻倍。
- if/else 在两个位置实现：
  - **emit 包装**：`HarnessState.emit_v2(...)` 仅在 `v2` 时调用；v1 路径完全走原 `state.emit(...)`
    不动。
  - **artifact 写入包装**：在 4 个核心 artifact 落盘的钩子点（pipeline.py 现有 `_persist_*` 函数）
    判 `harness_version`，v2 走 `artifacts_v2.write_versioned(...)`，v1 走原逻辑。
- 入口处把 `harness_version` 写入 `HarnessState`，避免后续每处都从 `run` 取。

**Alternatives considered**:
- **策略模式（V1Executor / V2Executor 双子类）**：抽象成本远大于本期收益；过度设计。否。
- **每个钩子点局部 if**：分散，难审查"哪些行为是 v2 新加的"。集中到入口 + 两个包装函数更可控。否。
- **完全独立的 `execute_v2()`**：v1 改了之后 v2 容易漏改；高 drift 风险。否。

**实现要点**:
- `TaskRun` dataclass 新增字段：`harness_version: Literal["v1", "v2"] = "v1"`
- `create_task(..., harness_version: str = "v1")` 透传；API `POST /api/tasks` 接受可选 query/body 字段
- `pipeline.execute(run)` 第一行：`is_v2 = run.harness_version == "v2"`，向下传递
- `HarnessState.is_v2: bool` 字段 → `emit_v2()` 内部判 `if not self.is_v2: return`

---

## 4. artifact 版本化文件命名

**Decision**: `<name>_v<N>.json` 后缀方案 + 保留 latest 副本 `<name>.json`。

**Rationale**:
- v1 读取代码（如 `pipeline.py` 里所有读 `material_pool.json` 的地方）**完全不动** —— 它们继续读
  无后缀文件，就是 latest。
- 版本文件并列在同一目录：`data/outputs/<task_id>/material_pool_v1.json`、`material_pool_v2.json`、…
  + latest `material_pool.json`（每次写入时 copy/overwrite）。
- 历史版本完整保留，未来 P4 reviewer 流程逻辑校验、P6 乐观并发 merge 都能直接按 `(id, version)`
  读到任意版本。
- 目录方案（`v1/material_pool.json`、`v2/material_pool.json`）需要改所有 read 路径或加 symlink，
  在 Windows / WSL2 跨平台兼容性差，不推荐。

**Alternatives considered**:
- **版本目录 `v1/<artifact>.json`**：要求所有 reader 知道当前版本号或用 latest symlink；
  跨平台 symlink 支持有差异；改动面比后缀方案大 2-3 倍。否。
- **单文件 JSONL 追加版本**：读取必须扫全文件取 latest，性能与心智负担都差。否。
- **不保留 latest 副本，让 v1 代码改读最新 vN**：直接破坏向后兼容（违反 spec FR-002），否。

**实现要点**:
- `artifacts_v2.write_versioned(task_id, artifact_id, payload, producer, base_version)`：
  - 计算下一个 `version`（扫目录最大 vN + 1）
  - 写 `data/outputs/<task_id>/<artifact_id>_v<N>.json`（带 `__meta__` 块含 `version` / `producer` /
    `base_version` / `delta_summary` / `created_at`）
  - 同步覆写 `data/outputs/<task_id>/<artifact_id>.json`（latest 副本，**仅 payload，无 `__meta__`**，
    保持与 v1 文件格式一致）
  - emit `artifact.update` 事件
- artifact_id 与文件名映射常量：`{"MaterialPool": "material_pool", "ReportCore": "report_core",
  "Outline": "outline", "Script": "script"}`（注意 Script 在 v1 是 `script.md` 不是 json，
  特殊处理：versioned 文件叫 `script_v<N>.md`）

**取舍提示**: 若文件多到几百版（P8 之后），考虑目录归档；P1 不优化。

---

## 5. events.jsonl 怎么承载新 schema

**Decision**: **直接 dump dict，v1 行不变；v2 行通过 `msg_type` 字段标识**。

**Rationale**:
- v1 现有事件结构（`AgentEvent.to_dict()` 输出）有 `kind`、`agent_id`、`step_key`、`payload`、`ts` 字段。
  这些字段名延续，v1 行写入字节级不变。
- v2 新事件用 `msg_type`（命名空间风格：`agent.speak` / `agent.silent` / `coordinator.intervene` /
  `reviewer.verdict` / `artifact.update`）作为顶层字段。replay 工具读到行后：
  ```python
  if "msg_type" in row:
      # v2 path
  elif "kind" in row:
      # v1 path
  ```
- v1 的 `kind` 与 v2 的 `msg_type` 是不同字段名，互不污染。
- 即便未来 v2 路径里也想 emit 一些"v1 风格"事件（如 `agent.start` / `agent.done`），仍写 v1 schema，
  共存无歧义。

**Alternatives considered**:
- **统一 envelope `{schema: "v1|v2", payload: {...}}`**：破坏现有 v1 行，所有 replay 与持久化代码要改。否。
- **复用 `kind` 字段统一命名空间**：v1 已经有 `agent.handoff`、`agent.failed` 等 `agent.*` 命名，
  v2 加 `agent.speak`、`agent.silent` 会让 `kind` 字段语义模糊（既路由信号又群聊发言）。**强烈不推荐**。
- **分两个文件 `events_v1.jsonl` / `events_v2.jsonl`**：replay 工具要合并时间线，麻烦；调试时丢上下文。否。

**实现要点**:
- `harness.py` 新增 `class V2EventBase(BaseModel)` 带 `msg_type: str = Field(...)`、`message_id`、`ts`
- `HarnessState.emit_v2(event: V2EventBase)`：
  ```python
  if not self.is_v2: return
  row = event.model_dump(mode="json")
  self._append_jsonl(row)        # 同 v1 写入接口
  await self.bus.emit(...)        # 同 v1 总线（事件 kind 用 msg_type，让现有订阅者忽略）
  ```
- `replay_check.py`（CLI）：
  ```bash
  python -m apps.web_backend.app.orchestrator.replay_check data/outputs/<task_id>/events.jsonl
  # 输出: v1 events: N1 条 / v2 events: N2 条 / schema invalid: N3 条 / 5 类计数
  ```

---

## 6. 单测怎么 mock LLM

**Decision**: **用现有 `agent_backend.set_default_backend()` 注入 mock，外加 `tests/conftest.py` 提供
`mock_backend` fixture**。

**Rationale**:
- CLAUDE.md 已写明 `agent_backend.py` 提供 `AgentBackend` 抽象基类 + `get_default_backend()` /
  `set_default_backend()`，后者就是为「injecting a mock in tests」准备的（原话）。这是 P1 测试基础。
- pytest fixture 给 mock_backend 一个统一构造入口，便于不同测试声明"我希望第 N 步返回什么 JSON"。
- 不引入 `unittest.mock.patch` 的 monkey-patching 风格 —— 那会绕过 set_default_backend 的设计意图。
- pytest-asyncio 是必要的（pipeline.execute 是 `async`），加到 `apps/web-backend/pyproject.toml`：
  ```toml
  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  ```
  pytest-asyncio 是 pytest 生态标准件，不算"新依赖" — 它是 pytest 的官方插件。

**Alternatives considered**:
- **mock `subprocess.run` / `Popen`**：跳过整个 `OpenClawSubprocessBackend`，但要 mock 太底层的 IO，
  脆弱。否。
- **VCR-like 录回放**：现在没有这种基础设施，引入成本大；P1 没必要。否。
- **跑真 LLM 做集成**：不可重复，CI 跑不起来；P1 单测不依赖网络。否。

**实现要点**:
- `tests/conftest.py`:
  ```python
  import pytest
  from apps.web_backend.app.orchestrator.agent_backend import (
      AgentBackend, set_default_backend, get_default_backend
  )

  class ScriptedBackend(AgentBackend):
      """按声明顺序返回每个 step 的 JSON 输出。"""
      def __init__(self, scripts: list[dict]): self.scripts = scripts; self.i = 0
      async def run_one_turn(self, *, agent_id, prompt, model) -> dict:
          ret = self.scripts[self.i]; self.i += 1; return ret

  @pytest.fixture
  def mock_backend():
      original = get_default_backend()
      def _make(scripts): set_default_backend(ScriptedBackend(scripts)); return scripts
      yield _make
      set_default_backend(original)
  ```
- 4 个测试文件（`test_events_v2_schema.py` / `test_artifacts_v2.py` / `test_v1_regression.py` /
  `test_v2_integration.py`）独立使用此 fixture；测试间互不影响（teardown 还原）。
- `pytest` 跑命令：`uv run --directory apps/web-backend pytest tests/orchestrator/ -v`

---

## 派生发现（Derived Findings）

### F1 — `HarnessState` 当前没有"是否 v2"的信息

`harness.py` 的 `HarnessState` dataclass 字段不带任何版本标识。P1 需要在它的构造处加：

```python
@dataclass
class HarnessState:
    ...
    is_v2: bool = False  # 由 run_harness(...) 入口设置
```

### F2 — `AgentEvent.to_dict()` 输出格式向下兼容

现有 v1 事件结构（`{"kind": ..., "agent_id": ..., "step_key": ..., "payload": ..., "ts": ...}`）已是
"扁平 dict"，不需要任何包装就能继续写 events.jsonl。v2 事件直接用 Pydantic `.model_dump(mode="json")`
出来的字段会有 `msg_type` 在最顶层，与 v1 的 `kind` 字段并不冲突。

### F3 — `pipeline.execute()` 流程足够清晰，分叉点单一

`execute(run)` 在 `_coordinator_briefing()` 后调 `run_harness(...)`，所有 step 都从这里启动。
v1/v2 分叉只需要在 `execute()` 顶部读 flag → 透传给 `run_harness(is_v2=is_v2)` → `HarnessState`
持有该 flag → 后续所有 emit_v2 / artifacts_v2 的"是不是要发"全部 short-circuit 在 `is_v2=False` 时
立即 return。**单跳静默是 v1 路径零回归的核心保障**。

### F4 — `tests/` 当前是空的（CLAUDE.md 已确认）

需要新建：`apps/web-backend/tests/__init__.py`、`tests/conftest.py`、`tests/orchestrator/__init__.py`、
4 个测试文件，加上 `pyproject.toml` 增加 pytest + pytest-asyncio 的 dev dependency 与 ini config。
也建议在仓根 `package.json` 或 `scripts/` 加一个 `pnpm test:backend` 别名，方便 CI 接入（P8 之后）。

### F5 — Script artifact 不是 JSON

`Script` 在 v1 路径下落盘是 `script.md`，不是 JSON。`artifacts_v2.write_versioned()` 需要按 artifact_id
区分文件扩展名：JSON 类用 `.json`，Script 用 `.md`。对应映射：

```python
ARTIFACT_EXT = {
    "MaterialPool": ".json",
    "ReportCore":   ".json",
    "Outline":      ".json",
    "Script":       ".md",
}
```

`__meta__` 块对 Markdown 文件用 HTML 注释 `<!--__meta__: {...}-->` 头部嵌入，
对 JSON 文件用顶层 `__meta__` 键。**这样两种 latest 文件都保留原格式，v1 reader 无感**。

---

## 阶段产出

完成本研究后进入 Phase 1（数据模型 + 契约 + quickstart），见 [data-model.md](./data-model.md) 与
[contracts/](./contracts/)。

**所有 NEEDS CLARIFICATION 状态：✅ 0 项遗留。**
