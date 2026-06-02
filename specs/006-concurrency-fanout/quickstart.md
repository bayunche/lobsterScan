# Quickstart: P6 — EventBus fan-out 并发 + html/video 真并行 验证

## 前置
- P1-P5 已合 main;真 LLM 链路已跑通。在 `006-concurrency-fanout` 分支。

## §1 · 单元 / ScriptedBackend 测试(验收主基线)

```bash
uv run --project apps/web-backend pytest apps/web-backend/tests -q          # 全量
uv run --project apps/web-backend pytest \
  apps/web-backend/tests/orchestrator/test_fanout_emit.py \
  apps/web-backend/tests/orchestrator/test_p6_parallel.py -q                # 仅 P6
```
期望:全绿。`test_v1_regression.py`(fanout off)字段级零回归 → SC-001。

## §2 · fanout off 零回归(SC-001)

```bash
# 不设 V2_FANOUT(默认 off)→ 行为等价 P5
uv run --project apps/web-backend pytest apps/web-backend/tests/orchestrator/test_v1_regression.py -q
```

## §3 · fanout on 真 LLM 端到端 + 耗时对比(SC-003/004)

```bash
# ---- A. fanout OFF 基线(测 Script→收尾耗时)----
for pid in $(netstat -ano | grep ":8000 .*LISTENING" | awk '{print $NF}'|sort -u); do taskkill //F //PID $pid; done
set -a; source .env; set +a
export OPENCLAW_BIN="$(pwd)/node_modules/.bin/openclaw"
unset V2_FANOUT     # off
( uv run --project apps/web-backend uvicorn app.main:app --host 127.0.0.1 --port 8000 >data/.logs/web-backend.log 2>&1 & )
sleep 12
resp=$(curl -sS -X POST http://127.0.0.1:8000/api/cluster/feat/report \
  -H 'Content-Type: application/json; charset=utf-8' --data-binary "@data/.logs/t040_payload.json")
TASK_OFF=$(echo "$resp" | python -c "import sys,json;print(json.load(sys.stdin)['task_id'])")
echo "$TASK_OFF" > /tmp/p6_off.txt
# 轮询到终态,记录 copywriting done → 收尾的墙钟时间(events.jsonl ts 差)

# ---- B. fanout ON ----
# 重起后端 export V2_FANOUT=on,同样提交 + 轮询
```

**验收断言**(读真实产物):
- 两次 task 都 status ∈ {done, partial}(非 failed),8 内容 step 全 success(SC-003)。
- fanout ON 的 events.jsonl:html-designer 与 video-producer 的 `agent.start` 都出现在
  对方 `agent.done` **之前**(确证并行,SC-004)。
- ON 的 copywriting.done → 收尾(gate_pass/reject)墙钟 ≤ OFF(并行不更慢,SC-003)。
- 任一模式,同 step 只一次 agent.done(去重未被破坏,SC-005)。

## §4 · 回退验证(FR-012)

```bash
unset V2_FANOUT   # 或 export V2_FANOUT=off → 重起 → 行为回 P5(串行),无需改代码
```

## 注意
- 真 LLM 偶发 deepseek 网络抖动属环境,不计入并发改造成败(spec 假设)。
- 轮询用真实 task_id(memory: verify-real-task-id);终态以 task.json status 为准,Read 读避 GBK。
- V2_FANOUT 与 V2_PROMPT_MODE(P5)正交,可组合。
