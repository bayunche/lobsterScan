# Quickstart: P8 — 运营兜底验证

**Date**: 2026-06-02 | **Branch**: `008-ops-safety-net`

验收基线(用户选定):**测试级全绿 + 1 次真 LLM 端到端**。沿用 P5/P6 基线。

---

## 1. 测试级(主证)

```bash
# 全量 orchestrator 测试(应含 P1-P7 既有 + P8 新增,零回归)
uv run --directory apps/web-backend python -m pytest tests/orchestrator/ -q

# 仅 P8 三轨
uv run --directory apps/web-backend python -m pytest \
  tests/orchestrator/test_p8_budget.py \
  tests/orchestrator/test_p8_rolling.py \
  tests/orchestrator/test_p8_yesman.py -q

# 零回归专测(三 flag unset → 字段级 = P7)
uv run --directory apps/web-backend python -m pytest tests/orchestrator/test_v1_regression.py -q
```

**通过标准**:
- `test_p8_budget`:累计正确 / `spent_tokens>=cap` 触发 `_on_budget_exceeded` / 软着陆 done|partial /
  已完成产物保留 / `budget_exceeded` 后 `force_run_v2` 短路 / 只软着陆一次 / emit `intervene(kind=budget)` 脱敏。
- `test_p8_rolling`:超阈值折叠为「尾部 K 行 + 1 行摘要」 / 未超阈值原样 / observer 缺失返回空串 / `T=1` 边界不崩。
- `test_p8_yesman`:`V2_YESMAN_DEFENSE=on` → `QUICK_REVIEW_PROMPT` 含对立质疑段;`off` → 不含。
- `test_v1_regression`:三 flag unset(默认)下 prompt/行为字段级与 P7 一致 → 既有断言全绿。

---

## 2. 真 LLM 端到端(1 次,SC-002/003/004/005 真实佐证)

> 需 `.env` 至少一个 LLM provider key + admin-backend secrets 就绪(同既往)。
> **用 POST 返回的真实 `task_id` 轮询,不读到真实产物不声称已验证**(working-agreement)。

```bash
# 起后端(Windows 不加 --reload)
uv run --directory apps/web-backend uvicorn app.main:app --port 8000 &

# 开三能力 + 设很低预算(让任务跑几步就触顶),走 v2 envelope 路径
export V2_PROMPT_MODE=envelope V2_FANOUT=on
export V2_BUDGET_CAP=120000 V2_ROLLING_SUMMARY=on V2_SUMMARY_THRESHOLD=8 V2_YESMAN_DEFENSE=on
# (cap 数值按实测 provider 单步 token 量调,目标:5 分钟档跑到中途触顶)

# 提交任务(取响应里的真实 task_id),SSE 订阅观察群聊
# 然后读 data/outputs/<真实task_id>/{task.json,chat.jsonl,events.jsonl} 核对
```

**人工核对清单**:
- [ ] `task.json` 最终 status = `partial`(触顶时产物不齐) 或 `done`(恰好齐) —— 不是 `failed`(SC-002/006)。
- [ ] 触顶后 `events.jsonl` 无新 step 启动事件 —— 新环节数为 0(SC-002)。
- [ ] 已完成 step 的产物(MaterialPool/ReportCore/Outline 等已产出的)仍在 outputs 下(FR-010)。
- [ ] `chat.jsonl` 有一条 budget 收尾发声,**不含** token 数 / `tsk_` / `agent_id`(SC-003)。
- [ ] envelope prompt 注入的「群聊上下文」条数 ≤ K+1,含一行「前 N 条已折叠」(SC-004)。
- [ ] reviewer 发声体现挑剔立场(yesman 生效,SC-005),且任务正常收尾。

**回退验证(FR-002 / SC-001)**:
```bash
unset V2_BUDGET_CAP V2_ROLLING_SUMMARY V2_SUMMARY_THRESHOLD V2_YESMAN_DEFENSE
# 再跑一次:无 budget 发声、无折叠行、reviewer prompt 无对立质疑段 → P7 行为
```

---

## 3. 已知环境约束(诚实标注)

- 真 LLM 端到端依赖网络 + provider 配额;若环境受阻(同 P7 CDP / deepseek 历史),
  真 LLM 验证可 deferred,**测试级三轨 + 零回归为主证**,并如实标注未实测项。
- budget 触顶的「中途」时机受 provider 单步 token 量波动影响(同 P6 耗时 LLM variance);
  cap 数值需按实测微调,SC-002 的「新环节数为 0」用 events.jsonl 客观判定,不靠耗时。
