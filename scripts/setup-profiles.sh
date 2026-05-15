#!/usr/bin/env bash
# 为 9 个角色各创建一个独立 OpenClaw 实例（profile = 完整隔离的 state + config）
#
# 每个 profile：
#   ~/.openclaw-lobster-<id>/openclaw.json   profile 配置
#   workspace 指向 openclaw/workspaces/<id>/  共享我们项目的 SOUL/AGENTS/USER
#   auth-profiles.json 从 main profile 拷贝过来（共享 deepseek / minimax 等 key）

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLES=(coordinator material point-extractor structure upward-opt copywriter html-designer video-producer reviewer)

MAIN_AUTH="$HOME/.openclaw/agents/main/agent/auth-profiles.json"
MAIN_CONFIG="$HOME/.openclaw/openclaw.json"

if [[ ! -f "$MAIN_AUTH" ]]; then
  echo "❌ 主 profile auth 未配置: $MAIN_AUTH"
  exit 1
fi
if [[ ! -f "$MAIN_CONFIG" ]]; then
  echo "❌ 主 profile 配置不存在: $MAIN_CONFIG"
  exit 1
fi

setup_one() {
  local id="$1"
  local profile="lobster-$id"
  local state_dir="$HOME/.openclaw-$profile"
  local ws="$REPO_ROOT/openclaw/workspaces/$id"

  if [[ -f "$state_dir/openclaw.json" ]]; then
    echo "  ⊘ $profile (已存在)"
    return
  fi

  echo "  ▸ $profile 初始化…"
  mkdir -p "$state_dir" "$state_dir/agents/main/agent"

  # 1. 用主配置作为模板，patch workspace 字段
  python3 - <<PY
import json, pathlib
src = json.loads(pathlib.Path("$MAIN_CONFIG").read_text(encoding="utf-8"))
src.setdefault("agents", {}).setdefault("defaults", {})["workspace"] = "$ws"
# 该 profile 只保留 main 一个 agent；删掉 main 之外的，避免和别的 profile 重叠
agents_list = src.get("agents", {}).get("list") or []
new_list = []
for a in agents_list:
    if a.get("id") == "main":
        a["workspace"] = "$ws"
        new_list.append(a)
src.setdefault("agents", {})["list"] = new_list
# 全部清空 plugins 配置（profile 之间隔离插件，避免引用主 profile 的失效插件）
src["plugins"] = {}
pathlib.Path("$state_dir/openclaw.json").write_text(
    json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8"
)
PY

  # 2. 拷贝 auth-profiles 给 main agent
  cp "$MAIN_AUTH" "$state_dir/agents/main/agent/auth-profiles.json"
  echo "    ✓ workspace=$ws · auth ✓"
}

echo "▸ 为 9 个角色各建一个独立 OpenClaw 实例（profile 模式）…"
for r in "${ROLES[@]}"; do setup_one "$r"; done

echo
echo "▸ 验证 ·"
for r in "${ROLES[@]}"; do
  if openclaw --profile "lobster-$r" agents list 2>/dev/null | grep -q "Workspace:.*$r"; then
    echo "  ✓ lobster-$r"
  else
    echo "  ✗ lobster-$r 配置可能有问题"
  fi
done

echo
echo "✓ 完成。9 个独立 OpenClaw 实例就绪。"
