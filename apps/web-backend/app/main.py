"""会汇报 · web-backend 入口

V0 接入方式：用 `openclaw agent` CLI subprocess 触发 turn（embedded mode），
8-step orchestrator 顺序调用 9 个 Agent，SSE 推送进度。

后续可升级到 WebSocket Channel Plugin 持久连 Gateway。
"""

# Windows 上 asyncio SelectorEventLoop 不支持 subprocess(NotImplementedError),
# OpenClaw agent CLI 调用全部挂掉。在模块导入期把 policy 切回 ProactorEventLoop
# (Python 3.8+ Windows 默认),保证在 plain `uvicorn` / 其他 ASGI runner / pytest 下
# subprocess 可用。非 Windows 平台 no-op。
#
# 已知局限:uvicorn `--reload` 模式下,uvicorn 强制 `use_subprocess=True`,
# `uvicorn/loops/asyncio.py` 又把它映射回 SelectorEventLoop,**会覆盖此 policy**。
# 因此 Windows + `--reload` + 真实 LLM 任务的组合仍不可用 — 必须改 scripts/dev.sh
# 去掉 --reload(代价:代码改动后手动 restart;但能跑真任务)。详 specs/002-worker-subscription
# T040 阻塞汇报。
import asyncio
import sys

if sys.platform == "win32":
    _policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if _policy_cls is not None and not isinstance(
        asyncio.get_event_loop_policy(), _policy_cls,
    ):
        asyncio.set_event_loop_policy(_policy_cls())

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
