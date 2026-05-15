"""管台 · 会话浏览（读 ~/.openclaw/agents/<id>/sessions）

V0：列目录 + 解析 JSONL；空目录返回空列表。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _sessions_dir(agent_id: str) -> Path:
    return settings.home_openclaw / "agents" / agent_id / "sessions"


@router.get("")
async def list_sessions(agent_id: str | None = None, limit: int = 50) -> dict:
    items: list[dict] = []
    agents = (
        [agent_id]
        if agent_id
        else [p.name for p in (settings.home_openclaw / "agents").iterdir()
              if p.is_dir()] if (settings.home_openclaw / "agents").exists() else []
    )
    for aid in agents:
        d = _sessions_dir(aid)
        if not d.exists():
            continue
        for f in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            items.append({
                "id": f.stem,
                "agent_id": aid,
                "updated_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size,
            })
            if len(items) >= limit:
                break
    return {"items": items, "total": len(items)}


@router.get("/{session_id}/messages")
async def messages(session_id: str, agent_id: str, limit: int = 200) -> dict:
    fp = _sessions_dir(agent_id) / f"{session_id}.jsonl"
    if not fp.exists():
        raise HTTPException(status_code=404)
    msgs = []
    with fp.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(msgs) >= limit:
                break
    return {"session_id": session_id, "agent_id": agent_id, "messages": msgs}
