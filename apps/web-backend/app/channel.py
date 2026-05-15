"""huihuibao-web Channel Plugin

连 OpenClaw Gateway，把 web-backend 创建的任务转写成 inbound message，
并把 coordinator 回流的事件 fanout 给 SSE 订阅者。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import websockets

log = logging.getLogger("huihuibao.channel")


class HuihuibaoChannel:
    def __init__(self, gateway_host: str, gateway_port: int):
        self._url = f"ws://{gateway_host}:{gateway_port}"
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._task: asyncio.Task | None = None
        # task_id → list[asyncio.Queue]，每个 SSE 连接一个 queue
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            try:
                async with websockets.connect(self._url) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({
                        "type": "channel.register",
                        "name": "huihuibao-web",
                    }))
                    async for raw in ws:
                        await self._dispatch(json.loads(raw))
            except Exception as e:  # noqa: BLE001
                log.warning("channel disconnected: %s, retry in 3s", e)
                await asyncio.sleep(3)
            finally:
                self._ws = None

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        # coordinator 发回的事件 metadata 里带 task_id
        task_id = msg.get("metadata", {}).get("task_id") or msg.get("task_id")
        if not task_id:
            return
        for q in self._subscribers.get(task_id, []):
            await q.put(msg)

    async def send_create_task(
        self,
        *,
        task_id: str,
        user_id: str,
        prompt: str,
        meta: dict[str, Any],
    ) -> None:
        if not self._ws:
            raise RuntimeError("channel not connected to gateway")
        await self._ws.send(json.dumps({
            "type": "message.inbound",
            "channel": "huihuibao-web",
            "accountId": user_id,
            "peerId": f"task:{task_id}",
            "content": prompt,
            "metadata": {"task_id": task_id, **meta},
        }))

    async def subscribe(self, task_id: str) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(task_id, []).append(q)
        try:
            while True:
                msg = await q.get()
                yield msg
        finally:
            self._subscribers.get(task_id, []).remove(q)
