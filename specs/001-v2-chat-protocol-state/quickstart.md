# Quickstart — v2 群聊协议 + 状态模型层

> 适用对象：本仓库的后端 / harness / 测试开发者。读完后你能开 v2 跑 demo 任务、看新事件、跑单测。
>
> 前置：装好 [README](../../README.md) 里的依赖（pnpm + uv venv），有可用 `.env`。

---

## 1. 装上 v2 路径

P1 改动在分支 `001-v2-chat-protocol-state`。Pull 后无需重装依赖（不引入新外部依赖）。

```bash
git switch 001-v2-chat-protocol-state
uv pip install -e apps/web-backend     # 仅当 pyproject.toml 内 dev-dep 有更新时需要
```

`pyproject.toml` 新增 dev dependency：

```toml
[project.optional-dependencies]
test = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
]
```

---

## 2. 用 v2 路径创建一个任务

任务级 `harness_version` 字段决定走哪条路径（默认 `v1`）。

### REST 调用

```bash
curl -X POST http://localhost:8000/api/cluster/feat/report \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "ses_demo",
    "raw_text": "本周做了客户回访 18 个，活动页面初稿完成…",
    "title": "本周项目进度汇报",
    "report_type": "project_progress",
    "audience": "直属领导",
    "duration": "3分钟",
    "style": "简洁正式",
    "supplement": "",
    "harness_version": "v2"
  }'
```

不传 `harness_version` → 默认 v1，行为与改造前完全一致。

### 前端入口

前端 UI 不在 P1 范围。`apps/web-frontend/app/feat/report/page.tsx` 的表单不暴露此字段；
开发者要测 v2 用 curl / Postman 即可（FR-018/019 守住，前端不引入 v2 字段）。

---

## 3. 看新事件

任务跑完后，`data/outputs/<task_id>/events.jsonl` 同时承载 v1 + v2 schema 行。

```bash
# 数 5 类新事件
grep -c '"msg_type":"agent.speak"'            data/outputs/<task_id>/events.jsonl
grep -c '"msg_type":"agent.silent"'           data/outputs/<task_id>/events.jsonl
grep -c '"msg_type":"coordinator.intervene"'  data/outputs/<task_id>/events.jsonl
grep -c '"msg_type":"reviewer.verdict"'       data/outputs/<task_id>/events.jsonl
grep -c '"msg_type":"artifact.update"'        data/outputs/<task_id>/events.jsonl

# 或用回放校验工具（FR-015）
uv run python -m apps.web_backend.app.orchestrator.replay_check \
    data/outputs/<task_id>/events.jsonl
```

`replay_check` 输出形如：

```text
events.jsonl summary · <task_id>
  v1 events:   42
  v2 events:   18
    agent.speak           : 9
    agent.silent          : 2
    coordinator.intervene : 1
    reviewer.verdict      : 1
    artifact.update       : 5
  schema invalid:       0
```

---

## 4. 看版本化 artifact

```bash
ls data/outputs/<task_id>/
# 期望:
#   material_pool.json          ← latest（payload 与 v1 文件格式一致）
#   material_pool_v1.json       ← v1 版（含 __meta__ 块）
#   material_pool_v2.json       ← v2 版
#   report_core.json + report_core_v*.json
#   outline.json   + outline_v*.json
#   script.md      + script_v*.md
#   web-presentation/  video/   等其它 artifact 不参与版本化
```

读某一版：

```bash
jq '.__meta__' data/outputs/<task_id>/material_pool_v2.json
# {"id":"MaterialPool","version":2,"producer":"material","base_version":1,...}
```

`material_pool.json`（latest 副本）**不带 `__meta__`**，让 v1 reader 完全不感知。

---

## 5. 跑单测

```bash
cd apps/web-backend
uv run pytest tests/orchestrator/ -v
```

期望输出：

```text
tests/orchestrator/test_events_v2_schema.py ........        [ 40%]
tests/orchestrator/test_artifacts_v2.py     ......          [ 70%]
tests/orchestrator/test_v1_regression.py    ...             [ 85%]
tests/orchestrator/test_v2_integration.py   .               [100%]

20 passed in 8.31s
```

单测覆盖：

| 文件 | 覆盖什么 |
|---|---|
| `test_events_v2_schema.py` | 5 类事件 happy path + 1 个 invalid case 各 1 个 |
| `test_artifacts_v2.py` | 版本号递增 / base_version 链 / latest 文件同步 / `__meta__` 注入位置 |
| `test_v1_regression.py` | v1 路径事件流与 baseline 逐字段比对（金标准 fixture） |
| `test_v2_integration.py` | 用 `ScriptedBackend` mock 跑完整 v2 demo task，5 类事件都至少出现 1 条 |

