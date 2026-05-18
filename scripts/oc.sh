#!/usr/bin/env bash
# 项目内统一 openclaw 调用入口。
# 优先 ./node_modules/.bin/openclaw(自托管,pnpm install 装的);若不存在再退回到全局 PATH。
#
# 用法:
#   bash scripts/oc.sh agents list
#   bash scripts/oc.sh gateway
#   bash scripts/oc.sh --profile lobster-foo agent --agent main --local -m "hi"

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

LOCAL_BIN="$REPO_ROOT/node_modules/.bin/openclaw"

if [[ -x "$LOCAL_BIN" ]]; then
  exec "$LOCAL_BIN" "$@"
fi

if command -v openclaw >/dev/null 2>&1; then
  exec openclaw "$@"
fi

cat >&2 <<EOF
❌ 未找到 openclaw。
   推荐 (自托管): cd "$REPO_ROOT" && pnpm install
   回退 (全局):    npm install -g openclaw@2026.5.5
EOF
exit 127
