"""GET /api/tasks/{task_id}/events · SSE 通道（订阅 pipeline 事件）"""

from __future__ import annotations

import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from ..orchestrator import pipeline

router = APIRouter(tags=["events"])


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str):
    async def gen():
        async for event, data in pipeline.subscribe(task_id):
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(gen(), ping=15)
