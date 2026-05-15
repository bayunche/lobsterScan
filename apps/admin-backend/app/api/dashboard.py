"""管台 · Dashboard 聚合接口

把多个数据源拼成一个驾驶舱视图，避免前端打多个请求。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from ..config import settings
from ..db import AuditLog, Secret, TokenUsage, db_session, decrypt

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

AGENT_IDS = [
    "coordinator", "material", "point-extractor", "structure",
    "upward-opt", "copywriter", "html-designer", "video-producer", "reviewer",
]
DISPLAY = {
    "coordinator": "汇报总控", "material": "资料员", "point-extractor": "分析师",
    "structure": "结构师", "upward-opt": "表达教练", "copywriter": "文书",
    "html-designer": "设计师", "video-producer": "视频制作", "reviewer": "质量检查员",
}

# 哪些 secret 是 LLM provider 至少要选一个，哪些是 TTS / 视频可选
LLM_KEYS = ["ANTHROPIC_API_KEY", "MINIMAX_API_KEY", "QWEN_API_KEY", "GLM_API_KEY", "OPENAI_API_KEY"]
VIDEO_KEYS = ["HEYGEN_API_KEY", "MINIMAX_API_KEY", "ELEVENLABS_API_KEY"]


def _gateway_online() -> tuple[bool, int]:
    """快速探测 Gateway，避免直接依赖 health 路由（避免循环）."""
    import socket
    for p in (
        _config_port(),
        7800, 18789, 18888, 8788,
    ):
        try:
            with socket.create_connection(("127.0.0.1", p), timeout=0.5):
                return True, p
        except OSError:
            continue
    return False, _config_port()


def _config_port() -> int:
    try:
        return int(json.loads(settings.openclaw_json.read_text(encoding="utf-8"))
                   .get("gateway", {}).get("port", 7800))
    except Exception:
        return 7800


def _agent_status() -> list[dict]:
    out = []
    ws_root = settings.workspaces_root
    for aid in AGENT_IDS:
        ws = ws_root / aid
        skill_dir = ws / ".agents" / "skills"
        skills = sorted(p.name for p in skill_dir.iterdir()) if skill_dir.exists() else []
        out.append({
            "id": aid,
            "display_name": DISPLAY[aid],
            "workspace_ready": (ws / "SOUL.md").exists() and (ws / "AGENTS.md").exists(),
            "skill_count": len(skills),
            "agent_dir_exists": (settings.home_openclaw / "agents" / aid).exists(),
        })
    return out


def _config_snapshot() -> dict:
    try:
        j = json.loads(settings.openclaw_json.read_text(encoding="utf-8"))
    except Exception:
        j = {}
    p = j.get("providers", {})
    default = p.get("default", "anthropic")
    return {
        "llm_provider": default,
        "llm_model": p.get(default, {}).get("model") if isinstance(p.get(default), dict) else None,
        "tts_provider": j.get("tts", {}).get("provider"),
        "tts_model": j.get("tts", {}).get("model"),
        "video_provider": j.get("video", {}).get("provider"),
    }


def _secrets_status() -> dict:
    with db_session() as s:
        rows = {r.key: r for r in s.execute(select(Secret)).scalars().all()}
    llm_ready = any(k in rows for k in LLM_KEYS)
    video_ready = any(k in rows for k in VIDEO_KEYS)
    return {
        "llm_ready": llm_ready,
        "video_ready": video_ready,
        "set": [k for k in rows],
        "llm_options_set": [k for k in LLM_KEYS if k in rows],
        "video_options_set": [k for k in VIDEO_KEYS if k in rows],
    }


def _readiness(agents: list[dict], gw_online: bool, secrets: dict, snapshot: dict) -> dict:
    items = [
        {"key": "openclaw_cli", "label": "OpenClaw CLI", "ok": _cli_installed(), "hint": "npm i -g openclaw@latest"},
        {"key": "gateway", "label": "Gateway 在线", "ok": gw_online, "hint": "openclaw gateway"},
        {"key": "workspaces", "label": "9 个 Agent workspace 就绪", "ok": all(a["workspace_ready"] for a in agents),
         "hint": "bash scripts/bootstrap-openclaw.sh apply-prompts"},
        {"key": "agent_dirs", "label": "agentDir 已创建", "ok": all(a["agent_dir_exists"] for a in agents),
         "hint": "bash scripts/bootstrap-openclaw.sh"},
        {"key": "llm_key", "label": "至少一个 LLM Key", "ok": secrets["llm_ready"], "hint": "Secrets 页填入 ANTHROPIC / MINIMAX 等"},
        {"key": "video_key", "label": "至少一个视频/TTS Key", "ok": secrets["video_ready"], "hint": "Secrets 页填入 HEYGEN / MINIMAX / ELEVENLABS"},
        {"key": "skills_mounted", "label": "Agent 已挂载 Skill", "ok": sum(a["skill_count"] for a in agents) > 0,
         "hint": "Skill 市场 → 挂载到对应 Agent"},
        {"key": "llm_config", "label": f"LLM Provider 已选 ({snapshot.get('llm_provider')})",
         "ok": bool(snapshot.get("llm_provider")), "hint": "Provider/TTS 页"},
        {"key": "tts_config", "label": f"TTS Provider 已选 ({snapshot.get('tts_provider') or '—'})",
         "ok": bool(snapshot.get("tts_provider")), "hint": "Provider/TTS 页"},
    ]
    done = sum(1 for x in items if x["ok"])
    return {"items": items, "done": done, "total": len(items),
            "score": round(done / len(items) * 100)}


def _cli_installed() -> bool:
    import shutil
    return shutil.which("openclaw") is not None


def _recent_pipelines() -> list[dict]:
    from . import pipelines as p
    runs = list(p._runs.values())                                                  # noqa: SLF001
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    out = []
    for r in runs[:5]:
        done = sum(1 for s in r["steps"] if s["status"] == "success")
        total = len(r["steps"])
        out.append({
            "task_id": r["task_id"], "title": r.get("title", "—"),
            "status": r["status"], "step_done": done, "step_total": total,
            "steps_status": [s["status"] for s in r["steps"]],
        })
    return out


def _token_summary(days: int = 7) -> dict:
    since = datetime.utcnow() - timedelta(days=days)
    with db_session() as s:
        total_p = s.execute(select(func.coalesce(func.sum(TokenUsage.prompt_tokens), 0)).where(TokenUsage.ts >= since)).scalar_one()
        total_c = s.execute(select(func.coalesce(func.sum(TokenUsage.completion_tokens), 0)).where(TokenUsage.ts >= since)).scalar_one()
        cost    = s.execute(select(func.coalesce(func.sum(TokenUsage.cost_usd_micros), 0)).where(TokenUsage.ts >= since)).scalar_one()
        # 按天分桶
        rows = s.execute(
            select(func.date(TokenUsage.ts), func.sum(TokenUsage.prompt_tokens + TokenUsage.completion_tokens))
            .where(TokenUsage.ts >= since)
            .group_by(func.date(TokenUsage.ts))
            .order_by(func.date(TokenUsage.ts))
        ).all()
    return {
        "days": days,
        "tokens": int(total_p or 0) + int(total_c or 0),
        "cost_usd": (cost or 0) / 1_000_000,
        "daily": [{"date": str(d), "tokens": int(t or 0)} for d, t in rows],
    }


def _recent_audit(limit: int = 6) -> list[dict]:
    with db_session() as s:
        rows = s.execute(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)).scalars().all()
    return [
        {
            "ts": r.ts.isoformat(), "action": r.action,
            "target": r.target, "detail": json.loads(r.detail_json or "{}"),
        }
        for r in rows
    ]


def _storage() -> dict:
    out_dir = settings.project_root / "data" / "outputs"
    up_dir = settings.project_root / "data" / "uploads"

    def size(p):
        if not p.exists():
            return 0
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    task_count = sum(1 for _ in out_dir.iterdir()) if out_dir.exists() else 0
    return {
        "outputs_bytes": size(out_dir),
        "uploads_bytes": size(up_dir),
        "task_count": task_count,
    }


@router.get("")
async def overview() -> dict:
    agents = _agent_status()
    gw_online, gw_port = _gateway_online()
    snap = _config_snapshot()
    secrets = _secrets_status()
    return {
        "gateway": {"online": gw_online, "port": gw_port},
        "cli_installed": _cli_installed(),
        "agents": agents,
        "config": snap,
        "secrets": secrets,
        "readiness": _readiness(agents, gw_online, secrets, snap),
        "tokens": _token_summary(days=7),
        "pipelines_recent": _recent_pipelines(),
        "audit_recent": _recent_audit(),
        "storage": _storage(),
    }
