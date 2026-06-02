# Phase 1 Data Model: P8 — 运营兜底

**Date**: 2026-06-02 | **Branch**: `008-ops-safety-net`

无新持久化实体(预算计数是 task-level 内存态)。本文件定义:配置常量、状态字段、折叠算法、
yesman 块、三个 enabled helper,以及零回归不变量。

---

## 1. 配置常量(`subscription.py`,env 可覆盖,默认关)

| 常量 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `V2_BUDGET_CAP` | `int` | `0` | 任务级 token 硬上限;`0` = off/无限 |
| `V2_ROLLING_SUMMARY` | `str` | `"off"` | rolling 折叠开关:`off`/`on` |
| `V2_SUMMARY_THRESHOLD` | `int` | `20` | `_recent` 条数超此值才折叠 |
| `V2_YESMAN_DEFENSE` | `str` | `"off"` | 审校对立质疑开关:`off`/`on` |

- 全部加入 `subscription.__all__`。
- 形态沿用 `V2_PROMPT_MODE`/`V2_FANOUT`(P5/P6):模块级变量,`os.environ.get` 初始化,运行期可 monkeypatch。
- **正交**:四者各自独立读取,互不引用;与 `V2_PROMPT_MODE`/`V2_FANOUT`/`is_v2` 无耦合(FR-003)。

---

## 2. 状态字段(`harness.py` · `HarnessState`)

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `spent_tokens` | `int` | `0` | 本任务累计消耗 token(US1) |
| `budget_exceeded` | `bool` | `False` | 是否已触顶(短路新 turn + 软着陆去重的真相源) |

- 默认值 = v1 路径不受影响(v1 不累计、不读)。
- 累计语义:`_run_step` 拿到 `TurnResult` 后 `state.spent_tokens += s.total_tokens`(主 turn);
  `_quick_review` turn(被 pipeline `_gate_review` 与 harness `_reviewer_quality_review` 共用)也补计。
- 缺失/异常 token 数按 0 计(`getattr(s, "total_tokens", 0)`),不抛(edge case)。

---

## 3. 预算硬上限流程(US1)

```
_run_step 完成一个 turn
  └─ state.spent_tokens += s.total_tokens        (累计;state 经 prev["__state__"] 取得)

observer._loop 每拍(OBSERVER_TICK_SEC=0.5s)
  └─ if _budget_enabled() and not state.budget_exceeded
        and state.spent_tokens >= V2_BUDGET_CAP:
         _on_budget_exceeded()                    (一次性)

_on_budget_exceeded():
  1. state.budget_exceeded = True                 # 短路真相源(FR-008/012)
  2. await _emit_intervene("budget", "为控制生成开销,我先用现有内容收尾。")   # FR-011, 脱敏
  3. result = self.gate.check(task_id)            # 复用既有 gatekeeper 纯规则
  4. _set_done("done" if result.passed else "partial")   # FR-009, 产物天然保留(FR-010)

新 turn 启动路径(短路点,FR-008):
  - AgentWorker.force_run_v2():开头 if state.budget_exceeded: return
  - handle_v2_event SPEAK 真跑分支:开头 if state.budget_exceeded: 不 _run_unlocked
```

**不变量**:
- `V2_BUDGET_CAP == 0` → observer 检测分支整体短路,`spent_tokens` 即使累计也无人消费 ⇒ 零回归(SC-001)。
- 软着陆只触发一次:`budget_exceeded` 既是短路标志又是去重标志(再入 `_on_budget_exceeded` 被 `not ... budget_exceeded` 挡掉)。
- 正在运行的 turn 不被取消(只挡新 turn)→ 其成果照常落盘(FR-010 / 边界:运行中跨越上限)。
- `CoordinatorIntervene.kind` 的 `Literal` **已含 `"budget"`**(events_v2.py:49-51),无 schema 改动。

---

## 4. rolling summary 折叠算法(US2 · `_transcript_block`)

输入:observer `_recent`(全量 agent.speak.text 列表),`K = V2_TRANSCRIPT_K`,`T = V2_SUMMARY_THRESHOLD`。

```
recent_all = list(obs._recent or [])
if _rolling_enabled() and len(recent_all) > T:
    tail = recent_all[-K:]                        # 逐条保留尾部 K 条(FR-013)
    folded = recent_all[:-K]                      # 更早的 N 条
    fold_text = "；".join(str(x) for x in folded)[:200]
    lines = ["（前 %d 条发言已折叠：%s）" % (len(folded), fold_text)]   # 一行摘要(FR-015,脱敏:仅发言文本)
    lines += [f"- {str(t)[:160]}" for t in tail]
else:
    use = recent_all[-K:]                          # 关闭/未超阈值:维持 P5 现状(FR-014,零回归)
    lines = [f"- {str(t)[:160]}" for t in use]
```

