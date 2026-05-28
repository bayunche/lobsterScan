# Phase 1 — Data Model

**Feature**: v2 群聊协议 + 状态模型层（P1）

**Module**: `apps/web-backend/app/orchestrator/events_v2.py` & `artifacts_v2.py`

**Schema 库**: Pydantic v2（决策 1，详 [research.md](./research.md)）

---

## 总览

P1 引入 5 个新事件模型 + 1 个 artifact 模型 + 1 个版本枚举：

```text
HarnessVersion              # task 级枚举（v1 / v2）

V2EventBase                  # 抽象基类
├── AgentSpeak              # agent.speak
├── AgentSilent             # agent.silent
├── CoordinatorIntervene    # coordinator.intervene
├── ReviewerVerdict         # reviewer.verdict
└── ArtifactUpdate          # artifact.update

ArtifactMeta                 # 持久化 artifact 的元数据头（写入 __meta__ 块）
ArtifactRef                  # 事件载荷里的"指向某一版 artifact"引用
```

所有模型继承 Pydantic v2 `BaseModel`，配置：

```python
from pydantic import BaseModel, ConfigDict, Field

class V2Base(BaseModel):
    model_config = ConfigDict(
        extra="forbid",      # 禁止未声明字段（防 typo / 防协议漂移）
        frozen=True,         # 不可变，避免下游 mutate
        str_strip_whitespace=True,
    )
```

---

## 1. HarnessVersion（枚举 + 字段）

**位置**: `apps/web-backend/app/orchestrator/pipeline.py` 内 `TaskRun` 字段；也在 `events_v2.py` 暴露类型别名

**形态**:

```python
from typing import Literal
HarnessVersion = Literal["v1", "v2"]
```

**用于**：
- `TaskRun.harness_version: HarnessVersion = "v1"`（默认 v1）
- `HarnessState.is_v2: bool = False`（由 `run_harness(...)` 入口设置）
- API `POST /api/tasks` body 接受可选字段 `harness_version`

**校验**：
- API 层用 Pydantic Literal 自动校验取值
- 未传 / null / 空字符串 / 未知值 → 一律降级为 `"v1"`（防御性，FR-002）

---

## 2. V2EventBase（抽象基类）

```python
from datetime import datetime, timezone

class V2EventBase(V2Base):
    """所有 v2 事件的公共字段。"""
    msg_type: str                            # 由子类常量化（agent.speak / agent.silent / ...）
    message_id: str = Field(default_factory=new_message_id)
    task_id: str                             # 必填
    ts: float = Field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
```

**校验规则**：
- `msg_type` 必须匹配 5 个常量之一（子类 override 时用 Literal 锁死）
- `message_id` 必须匹配正则 `^msg_[a-f0-9]{8}$`（由 `is_message_id()` 校验）
- `task_id` 非空
- `ts` 单位是 Unix epoch 秒（float），与现有 v1 `AgentEvent.ts` 保持一致

---

## 3. AgentSpeak（agent.speak）

```python
from typing import Literal

Intent = Literal["ask", "propose", "challenge", "confirm", "yield", "done"]

class ArtifactRef(V2Base):
    id: Literal["MaterialPool", "ReportCore", "Outline", "Script"]
    version: int = Field(ge=1)
    base_version: int | None = Field(default=None, ge=1)
    delta_summary: str = Field(default="", max_length=60)

class AgentSpeak(V2EventBase):
    msg_type: Literal["agent.speak"] = "agent.speak"
    from_: str = Field(alias="from")         # agent_id；用 alias 避开 Python 关键字
    text: str = Field(min_length=1, max_length=4000)
    mentions: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    reply_to: str | None = None              # message_id 或 None
    intent: Intent
    artifact_updates: list[ArtifactRef] = Field(default_factory=list)
```

**字段说明**：

| 字段 | 必填 | 约束 | 用途 |
|---|---|---|---|
| `from` (alias `from_`) | ✓ | 非空字符串 | 发言者 agent_id（9 位之一） |
| `text` | ✓ | 1-4000 字符 | 群里实际说的话；前端渲染气泡 |
| `mentions` | ✗ | list[agent_id]，可空 | @ 谁请回应 |
| `cc` | ✗ | list[agent_id]，可空 | FYI 抄送 |
| `reply_to` | ✗ | message_id 或 null | 回复哪条消息（线程化） |
| `intent` | ✓ | 6 枚举之一 | 发言意图 |
| `artifact_updates` | ✗ | ArtifactRef 列表 | 这次发言顺带做了哪些 artifact 写入 |

