"""OpenClaw 管理平台后端入口（详见 docs/管理平台规格.md）"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    agents, avatars, bindings, broadcast, config_api, dashboard, health,
    pipelines, secrets, sessions, skills, storage, templates, tokens,
)
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="OpenClaw 统一管台 · admin-backend", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # 同时放行 localhost 和 127.0.0.1(浏览器把它们当不同 origin,CORS 严格匹配)
    allow_origins=["http://localhost:3100", "http://127.0.0.1:3100"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PREFIX = "/admin/api"
for r in (
    dashboard.router,
    agents.router, skills.router, bindings.router, sessions.router,
    pipelines.router, health.router, config_api.router, templates.router,
    tokens.router, secrets.router, avatars.router, storage.router, broadcast.router,
):
    app.include_router(r, prefix=PREFIX)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
