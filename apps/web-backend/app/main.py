"""会汇报 · web-backend 入口

V0 接入方式：用 `openclaw agent` CLI subprocess 触发 turn（embedded mode），
8-step orchestrator 顺序调用 9 个 Agent，SSE 推送进度。

后续可升级到 WebSocket Channel Plugin 持久连 Gateway。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import cluster, events, exports, files, tasks

app = FastAPI(title="会汇报 · web-backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    # 同时放行 localhost 和 127.0.0.1(浏览器把它们当不同 origin,CORS 严格匹配)
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cluster.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(exports.router, prefix="/api")


@app.get("/healthz")
async def healthz():
    return {"ok": True}
