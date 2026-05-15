#!/usr/bin/env bash
# 把 9 个项目 Agent 注册到 OpenClaw (~/.openclaw/openclaw.json)
# 幂等：已存在的 agent 会被跳过

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS=(coordinator material point-extractor structure upward-opt copywriter html-designer video-producer reviewer)

# 默认模型从 openclaw.json providers.default 推断；这里先用空让 OpenClaw 用全局默认
MODEL_OPT=""
if [[ -n "${LOBSTER_DEFAULT_MODEL:-}" ]]; then
  MODEL_OPT="--model $LOBSTER_DEFAULT_MODEL"
fi

# 已注册的 agent 列表
EXISTING=$(openclaw agents list 2>/dev/null | grep -E "^- " | awk '{print $2}' | tr -d '()')

register_one() {
  local id="$1"
  if echo "$EXISTING" | grep -qx "$id"; then
    echo "  ⊘ $id (已注册)"
    return
  fi
  local ws="$REPO_ROOT/openclaw/workspaces/$id"
  local ad="$HOME/.openclaw/agents/lobster-$id"
  mkdir -p "$ws/.agents/skills" "$ad"
  openclaw agents add "$id" \
    --workspace "$ws" \
    --agent-dir "$ad" \
    $MODEL_OPT \
    --non-interactive --json 2>/dev/null \
    | python3 -c "
import json, sys
try:
  d = json.load(sys.stdin)
  print(f\"  ✓ {d.get('agentId', '$id')}  ws={d.get('workspace', '')}\")
except Exception as e:
  print(f\"  ✗ $id error: {e}\")
"
}

echo "▸ 注册 9 个 lobsterScan agent 到 OpenClaw …"
for id in "${AGENTS[@]}"; do
  register_one "$id"
done

echo
echo "▸ 当前 OpenClaw agents："
openclaw agents list 2>/dev/null | grep -E "^- " | head -25
