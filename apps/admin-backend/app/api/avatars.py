"""管台 · 数字人形象库

V0：CRUD 元数据；与 HeyGen / 自托管的真同步留给后续。
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import Avatar, audit, db_session

router = APIRouter(prefix="/avatars", tags=["avatars"])


class AvatarBody(BaseModel):
    name: str
    source: str = "heygen"        # heygen | self-hosted
    preview_url: str | None = None
    voice_id: str | None = None
    meta: dict = {}


@router.get("")
async def list_avatars() -> list[dict]:
    with db_session() as s:
        rows = s.execute(select(Avatar).order_by(Avatar.created_at.desc())).scalars().all()
    return [
        {
            "id": r.id,
            "source": r.source,
            "name": r.name,
            "preview_url": r.preview_url,
            "voice_id": r.voice_id,
            "meta": json.loads(r.meta_json or "{}"),
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.post("")
async def create(body: AvatarBody) -> dict:
    aid = f"avt_{uuid.uuid4().hex[:10]}"
    with db_session() as s:
        s.add(Avatar(
            id=aid, source=body.source, name=body.name,
            preview_url=body.preview_url, voice_id=body.voice_id,
            meta_json=json.dumps(body.meta, ensure_ascii=False),
        ))
    audit("avatar.create", target=aid, detail={"name": body.name})
    return {"id": aid}


@router.put("/{avatar_id}")
async def update(avatar_id: str, body: AvatarBody) -> dict:
    with db_session() as s:
        r = s.get(Avatar, avatar_id)
        if not r:
            raise HTTPException(status_code=404)
        r.name = body.name
        r.source = body.source
        r.preview_url = body.preview_url
        r.voice_id = body.voice_id
        r.meta_json = json.dumps(body.meta, ensure_ascii=False)
    audit("avatar.update", target=avatar_id, detail={"name": body.name})
    return {"ok": True}


@router.delete("/{avatar_id}")
async def delete(avatar_id: str) -> dict:
    with db_session() as s:
        r = s.get(Avatar, avatar_id)
        if not r:
            raise HTTPException(status_code=404)
        s.delete(r)
    audit("avatar.delete", target=avatar_id)
    return {"ok": True}
