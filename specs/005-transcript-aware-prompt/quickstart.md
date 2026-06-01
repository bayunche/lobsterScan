# Quickstart: P5 — Transcript-Aware Prompt + speak/silent/done 验证

## 前置
- P1-P4 已合 main;真 LLM 链路已跑通(deepseek 已配)。
- 在 `005-transcript-aware-prompt` 分支。

## §1 · 单元 / ScriptedBackend 测试(验收主基线)

```bash
# 全量(应保持基线 + 新增)
uv run --project apps/web-backend pytest apps/web-backend/tests -q

# 仅 P5 相关
uv run --project apps/web-backend pytest apps/web-backend/tests/orchestrator/test_transcript_block.py \
  apps/web-backend/tests/orchestrator/test_envelope_parse.py \
  apps/web-backend/tests/orchestrator/test_p5_e2e.py -q
```
期望:全绿。`test_v1_regression.py`(legacy 模式)字段级零回归 → SC-001。

## §2 · legacy 零回归确认(SC-001)

```bash
# 不设 V2_PROMPT_MODE(默认 legacy)→ 行为应等价 P4
uv run --project apps/web-backend pytest apps/web-backend/tests/orchestrator/test_v1_regression.py -q
```

## §3 · envelope 模式真 LLM 端到端(SC-003,1 次)

```bash
# 起后端(Windows:plain uvicorn,见 dev.sh)+ envelope flag
for pid in $(netstat -ano | grep ":8000 .*LISTENING" | awk '{print $NF}'|sort -u); do taskkill //F //PID $pid; done
set -a; source .env; set +a
export OPENCLAW_BIN="$(pwd)/node_modules/.bin/openclaw"
export V2_PROMPT_MODE=envelope          # 开启 P5
( uv run --project apps/web-backend uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  >data/.logs/web-backend.log 2>&1 & )
sleep 12 && curl -sS http://127.0.0.1:8000/healthz

# 提交 v2 task(harness_version=v2 的 payload),用真实返回的 task_id 轮询
resp=$(curl -sS -X POST http://127.0.0.1:8000/api/cluster/feat/report \
  -H 'Content-Type: application/json; charset=utf-8' --data-binary "@data/.logs/t040_payload.json")
TASK=$(echo "$resp" | python -c "import sys,json;print(json.load(sys.stdin)['task_id'])")
echo "$TASK" > /tmp/p5_tid.txt; echo "TASK=$TASK"
```

**验收断言**(读真实产物,不臆测):
- `data/outputs/$TASK/task.json` 的 status ∈ {done, partial}(非 failed)。
- 8 个内容 step(material_parsing…video_production)全 success。
- `events.jsonl` 无因契约改造引入的解析失败 / KeyError。
- 任一 step 的 prompt(可临时 log)含「群聊上下文」段落 → SC-004。
- 产物 schema(script.md / outline.json / …)与 legacy 字段级一致 → SC-005。

## §4 · 回退验证(FR-015)

```bash
# 去掉 flag(或设 legacy)→ 重起 → 行为回到 P4,无需改代码
unset V2_PROMPT_MODE   # 或 export V2_PROMPT_MODE=legacy
# 重起后端,重跑 §3 → 任务仍跑通(等价 P4),证明一键回退
```

## 注意
- 真 LLM 偶发 deepseek 网络抖动(FailoverError)属环境,不计入契约改造成败(spec 假设)。
- 轮询用真实 task_id(见 memory: verify-real-task-id),终态以 task.json status 为准。
