# Quickstart — Worker 订阅化 + decide-to-speak 闸门（P2）

> 适用对象：本仓库后端 / harness 开发者。读完后你能给新 agent 配 interests，单测覆盖订阅流程，
> 跑通 v2 demo 看到 mention → 自动响应链路。
>
> 前置：装好依赖（pnpm install + uv venv），有可用 `.env`，main 已包含 P1 实现。

---

## 1. 给一个 agent 配 interests 与 requires

`subscription.py` 顶部的 `WORKER_PROFILE` 是 9 个 agent 的静态注册表。要新增 / 调整一个 agent
的订阅行为，编辑这个表即可（无需改 harness.py / pipeline.py）：

```python
# subscription.py
WORKER_PROFILE: dict[str, WorkerProfile] = {
    # ... 已有
    "html-designer": WorkerProfile(
        interests=(
            mention_includes("html-designer"),    # 任何 agent.speak.mentions 含 "html-designer"
            artifact_id_in({"Script"}),           # 任何 Script artifact 写入
            # 想加新的订阅源 → 在这里加 Predicate 即可
        ),
        requires=("Script",),                     # html-designer 跑活需要 Script 就绪
    ),
}
```

3 个预制 Predicate helper（subscription.py 暴露）：

| Helper | 触发条件 |
|---|---|
| `mention_includes(self_id)` | 收到 AgentSpeak 且 mentions 列表含 self_id |
| `hint_agent_is(self_id)` | 收到 CoordinatorIntervene 且 hint_agent == self_id |
| `artifact_id_in(ids)` | 收到 ArtifactUpdate 且 id ∈ ids |

需要更复杂谓词？直接写函数：

```python
def my_custom_pred(self_id: str) -> Predicate:
    def _p(event: V2EventBase, _: str) -> bool:
        return isinstance(event, AgentSpeak) and event.intent == "challenge"
    return _p
```

---

## 2. v2 路径下订阅流程速览

```text
任意 v2 emit 源（pipeline / 测试 / handle_v2_event 自己）
        │
        v
HarnessState.emit_v2(event)
        │
        ├── is_v2=False → return（v1 短路，零开销）
        │
        └── 写 events.jsonl + bus.emit + subscriptions.dispatch(event)
                                                        │
                                                        v
                                            遍历 WORKER_PROFILE.profiles
                                                        │
                                                        ├── 不匹配 → 跳过
                                                        └── 匹配 → worker.enqueue_v2(event)
                                                                        │
                                                                        v
                                                              worker._consume_loop 取 event
                                                                        │
                                                                        v
                                                              worker.handle_v2_event(event)
                                                                        │
                                                                        v
                                                          decide_to_speak(event, ...)
                                                                        │
                                                ┌────────────────────┼──────────────┐
                                                v                    v              v
                                            IGNORE              SILENT          SPEAK
                                            (log debug)         (emit silent)   (emit speak)
                                                                  │              │
                                                                  └──── acquire lock ────┘
                                                                  ↑(60s 超时 → 降级 silent)↑
```

---

## 3. 怎么跑 v2 demo 看 mention 自动响应

启动 backend（注入 ScriptedBackend mock）：

```bash
cd apps/web-backend
uv run --reload uvicorn app.main:app --port 8000
```

发个 v2 任务（注意带 `"harness_version": "v2"`）：

```bash
curl -X POST http://localhost:8000/api/cluster/feat/report \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "ses_p2demo",
    "raw_text": "本周做了客户回访，活动页面初稿完成...",
    "title": "P2 demo",
    "report_type": "project_progress",
    "audience": "直属领导",
    "duration": "3分钟",
    "style": "简洁正式",
    "harness_version": "v2"
  }'
```

任务跑完后查 events.jsonl：

```bash
TASK_ID=tsk_xxx   # 上一步返回里取
grep -c '"msg_type":"agent.speak"'  data/outputs/$TASK_ID/events.jsonl
grep -c '"msg_type":"agent.silent"' data/outputs/$TASK_ID/events.jsonl
```

期望看到：每个 step 完成后 emit 1 条 speak，下游 worker 通过订阅回响（speak/silent）。

用 replay_check 校验 schema：

