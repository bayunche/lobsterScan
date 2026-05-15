"""管台 · Channel & Bindings 可视化编辑

读写 openclaw/openclaw.json 的 channels / bindings 字段。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import audit

router = APIRouter(prefix="/bindings", tags=["bindings"])
OC_JSON = settings.openclaw_json


def _read() -> dict:
    if not OC_JSON.exists():
        return {"channels": [], "bindings": [], "agents": {"list": []}}
    return json.loads(OC_JSON.read_text(encoding="utf-8"))


def _write(data: dict) -> None:
    OC_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Channel(BaseModel):
    name: str
    type: str
    listen: str | None = None


class Binding(BaseModel):
    agentId: str
    match: dict


class BindingsBody(BaseModel):
    channels: list[Channel]
    bindings: list[Binding]


@router.get("")
async def read():
    j = _read()
    return {
        "channels": j.get("channels", []),
        "bindings": j.get("bindings", []),
        "agent_ids": [a["id"] for a in j.get("agents", {}).get("list", [])],
    }


@router.put("")
async def write(body: BindingsBody):
    j = _read()
    j["channels"] = [c.model_dump() for c in body.channels]
    j["bindings"] = [b.model_dump() for b in body.bindings]
    _write(j)
    audit("bindings.write", detail={"channels": len(body.channels), "bindings": len(body.bindings)})
    return {"ok": True}


@router.post("/channels")
async def add_channel(body: Channel):
    j = _read()
    chans = j.setdefault("channels", [])
    if any(c["name"] == body.name for c in chans):
        raise HTTPException(status_code=400, detail={"error": {"biz_message": "Channel 名重复"}})
    chans.append(body.model_dump())
    _write(j)
    audit("bindings.channel_add", target=body.name)
    return {"ok": True}


@router.delete("/channels/{name}")
async def del_channel(name: str):
    j = _read()
    chans = [c for c in j.get("channels", []) if c.get("name") != name]
    if len(chans) == len(j.get("channels", [])):
        raise HTTPException(status_code=404)
    j["channels"] = chans
    # 同时删 binding 中引用了这个 channel 的条目
    j["bindings"] = [b for b in j.get("bindings", []) if b.get("match", {}).get("channel") != name]
    _write(j)
    audit("bindings.channel_delete", target=name)
    return {"ok": True}


@router.post("/items")
async def add_binding(body: Binding):
    j = _read()
    binds = j.setdefault("bindings", [])
    binds.append(body.model_dump())
    _write(j)
    audit("bindings.add", detail={"agent": body.agentId, "match": body.match})
    return {"ok": True}


@router.delete("/items/{index}")
async def del_binding(index: int):
    j = _read()
    binds = j.get("bindings", [])
    if index < 0 or index >= len(binds):
        raise HTTPException(status_code=404)
    removed = binds.pop(index)
    _write(j)
    audit("bindings.delete", detail={"removed": removed})
    return {"ok": True}