---

## 6. 写新事件（开发者视角）

在 harness/pipeline 代码里要 emit v2 事件时：

```python
from app.orchestrator.events_v2 import AgentSpeak, ArtifactRef

await state.emit_v2(AgentSpeak(
    task_id=run.task_id,
    from_="point-extractor",
    text="刚把 ReportCore 更新了，重点条目从 5 条收敛到 3 条。",
    mentions=["copywriter"],          # @ 文书接力
    cc=["reviewer"],                  # 抄送质量检查员
    reply_to=None,                    # 主线发言
    intent="propose",
    artifact_updates=[ArtifactRef(
        id="ReportCore", version=2, base_version=1,
        delta_summary="收敛 5→3 重点条目，去除已经过审的常规事项"
    )],
))
```

`state.emit_v2()` 内部行为：

- 若 `state.is_v2` 为 False（即跑 v1 路径）→ **立即 return，零开销**
- 校验事件 schema（Pydantic 自动）
- 在 message_id 去重表里查 / 写
- append 一行到 `events.jsonl`（`event.model_dump(mode="json")`）
- 投递到 EventBus（`msg_type` 作为 dispatch key；P2 才有 worker 订阅）

写 artifact：

```python
from app.orchestrator.artifacts_v2 import write_versioned

new_version = await write_versioned(
    state=state,
    artifact_id="ReportCore",
    payload={"points": [...], "data_gaps": [...]},
    producer="point-extractor",
    base_version=1,                    # 基于 v1 修订
    delta_summary="收敛 5→3 重点条目",
)
# write_versioned 内部：
#   - 写 report_core_v2.json（含 __meta__）
#   - 复写 report_core.json（latest，去 __meta__）
#   - emit ArtifactUpdate(id=..., version=2, base_version=1, ref=...)
# 返回 new_version=2
```

---

## 7. 红线自检

提 PR 前过一遍：

- [ ] 任意用户可见层（chat 气泡、错误提示、SSE 推回前端、导出文件名）grep 不到 `message_id` / `artifact_version` / `harness_version` 字面量
- [ ] v1 demo task 跑出来的 events.jsonl 与 main baseline 逐行相同（用 `diff` 校验）
- [ ] v2 路径任何 schema 校验失败 / message_id 冲突 / artifact 写入异常都没让任务 `failed`（看 `task.json` status）
- [ ] 不动 agent prompt（grep `_build_<step>_prompt` 函数无改动）
- [ ] 不动 Coordinator 行为（grep `_resolve_target` / 必经步骤保护代码无改动）

---

## 8. 排错

| 现象 | 检查 |
|---|---|
| v2 事件没出现在 events.jsonl | 1. 任务创建时确实传了 `harness_version: "v2"` 吗？2. `state.is_v2` 是 True 吗？3. emit_v2 调用点是否被 if v1 短路了？ |
| schema 校验报错但任务没 failed | ✓ 正确行为（FR-020 降级）；查 events.jsonl 里 `agent.failed` 行的 `payload.error` 字段 |
| `material_pool.json`（latest）没有更新 | `write_versioned()` 写完版本文件之后 copy/overwrite latest 步骤是否抛了异常？查 `data/.logs/web-backend.log` |
| v1 任务跑出来的 events.jsonl 与 main 不一致 | **回归！** 立即审查 v1 路径上是否被加了 v2 only 的代码（如 emit_v2 未被 is_v2 短路） |

---

## 9. 后续阶段衔接

| 阶段 | 哪里需要这次产出 |
|---|---|
| **P2 worker 订阅化** | 订阅事件 kind 改为 `msg_type` 命名空间（`agent.speak`、`agent.silent`）；decide-to-speak 闸门 emit `AgentSilent` |
| **P3 Coordinator 转型** | 监听 `reviewer.verdict` 转写为 `coordinator.intervene(kind=gate_reject)` + 写 `mention` |
| **P4 Reviewer 双轨** | 订阅 `artifact.update`；emit `ReviewerVerdict`（schema 已就位） |
| **P5 prompt 重写** | agent prompt 改输出 `agent.speak` / `agent.silent` 二选一（schema 已就位） |
| **P6 并发** | `base_version` 字段 + `version` 单调递增已埋；做 retry / merge 时只读这两个字段即可 |
| **P7 UX** | 前端读 `events.jsonl` v2 行渲染 mentions 高亮、silent 灰显气泡、artifact diff |