**Edge cases** (spec Edge Cases 落地)：
- `mentions` 含未知 agent_id → 由 emit 包装函数 log warn 后**剔除**该 mention，仍发出 speak
- `reply_to` 引用不存在的 message_id → 保留原值不校验（外部线程可能引用），下游 replay 自行处理
- `from` ≠ 当前 agent → log warn 但仍写入（可能是 user 发声场景）

---

## 4. AgentSilent（agent.silent）

```python
class AgentSilent(V2EventBase):
    msg_type: Literal["agent.silent"] = "agent.silent"
    from_: str = Field(alias="from")
    reply_to: str | None = None
    reason: str = Field(min_length=1, max_length=30)
```

**用途**: 被 @ 但没东西可说，避免管线静默（前端可灰显「X 听完了，没补充」）。

**字段说明**：

| 字段 | 必填 | 约束 |
|---|---|---|
| `from` | ✓ | 非空 agent_id |
| `reply_to` | ✗ | 被哪条消息触发；为 null 表示自发沉默 |
| `reason` | ✓ | 1-30 字符；典型："已经被 X 说了"/"无新信息"/"需要 Y 先出 artifact" |

---

## 5. CoordinatorIntervene（coordinator.intervene）

```python
InterveneKind = Literal[
    "loop_detected", "stagnation", "drift", "budget", "gate_pass", "gate_reject"
]

class CoordinatorIntervene(V2EventBase):
    msg_type: Literal["coordinator.intervene"] = "coordinator.intervene"
    kind: InterveneKind
    text: str = Field(min_length=1, max_length=200)
    hint_agent: str | None = None
```

**字段说明**：

| 字段 | 必填 | 约束 |
|---|---|---|
| `kind` | ✓ | 6 枚举之一（loop / stagnation / drift / budget / gate_pass / gate_reject） |
| `text` | ✓ | 1-200 字符；群里发的纠偏话（业务化中文） |
| `hint_agent` | ✗ | 暗示某 agent 接话；agent 仍可不接（P3 才实装行为，P1 只埋字段） |

**红线遵守**：
- 严禁出现 next-speaker 命令式语义；`text` 用"建议"/"提醒"/"复诵目标"等口吻（P1 在 docstring 强调；P3 prompt 工程时落实）
- 「输出管控」决定 `task.end` 状态码是 P3 的事；P1 仅承载 schema

---

## 6. ReviewerVerdict（reviewer.verdict）

```python
Verdict = Literal["pass", "fail"]
VerdictDimension = Literal["quality", "process_logic", "both"]

class Finding(V2Base):
    severity: Literal["high", "med", "low"]
    what: str = Field(min_length=1, max_length=200)
    where: str = Field(default="", max_length=80)        # 例：'Script@v3' / 'Outline@v2.chapter[1]'

class ReviewerVerdict(V2EventBase):
    msg_type: Literal["reviewer.verdict"] = "reviewer.verdict"
    verdict: Verdict
    dimension: VerdictDimension
    findings: list[Finding] = Field(default_factory=list)
    suggested_fix_agent: str | None = None              # fail 时给修复建议；pass 时为 null
    suggestions: list[str] = Field(min_length=3)         # ≥3 条改进建议（pass 也给，用于 refine chips）
```

**字段说明**：

| 字段 | 必填 | 约束 |
|---|---|---|
| `verdict` | ✓ | pass / fail |
| `dimension` | ✓ | quality / process_logic / both |
| `findings` | ✗ | 问题列表（fail 时强烈建议非空，但 schema 不强制） |
| `suggested_fix_agent` | ✗ | agent_id；**Coordinator 转写时用，reviewer 自己不直接 @** |
| `suggestions` | ✓ | 至少 3 条改进建议（业务化中文，供 refine chips 复用） |

**红线遵守**（宪章原则 IV）：
- Reviewer 不直接 mention 别人 → schema 中**没有** `mentions` / `cc` 字段；只有 `suggested_fix_agent` 暗示
- `verdict=fail` 不直接产生路由副作用；P3 阶段由 Coordinator 监听到 fail 后转写为 `coordinator.intervene`

---

## 7. ArtifactUpdate（artifact.update）

```python
class ArtifactUpdate(V2EventBase):
    msg_type: Literal["artifact.update"] = "artifact.update"
    id: Literal["MaterialPool", "ReportCore", "Outline", "Script"]
    version: int = Field(ge=1)
    base_version: int | None = Field(default=None, ge=1)
    producer: str = Field(min_length=1)             # agent_id
    delta_summary: str = Field(default="", max_length=60)
    ref: str                                         # data/outputs/<task>/<name>_v<N>.<ext>
```

**用途**: artifact 写入后由 `artifacts_v2.write_versioned()` emit；订阅者（reviewer / replay 工具 / 未来的并发协调器）藉此感知变更。