```bash
uv run python -m app.orchestrator.replay_check data/outputs/$TASK_ID/events.jsonl
# 期望：v2 events >> 5；schema invalid = 0
```

---

## 4. 写单测

### 4.1 谓词单测

```python
# tests/orchestrator/test_subscription.py
from app.orchestrator.events_v2 import AgentSpeak, ArtifactUpdate
from app.orchestrator.subscription import (
    mention_includes, hint_agent_is, artifact_id_in,
)


def test_mention_includes_matches():
    pred = mention_includes("reviewer")
    ev = AgentSpeak(task_id="t", **{"from": "x"}, text="x",
                    intent="propose", mentions=["reviewer"])
    assert pred(ev, "reviewer") is True

def test_mention_includes_no_match():
    pred = mention_includes("reviewer")
    ev = AgentSpeak(task_id="t", **{"from": "x"}, text="x",
                    intent="propose", mentions=["copywriter"])
    assert pred(ev, "reviewer") is False
```

### 4.2 decide_to_speak 4 分支单测

```python
from app.orchestrator.subscription import (
    decide_to_speak, DecisionResult, WorkerProfile, mention_includes,
    MentionCounter, ReplyToRegistry,
)

def _make_speak(reply_to=None):
    from app.orchestrator.events_v2 import AgentSpeak
    return AgentSpeak(task_id="t", **{"from": "x"}, text="x", intent="ask",
                      reply_to=reply_to, mentions=["me"])


def test_decision_speak_when_requires_satisfied():
    profile = WorkerProfile(interests=(mention_includes("me"),), requires=("MaterialPool",))
    d, _ = decide_to_speak(
        event=_make_speak(), agent_id="me", profile=profile,
        mention_counter=MentionCounter(), reply_to_registry=ReplyToRegistry(),
        available_artifacts={"MaterialPool": 1},
    )
    assert d == DecisionResult.SPEAK

def test_decision_silent_when_requires_missing():
    profile = WorkerProfile(interests=(mention_includes("me"),), requires=("MaterialPool",))
    d, reason = decide_to_speak(
        event=_make_speak(), agent_id="me", profile=profile,
        mention_counter=MentionCounter(), reply_to_registry=ReplyToRegistry(),
        available_artifacts={},
    )
    assert d == DecisionResult.SILENT
    assert "MaterialPool" in reason

def test_decision_ignore_when_duplicate_reply_to():
    profile = WorkerProfile(interests=(mention_includes("me"),), requires=())
    rr = ReplyToRegistry()
    rr.mark("me", "msg_aaaa1111")
    d, _ = decide_to_speak(
        event=_make_speak(reply_to="msg_aaaa1111"), agent_id="me", profile=profile,
        mention_counter=MentionCounter(), reply_to_registry=rr,
        available_artifacts={},
    )
    assert d == DecisionResult.IGNORE

def test_decision_ignore_when_mention_count_exceeded():
    profile = WorkerProfile(interests=(mention_includes("me"),), requires=())
    mc = MentionCounter()
    mc.bump("me"); mc.bump("me")  # 已 2 次（达阈值）
    d, _ = decide_to_speak(
        event=_make_speak(), agent_id="me", profile=profile,
        mention_counter=mc, reply_to_registry=ReplyToRegistry(),
        available_artifacts={},
    )
    assert d == DecisionResult.IGNORE
```

### 4.3 per-agent lock 单测

```python
# tests/orchestrator/test_per_agent_lock.py
import asyncio
import pytest
from app.orchestrator.harness import HarnessState
from app.orchestrator.harness import EventBus


@pytest.mark.asyncio
async def test_same_agent_serial():
    state = HarnessState(run=None, prev={}, by_key={}, bus=EventBus(), is_v2=True)
    lock = state.get_agent_lock("material")

    order: list[str] = []

    async def hold(name: str, dur: float):
        async with lock:
            order.append(f"{name}-acq")
            await asyncio.sleep(dur)
            order.append(f"{name}-rel")

    await asyncio.gather(hold("A", 0.05), hold("B", 0.01))
    # B 必须等 A 释放
    assert order == ["A-acq", "A-rel", "B-acq", "B-rel"]


@pytest.mark.asyncio
async def test_different_agents_parallel():
    state = HarnessState(run=None, prev={}, by_key={}, bus=EventBus(), is_v2=True)
    lock_a = state.get_agent_lock("agent-a")
    lock_b = state.get_agent_lock("agent-b")
    assert lock_a is not lock_b
```

