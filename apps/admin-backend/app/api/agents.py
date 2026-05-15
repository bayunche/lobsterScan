"""管理平台 · Agent CRUD（详见 docs/API接口规范.md §3.1）"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings

router = APIRouter(prefix="/agents", tags=["agents"])

WORKSPACES = settings.workspaces_root
BACKUPS = settings.backups_root

AGENT_IDS = [
    "coordinator", "material", "point-extractor", "structure",
    "upward-opt", "copywriter", "html-designer", "video-producer", "reviewer",
]
DISPLAY_NAME = {
    "coordinator": "汇报总控", "material": "资料员", "point-extractor": "分析师",
    "structure": "结构师", "upward-opt": "表达教练", "copywriter": "文书",
    "html-designer": "设计师", "video-producer": "视频制作", "reviewer": "质量检查员",
}
WHICH_FILE = {"soul": "SOUL.md", "agents": "AGENTS.md", "user": "USER.md"}


class FileBody(BaseModel):
    content: str
    etag: str | None = None


@router.get("")
async def list_agents() -> list[dict]:
    out = []
    for aid in AGENT_IDS:
        ws = WORKSPACES / aid
        skill_dir = ws / ".agents" / "skills"
        skills = [p.name for p in skill_dir.iterdir()] if skill_dir.exists() else []
        out.append({
            "id": aid,
            "display_name": DISPLAY_NAME[aid],
            "status": "ready" if ws.exists() else "uninitialized",
            "skill_count": len(skills),
            "skills": skills,
        })
    return out


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    if agent_id not in AGENT_IDS:
        raise HTTPException(status_code=404)
    ws = WORKSPACES / agent_id
    return {
        "id": agent_id,
        "display_name": DISPLAY_NAME[agent_id],
        "workspace": str(ws),
        "files": list(WHICH_FILE.keys()),
    }


@router.get("/{agent_id}/files/{which}")
async def read_file(agent_id: str, which: str) -> dict:
    fname = WHICH_FILE.get(which)
    if not fname or agent_id not in AGENT_IDS:
        raise HTTPException(status_code=404)
    fp = WORKSPACES / agent_id / fname
    if not fp.exists():
        return {"content": "", "etag": None, "updated_at": None}
    content = fp.read_text(encoding="utf-8")
    return {
        "content": content,
        "etag": str(fp.stat().st_mtime_ns),
        "updated_at": datetime.fromtimestamp(fp.stat().st_mtime).isoformat(),
    }


@router.put("/{agent_id}/files/{which}")
async def write_file(agent_id: str, which: str, body: FileBody) -> dict:
    fname = WHICH_FILE.get(which)
    if not fname or agent_id not in AGENT_IDS:
        raise HTTPException(status_code=404)

    fp = WORKSPACES / agent_id / fname
    fp.parent.mkdir(parents=True, exist_ok=True)
    if fp.exists():
        BACKUPS.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (BACKUPS / f"{agent_id}_{fname.replace('.md', '')}_{ts}.md").write_text(
            fp.read_text(encoding="utf-8"), encoding="utf-8"
        )

    fp.write_text(body.content, encoding="utf-8")
    # TODO: 调用 OpenClaw 触发 system.reload-agent
    return {"ok": True, "etag": str(fp.stat().st_mtime_ns)}


@router.post("/{agent_id}/actions/reload")
async def reload_agent(agent_id: str) -> dict:
    # TODO: 接入 OpenClaw SDK 后改成真发 system.reload-agent
    # 当前阶段：把 reload 意图写进 audit，方便后续追踪
    from ..db import audit
    audit("agent.reload", target=agent_id)
    return {"ok": True, "note": "reload 已记录，待 OpenClaw 接入后真触发"}


@router.get("/{agent_id}/backups")
async def list_backups(agent_id: str) -> list[dict]:
    if agent_id not in AGENT_IDS:
        raise HTTPException(status_code=404)
    out: list[dict] = []
    if not BACKUPS.exists():
        return out
    prefix = f"{agent_id}_"
    # 文件名格式：<agent>_<SOUL|AGENTS|USER>_<YYYYMMDD>_<HHMMSS>.md
    import re as _re
    pat = _re.compile(rf"^{_re.escape(agent_id)}_(SOUL|AGENTS|USER)_(.+)\.md$")
    for p in sorted(BACKUPS.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        m = pat.match(p.name)
        if not m:
            continue
        out.append({
            "filename": p.name,
            "which": m.group(1).lower(),     # 与前端 which 参数一致：soul/agents/user
            "saved_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            "size_bytes": p.stat().st_size,
        })
    return out


@router.post("/{agent_id}/backups/{filename}/restore")
async def restore_backup(agent_id: str, filename: str) -> dict:
    if agent_id not in AGENT_IDS:
        raise HTTPException(status_code=404)
    src = BACKUPS / filename
    if not src.exists() or not src.name.startswith(f"{agent_id}_"):
        raise HTTPException(status_code=404)
    # 文件名形如 <agent>_SOUL_<ts>.md
    for k, fname in WHICH_FILE.items():
        stem = fname.replace(".md", "")          # SOUL / AGENTS / USER
        if src.name.startswith(f"{agent_id}_{stem}_"):
            dst = WORKSPACES / agent_id / fname
            # 当前内容再备份一份，避免恢复丢失最新版
            if dst.exists():
                BACKUPS.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                (BACKUPS / f"{agent_id}_{stem}_pre-restore-{ts}.md").write_text(
                    dst.read_text(encoding="utf-8"), encoding="utf-8"
                )
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            from ..db import audit
            audit("agent.restore", target=agent_id, detail={"from": filename, "which": k})
            return {"ok": True, "which": k}
    raise HTTPException(status_code=400, detail={"error": {"biz_message": "无法识别备份对应的 which"}})
