#!/usr/bin/env bash
# 一键拉起开发环境
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

[[ -f .env ]] || { echo "请先 cp .env.example .env 并填入 API key"; exit 1; }
set -a; source .env; set +a

LOG_DIR="$REPO_ROOT/data/.logs"
mkdir -p "$LOG_DIR"

run() {
  local name="$1"; shift
  echo "▸ start $name → $LOG_DIR/$name.log"
  ( "$@" >>"$LOG_DIR/$name.log" 2>&1 & echo $! >"$LOG_DIR/$name.pid" )
}

# 1. OpenClaw Gateway
if command -v openclaw >/dev/null; then
  run gateway openclaw gateway
else
  echo "⚠️ 未安装 openclaw CLI，跳过 gateway"
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