**校验**：
- `version` ≥ 1；`base_version` 若非 null 必须 < `version`
- `ref` 必须是相对路径，前缀 `data/outputs/`
- HTML / 视频 artifact 不允许出现在 `id` 枚举里（P1 只 4 核心）

---

## 8. ArtifactMeta（持久化文件 `__meta__` 块）

```python
class ArtifactMeta(V2Base):
    id: Literal["MaterialPool", "ReportCore", "Outline", "Script"]
    version: int = Field(ge=1)
    producer: str
    base_version: int | None = None
    delta_summary: str = Field(default="", max_length=60)
    created_at: float                                # epoch seconds
    message_id: str                                  # 关联的 artifact.update 事件 id
```

**落盘形态**：

- JSON artifact（MaterialPool / ReportCore / Outline）—— 在文件顶层加 `__meta__` 键：
  ```json
  {
    "__meta__": {"id": "MaterialPool", "version": 2, "producer": "material", ...},
    "items": [...]
  }
  ```
- Markdown artifact（Script）—— 在文件最顶部插入 HTML 注释：
  ```markdown
  <!--__meta__: {"id":"Script","version":3,"producer":"copywriter","base_version":2,...}-->

  # 实际 Script 内容
  ```
- **latest 副本不带 `__meta__`**：保持与 v1 文件格式字节级一致（v1 reader 不需要知道版本号）

---

## 状态转移

### Artifact 版本号

```
首次写入 v1（base_version=null）
   ↓
某 agent 基于 v1 修订 → v2（base_version=1）
   ↓
另一 agent 基于 v2 修订 → v3（base_version=2）
   ↓
（P6 并发场景）agent 基于 v1 修订但 latest 已是 v2 → version=3, base_version=1
                                                  ← P1 不处理冲突，原样落
                                                    ↑
                                            **P1 仅记录，不 merge**
```

### Reviewer Verdict

```
artifact.update 事件被 emit
   ↓ (P4 才实装的订阅；P1 schema 就位即可)
reviewer 决定要不要跑即时质量门
   ↓
跑完 → reviewer.verdict 事件（pass 或 fail）
   ↓
   ├ pass → coordinator.intervene(kind=gate_pass) (P3)
   └ fail → coordinator.intervene(kind=gate_reject) + 转写 suggested_fix_agent 的 mention (P3)
```

### Coordinator Intervene

```
EventBus 出现 stagnation / drift / loop_detected / budget 阈值条件 (P3 实装)
   ↓
Coordinator 发 coordinator.intervene(kind=...)
   ↓
agent 自行决定要不要响应（不强制路由）
```

---

## 验证规则汇总

| 来源 | 字段 | 规则 |
|---|---|---|
| FR-005 | 所有 v2 事件 | `message_id` 全任务内唯一（emit 前 set 内去重） |
| FR-006 | AgentSpeak / AgentSilent | `reply_to` 字段存在即必须是合法 `message_id` 格式（但不校验是否存在引用对象） |
| FR-008 | AgentSpeak | `intent` 必须在 6 枚举内 |
| FR-009 | AgentSpeak | `artifact_updates[*].id` 必须在 4 核心 artifact 列表内 |
| FR-010 | artifact.update | `id` 必须在 4 核心列表；非 4 核心的 artifact 不发此事件 |
| FR-011 | artifact.update | `version` 单调递增（每次写入 +1，从 1 起） |
| FR-018 | 所有 v2 事件 | 经 SSE 推前端时必须 strip 掉 `message_id` 等技术字段（在 transport 层做） |

---

## 与 v1 数据模型的关系

| v1（保留） | v2（新增） | 兼容关系 |
|---|---|---|
| `AgentEvent`（dataclass） | `V2EventBase`（Pydantic）+ 5 子类 | 并存，不互相替代；写入同一个 events.jsonl，靠 `kind` vs `msg_type` 区分 |
| `StepState.output_json` | 仍是产物原 dict | 不变 |
| `data/outputs/<task>/material_pool.json` | 同名 + `material_pool_v<N>.json` | latest 始终是最新版的复制（payload 一致） |
| `TaskRun.steps` / `TaskRun.subscribers` | 不动 | v2 仅新增 `harness_version` 字段 |
| `chat.jsonl`（前端用） | 不动 | v2 事件先不暴露给前端（P7 才改） |

---

## 已知未决（留给 P2+）

- **mentions / cc 实际触发 worker 唤醒** — P1 只埋字段；P2 才让 worker 订阅这些 mention
- **artifact 乐观并发冲突解决** — P1 只允许 base_version 标注；P6 才做 retry / LLM merge
- **rolling summary** — P1 不压缩 transcript；P8 才上

这些都是 spec Out of Scope 明确列出的，data-model 已为未来阶段铺好字段。