**不变量**:
- `_rolling_enabled()` 假 或 `len ≤ T` → 走 `recent_all[-K:]`,与 P5 `_transcript_block` 逐字段一致(SC-001)。
- 注入条数上界 = `K + 1`(尾部 K 行 + 1 行折叠摘要)(SC-004)。
- 折叠摘要不含 id(只拼 `agent.speak.text`,本就脱敏)(FR-015 / SC-003 同口径)。
- observer 为 None / 异常 → `_transcript_block` 既有 try/except 返回空串(FR-016)。
- 边界 `T=1`、`K≥1`:`folded = recent_all[:-K]` 可能为空 → 不进折叠分支(`len > T` 仍需成立);
  若 `len>T` 且 `K≥len` 则 `folded` 空 → fold_text 空、`len(folded)=0`,渲染「（前 0 条…）」无害(测试覆盖)。

**`RollingSummarizer` 注入点(可选,本期纯规则)**:若引入,`get/set_default_rolling_summarizer()`
返回默认 `RuleBasedRollingSummarizer`(上述拼接截断逻辑);真 LLM 实现非目标。决策见 research §3。

---

## 5. yes-man 对立质疑块(US3 · `pipeline.py`)

```python
_YESMAN_BLOCK = """
# 审校立场（对立质疑）
你不是来盖章放行的。请带着挑剔的眼光审：
- 主动找瑕疵：假设作者可能夸大、遗漏或自说自话，逐条核对是否真站得住。
- 不达标就如实指出，并给出具体可执行的重做指令；不要用「看上去 OK」式敷衍放行。
- 只有确实没有明显问题时才通过。
"""

def _yesman_enabled() -> bool: ...   # 读 subscription.V2_YESMAN_DEFENSE == "on",异常按 off

# 注入：QUICK_REVIEW_PROMPT 渲染时，_yesman_enabled() → 在提示词前/内拼 _YESMAN_BLOCK
```

**覆盖面(关键)**:`_quick_review`(pipeline)被 **两条** review 路径共用 ——
① pipeline `_gate_review`(v1/legacy 质量门)、② harness `_reviewer_quality_review`(P4 质量轨,harness.py:487
`from .pipeline import _quick_review`)。**故注入一处(`QUICK_REVIEW_PROMPT` 构造)即覆盖两轨**。

**不变量**:`_yesman_enabled()` 假(默认)→ prompt 一字不拼,与 P7 逐字段一致(FR-018 / SC-001)。
注入仅追加立场段,不改 `accept/comment/reason_if_reject` 输出契约(SC-005:开关不影响能否正常收尾)。

---

## 6. 三个 enabled helper(`pipeline.py`,沿用 `_envelope_enabled` 范式)

```python
def _budget_enabled() -> bool:   # observer 侧也需要;放 pipeline 或 observer 就近，每次读 subscription.V2_BUDGET_CAP > 0
def _rolling_enabled() -> bool:  # subscription.V2_ROLLING_SUMMARY == "on"
def _yesman_enabled() -> bool:   # subscription.V2_YESMAN_DEFENSE == "on"
```
- 每次读模块属性(支持 monkeypatch);`try/except → 关闭` 降级。
- budget 的「enabled + cap 值」检测在 observer 内就近读 `subscription.V2_BUDGET_CAP`(避免 pipeline↔observer 循环 import,
  同 `_fanout_enabled_safe` 在 harness 内读 subscription 的做法)。

---

## 7. 零回归不变量汇总(FR-002 / SC-001)

| flag 全默认 | 行为 |
|---|---|
| `V2_BUDGET_CAP=0` | observer budget 分支短路;`spent_tokens` 无人读;`budget_exceeded` 恒 False → 派发不短路 |
| `V2_ROLLING_SUMMARY=off` | `_transcript_block` 走 `recent_all[-K:]` = P5 原逻辑 |
| `V2_YESMAN_DEFENSE=off` | `QUICK_REVIEW_PROMPT` 不拼 `_YESMAN_BLOCK` = P4/P7 原 prompt |
| 三者 + `is_v2=False` | v1 路径根本不构造 observer/transcript,完全不触达 |

⇒ `test_v1_regression.py` 字段级对比通过即证零回归。