### 4.4 端到端集成测试

```python
# tests/orchestrator/test_v2_subscription_e2e.py
import asyncio, pytest
from app.orchestrator.events_v2 import AgentSpeak
from app.orchestrator.harness import HarnessState, EventBus

@pytest.mark.asyncio
async def test_mention_triggers_subscriber_response(tmp_outputs_dir, stub_state):
    """发 mentions=['point-extractor'] → point-extractor 通过订阅响应。"""
    # 用 stub_state（is_v2=True）+ 手工挂一个最小 SubscriptionRegistry
    # 详细模拟见 P1 test_v2_integration.py 模式
    ...
```

---

## 5. 调试

| 现象 | 检查 |
|---|---|
| 订阅没触发 | 1. task 是否带 `harness_version: "v2"`？2. `state.subscriptions is None` 吗？3. WORKER_PROFILE 里 agent_id 拼写对吗？4. inbox 是否已 start_v2_consumer？ |
| 反复触发同一 agent | 检查 MentionCounter / ReplyToRegistry 是否被 bump / mark；阈值 `V2_MENTION_LIMIT` 是否合理 |
| lock 等超 60s | 1. v1 worker 正在跑（耗时 LLM turn）；2. 把 `V2_LOCK_WAIT_SEC` 调更大 OR 让等不到的 subscription 直接降级 silent（已是默认） |
| events.jsonl 没出现订阅 emit 的 speak/silent | handle_v2_event 内部异常被 catch + log warn；查 `data/.logs/web-backend.log` 找 "subscription" / "handle_v2_event" |
| v1 任务也莫名出现 v2 字段 | 用 grep `events.jsonl` 应 0 命中；如有 → 检查 emit_v2 / dispatch 是否漏了 is_v2 短路 |

---

## 6. 配置项

| 环境变量 | 默认 | 用途 |
|---|---|---|
| `V2_MENTION_LIMIT` | 2 | 同任务被 @ 的次数阈值；超阈 ignore |
| `V2_LOCK_WAIT_SEC` | 60 | per-agent lock 等待超时；超时降级 silent |
| `V2_INBOX_MAX` | 32 | worker inbox 队列容量；满了丢最老 |

无 settings 配置（保持 stdlib only）。

---

## 7. 红线自检

提 PR 前过一遍：

- [ ] 任意用户可见层 grep 不到 `interests` / `requires` / `decide_to_speak` / `lock_wait_timeout` / `_consume_loop` / `enqueue_v2` 字面量
- [ ] v1 demo task 跑出来的 events.jsonl 与 main baseline 逐字段相同（diff 校验）
- [ ] v1 路径下，`state.subscriptions is None` 且 `agent_locks == {}` 且每个 worker `inbox is None`
- [ ] 不动 Coordinator class 任何方法（grep `class Coordinator` / `_resolve_target` 无改动）
- [ ] 不动 Reviewer 现有审校 prompt / SOUL（P4 才动）
- [ ] 不动 agent prompt 生成函数（grep `_build_<step>_prompt` 无改动）

---

## 8. 后续阶段衔接

| 阶段 | 哪里需要这次产出 |
|---|---|
| **P3 Coordinator 转型** | subscription 升级为真 work-driver（Coordinator chain routing 移除后），decide_to_speak 改为可触发 `_run_step` |
| **P4 Reviewer 双轨** | reviewer.handle_v2_event 内升级：依赖齐时不仅 emit speak，还跑真审校；emit ReviewerVerdict |
| **P5 prompt 重写** | 让 prompt 真消费 transcript；subscription text 模板（`f"收到 — {reason}"`）升级为 LLM 生成 |
| **P6 并发** | per-agent lock 改 RWLock；artifact 乐观并发 |
| **P7 UX** | 前端读 events.jsonl 渲染订阅链路：@ 高亮、silent 灰显气泡、artifact diff |
