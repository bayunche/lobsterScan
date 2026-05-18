#!/usr/bin/env bash
# 一键拉起开发环境
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

[[ -f .env ]] || { echo "请先 cp .env.example .env 并填入 API key"; exit 1; }
set -a; source .env; set +a

# 解析项目自托管的 openclaw,优先 ./node_modules/.bin/openclaw,否则回退全局 PATH。
# export OPENCLAW_BIN 给 web-backend(Python client 用它定位 CLI,见 app/openclaw/client.py)
LOCAL_OC="$REPO_ROOT/node_modules/.bin/openclaw"
if [[ -x "$LOCAL_OC" ]]; then
  export OPENCLAW_BIN="$LOCAL_OC"
  echo "▸ openclaw = $LOCAL_OC (self-hosted)"
elif command -v openclaw >/dev/null; then
  export OPENCLAW_BIN="$(command -v openclaw)"
  echo "▸ openclaw = $OPENCLAW_BIN (PATH fallback,建议 pnpm install 让其挪到 node_modules)"
else
  unset OPENCLAW_BIN
  echo "⚠️  未找到 openclaw 二进制 — 先 pnpm install 或 npm i -g openclaw@2026.5.5"
fi

LOG_DIR="$REPO_ROOT/data/.logs"
mkdir -p "$LOG_DIR"

run() {
  local name="$1"; shift
  echo "▸ start $name → $LOG_DIR/$name.log"
  ( "$@" >>"$LOG_DIR/$name.log" 2>&1 & echo $! >"$LOG_DIR/$name.pid" )
}

# 1. OpenClaw Gateway(走解析到的 OPENCLAW_BIN)
#    检测 OPENCLAW_GATEWAY_PORT(.env 给出,默认 18789)是否已被占用,
#    若已有 gateway 在跑(常见于用户全局 openclaw 后台进程)就跳过启动,
#    避免端口冲突。业务后端走 --local 模式不经 gateway,跳过不影响 pipeline。
GW_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
if ss -tln 2>/dev/null | awk '{print $4}' | grep -qE ":${GW_PORT}\$"; then
  echo "▸ skip gateway: 端口 ${GW_PORT} 已被占用(看起来是已运行的 openclaw gateway,pipeline 走 --local 不需要)"
elif [[ -n "${OPENCLAW_BIN:-}" ]]; then
  run gateway "$OPENCLAW_BIN" gateway
else
  echo "⚠️ 跳过 gateway(openclaw 未安装)"
fi

# 2. web-backend (FastAPI)
run web-backend uv run --directory apps/web-backend uvicorn app.main:app \
  --host "$WEB_BACKEND_HOST" --port "$WEB_BACKEND_PORT" --reload

# 3. admin-backend
run admin-backend uv run --directory apps/admin-backend uvicorn app.main:app \
  --host "$ADMIN_BACKEND_HOST" --port "$ADMIN_BACKEND_PORT" --reload

# 4. web-frontend
run web-frontend pnpm --filter web-frontend dev

# 5. admin-frontend
run admin-frontend pnpm --filter admin-frontend dev

echo ""
echo "✅ services starting:"
echo "  - 用户端:        http://localhost:3000"
echo "  - 管理平台:      http://localhost:3100"
echo "  - web-backend:   http://localhost:$WEB_BACKEND_PORT/docs"
echo "  - admin-backend: http://localhost:$ADMIN_BACKEND_PORT/docs"
echo ""
echo "停止： bash scripts/stop.sh"
