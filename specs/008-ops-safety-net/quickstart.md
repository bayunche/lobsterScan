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
# 起 admin-backend(secrets 单源,web 依赖它拉 key)
uv run --directory apps/admin-backend uvicorn app.main:app --port 8100 &

# 起 web-backend(Windows 不加 --reload;openclaw.mjs 现由代码自动定位,无需手动设 OPENCLAW_BIN)
V2_PROMPT_MODE=envelope V2_FANOUT=on \
V2_BUDGET_CAP=30000 V2_ROLLING_SUMMARY=on V2_SUMMARY_THRESHOLD=6 V2_YESMAN_DEFENSE=on \
uv run --directory apps/web-backend uvicorn app.main:app --port 8000 &

# 提交任务(payload 写文件用 -d @file 避免 shell 中文转义),取响应里的真实 task_id
curl -s -X POST http://127.0.0.1:8000/api/tasks -H 'Content-Type: application/json' -d @task.json
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

## 3. 实测结果 + 已知环境约束(诚实标注)

**✅ budget 轨已真 LLM 实测通过**(2026-06-02,task `tsk_13bdd9a288fe`,minimax):
- `status=partial`(非 failed)/ `material_parsing` success + 产物 7781B 保留 /
  spent_tokens 越 30000 cap → emit `intervene(kind=budget)` /
  触顶后 structure/upward/copywriting/html/video/review **全 skipped(SC-002 新环节=0)** /
  budget 发声脱敏无 token·id 泄漏(SC-003) / reviewer.verdict 带 yesman on 跑通未报错。
- **未视觉实证**:rolling 折叠(本次仅 ~2 发言 < 阈值 6,需更长任务触发)+ yesman prompt 文本
  (事件回读不到)—— 二者由组件测试(rolling 6 + yesman 3 绿)证。要视觉实证 rolling,
  需调高 cap 让任务多跑几步、发言累积过阈。

**Windows openclaw spawn(已在代码根治,无需手动设环境变量)**:
- P8 e2e 首次踩坑:不设 `OPENCLAW_BIN` 时裸默认 `"openclaw"` 推不出 `openclaw.mjs` → fallback 裸 bin →
  Windows `CreateProcess` 不识别无扩展名 sh shim → `[WinError 2]`,每 turn 立即失败、observer 空转。
- **已修复**:`agent_backend._find_openclaw_mjs` 现从本文件向上逐级找 `node_modules/openclaw/openclaw.mjs`
  (不依赖 CWD / 不强依赖 `OPENCLAW_BIN`),Windows 自动 `node openclaw.mjs`。手动设 `OPENCLAW_BIN` 仍作显式 override。
  测试:`tests/orchestrator/test_agent_backend_bin.py`(5 绿)。

**其他**:
- budget 触顶的「中途」时机受 provider 单步 token 量波动影响(同 P6 耗时 LLM variance);
  cap 数值需按实测微调,SC-002 的「新环节数为 0」用 events.jsonl 客观判定,不靠耗时。
- 正在运行的 turn 在触顶时允许跑完(其成果保留),只挡**新** turn —— 故实测里 `point_extraction`
  可能停在 `running`(它在 budget tick 前已过短路检查起跑),其余步才是被 `budget_exceeded` 挡住 skipped。
