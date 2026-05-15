"""管台 · Secrets（API Key 集中管理）

仅暴露 key 名 + masked 预览，不返回明文。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from ..db import Secret, audit, db_session, decrypt, encrypt

router = APIRouter(prefix="/secrets", tags=["secrets"])

KNOWN_KEYS = [
    # LLM
    "ANTHROPIC_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_GROUP_ID",          # MiniMax SDK 必须；和 API_KEY 配对使用
    "OPENAI_API_KEY",
    "QWEN_API_KEY",
    "GLM_API_KEY",
    # TTS / 数字人
    "HEYGEN_API_KEY",
    "ELEVENLABS_API_KEY",
]


def _mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "*" * len(v)
    return v[:4] + "*" * (len(v) - 8) + v[-4:]


class SecretBody(BaseModel):
    value: str


@router.get("")
async def list_secrets() -> list[dict]:
    with db_session() as s:
        rows = {r.key: r for r in s.execute(select(Secret)).scalars().all()}
    out = []
    for k in KNOWN_KEYS:
        r = rows.get(k)
        out.append({
            "key": k,
            "is_set": r is not None,
            "masked": _mask(decrypt(r.value_enc)) if r else "",
            "updated_at": r.updated_at.isoformat() if r else None,
        })
    return out


@router.put("/{key}")
async def set_secret(key: str, body: SecretBody) -> dict:
    if key not in KNOWN_KEYS:
        raise HTTPException(status_code=400, detail={"error": {"biz_message": "未知 secret key"}})
    enc = encrypt(body.value)
    with db_session() as s:
        existing = s.get(Secret, key)
        if existing:
            existing.value_enc = enc
        else:
            s.add(Secret(key=key, value_enc=enc))
    audit("secret.set", target=key)
    return {"ok": True}


@router.delete("/{key}")
async def delete_secret(key: str) -> dict:
    with db_session() as s:
        existing = s.get(Secret, key)
        if existing:
            s.delete(existing)
    audit("secret.delete", target=key)
    return {"ok": True}


@router.get("/internal/values")
async def internal_values(request: Request) -> dict:
    """业务后端拉取明文 secrets。仅允许 127.0.0.1 / ::1 访问。"""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="local only")
    with db_session() as s:
        rows = s.execute(select(Secret)).scalars().all()
    return {r.key: decrypt(r.value_enc) for r in rows}
