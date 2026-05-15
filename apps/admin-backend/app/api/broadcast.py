"""管台 · 统一下发

把 Provider / Model / Skill 变更批量推给多个 Agent。
V0 只更新文件 + 审计；reload 调用 OpenClaw 的部分待后续接入 SDK 时补。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import audit

router = APIRouter(prefix="/broadcast", tags=["broadcast"])


class ProviderBroadcast(BaseModel):
    target_agents: list[str]        # 空表示所有
    provider: str | None = None     # anthropic / glm / qwen
    model: str | None = None


def _patch_agents_md(ws: Path, provider: str | None, model: str | None) -> bool:
    fp = ws / "AGENTS.md"
    if not fp.exists():
        return False
    text = fp.read_text(encoding="utf-8")
    if provider:
        text = re.sub(r"(^provider:\s*).*$", rf"\g<1>{provider}", text, count=1, flags=re.M)
    if model:
        text = re.sub(r"(^model:\s*).*$", rf"\g<1>{model}", text, count=1, flags=re.M)
    fp.write_text(text, encoding="utf-8")
    return True


@router.post("/provider")
async def broadcast_provider(body: ProviderBroadcast) -> dict:
    ws_root = settings.workspaces_root
    targets = body.target_agents or [p.name for p in ws_root.iterdir() if p.is_dir()]
    updated = []
    for aid in targets:
        if _patch_agents_md(ws_root / aid, body.provider, body.model):
            updated.append(aid)
    audit("broadcast.provider", detail={"provider": body.provider, "model": body.model, "updated": updated})
    return {"ok": True, "updated": updated}


class TtsBroadcast(BaseModel):
    target_agents: list[str]    # 通常只下发到 video-producer / copywriter
    tts_provider: str | None = None
    tts_model: str | None = None


def _patch_tts(ws: Path, provider: str | None, model: str | None) -> bool:
    fp = ws / "AGENTS.md"
    if not fp.exists():
        return False
    text = fp.read_text(encoding="utf-8")
    changed = False
    if provider:
        if re.search(r"^tts_provider:\s*", text, re.M):
            text = re.sub(r"(^tts_provider:\s*).*$", rf"\g<1>{provider}", text, count=1, flags=re.M)
        else:
            text = text.rstrip() + f"\n\n# TTS\ntts_provider: {provider}\n"
        changed = True
    if model:
        if re.search(r"^tts_model:\s*", text, re.M):
            text = re.sub(r"(^tts_model:\s*).*$", rf"\g<1>{model}", text, count=1, flags=re.M)
        else:
            text = text.rstrip() + f"\ntts_model: {model}\n"
        changed = True
    if changed:
        fp.write_text(text, encoding="utf-8")
    return changed


@router.post("/tts")
async def broadcast_tts(body: TtsBroadcast) -> dict:
    ws_root = settings.workspaces_root
    targets = body.target_agents or ["video-producer", "copywriter"]
    updated = []
    for aid in targets:
        if _patch_tts(ws_root / aid, body.tts_provider, body.tts_model):
            updated.append(aid)
    audit("broadcast.tts", detail={"provider": body.tts_provider, "model": body.tts_model, "updated": updated})
    return {"ok": True, "updated": updated}


class SkillBroadcast(BaseModel):
    skill: str
    op: str                          # mount | unmount
    target_agents: list[str]


@router.post("/skill")
async def broadcast_skill(body: SkillBroadcast) -> dict:
    if body.op not in {"mount", "unmount"}:
        raise HTTPException(status_code=400, detail={"error": {"biz_message": "op 必须是 mount 或 unmount"}})
    # 复用 skills 路由的逻辑由前端串接调用；此接口只做审计记录入口
    audit("broadcast.skill", detail=body.model_dump())
    return {"ok": True, "note": "前端请并发调用 POST /skills/install 或 DELETE /skills/{name}"}


@router.get("/audit")
async def recent_audit(limit: int = 100) -> list[dict]:
    from sqlalchemy import select
    from ..db import AuditLog, db_session
    with db_session() as s:
        rows = s.execute(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)).scalars().all()
    return [
        {
            "ts": r.ts.isoformat(),
            "actor": r.actor,
            "action": r.action,
            "target": r.target,
            "detail": json.loads(r.detail_json or "{}"),
        }
        for r in rows
    ]
